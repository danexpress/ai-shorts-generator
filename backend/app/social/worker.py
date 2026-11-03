"""Durable polling worker: run separately with `make worker`.

Each claim/checkpoint commits before provider I/O. Jobs are leased across processes;
upload session capabilities are encrypted. No raw provider errors are logged.
"""

import logging
import signal
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select

from ..config import Settings
from ..database import Database
from ..db_models import ProjectRecord, UserRecord
from ..errors import ApiError
from ..store import oid
from .accounts import AccountService, event, safe_error
from .adapters import publishers
from .adapters.base import ProviderError
from .config import SocialSettings
from .copy import owned_render
from .db_models import ProviderQuotaUsage, PublishBatch, PublishJob, SocialAccount
from .http import ProviderHTTP
from .thumbnail import cover_image
from .video import inspect_video

logger = logging.getLogger(__name__)
QUOTA_NOTICE = (
    "This app reached the platform's daily upload limit. Your post is queued and continues "
    "automatically after the limit resets."
)


class QuotaWait(Exception):
    """A daily quota bucket is full; hold the job until `reset_ms`."""

    def __init__(self, reset_ms):
        super().__init__(reset_ms)
        self.reset_ms = reset_ms


class PublishWorker:
    def __init__(self, database, settings, accounts, clock=time.time):
        self.database, self.settings, self.accounts, self.clock = (
            database,
            settings,
            accounts,
            clock,
        )
        self.owner = oid("worker")

    def now(self):
        return int(self.clock() * 1000)

    def claim(self):
        now = self.now()
        with self.database.transaction() as db:
            jobs = db.scalars(
                select(PublishJob)
                .where(
                    PublishJob.status.in_(
                        ["scheduled", "queued", "uploading", "processing", "publishing"]
                    ),
                    PublishJob.next_attempt_at <= now,
                    or_(PublishJob.lease_until.is_(None), PublishJob.lease_until <= now),
                )
                .order_by(PublishJob.next_attempt_at, PublishJob.created_at)
            )
            for job in jobs:
                # Serialize jobs for one connection (especially rotating refresh tokens).
                busy = (
                    db.scalar(
                        select(PublishJob.id).where(
                            PublishJob.social_account_id == job.social_account_id,
                            PublishJob.id != job.id,
                            PublishJob.lease_until > now,
                        )
                    )
                    if job.social_account_id
                    else None
                )
                if busy:
                    continue
                job.lease_owner, job.lease_until = (
                    self.owner,
                    now + self.accounts.settings.lease_seconds * 1000,
                )
                if job.status == "scheduled":  # its time has come: publish as usual
                    job.status = "queued"
                if job.status == "queued":
                    job.attempt_count += 1
                job.updated_at = now
                return job.id
        return None

    def save_checkpoint(self, job_id, cp):
        with self.database.transaction() as db:
            job = db.get(PublishJob, job_id)
            if not job or job.lease_owner != self.owner or job.lease_until <= self.now():
                raise ProviderError("PUBLISH_UNCERTAIN")
            account = (
                db.get(SocialAccount, job.social_account_id) if job.social_account_id else None
            )
            if not account or account.platform_user_id != job.platform_user_id:
                raise ProviderError("ACCOUNT_DISCONNECTED")
            job.checkpoint_encrypted = self.accounts.cipher.encrypt(cp, job.user_id, job.platform)
            job.lease_until = self.now() + self.accounts.settings.lease_seconds * 1000

    def quota_day(self, adapter):
        """(day key, next reset in epoch ms) in the adapter's quota timezone."""
        try:
            zone = ZoneInfo(adapter.quota_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("UTC")
        local = datetime.fromtimestamp(self.now() / 1000, zone)
        reset = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
        return local.date().isoformat(), int(reset.timestamp() * 1000)

    def spend(self, adapter, action, cp):
        """Reserve the call's quota before making it, or raise QuotaWait."""
        limits = adapter.quota_limits()
        costs = {b: c for b, c in adapter.quota_costs(action, cp).items() if c and b in limits}
        if not costs:
            return
        day, reset = self.quota_day(adapter)
        with self.database.transaction() as db:
            rows = {}
            for bucket in costs:
                row = db.get(ProviderQuotaUsage, (bucket, day))
                if row is None:
                    row = ProviderQuotaUsage(bucket=bucket, day=day, units=0)
                    db.add(row)
                rows[bucket] = row
            if any(rows[b].units + cost > limits[b] for b, cost in costs.items()):
                raise QuotaWait(reset)
            for bucket, cost in costs.items():
                rows[bucket].units += cost

    def exhaust(self, adapter, action, cp):
        """The platform says a quota is spent (e.g. shared with another app): mark it full."""
        limits = adapter.quota_limits()
        buckets = [b for b in adapter.quota_costs(action, cp) if b in limits] or list(limits)
        day, reset = self.quota_day(adapter)
        with self.database.transaction() as db:
            for bucket in buckets:
                row = db.get(ProviderQuotaUsage, (bucket, day))
                if row is None:
                    db.add(ProviderQuotaUsage(bucket=bucket, day=day, units=limits[bucket]))
                else:
                    row.units = max(row.units, limits[bucket])
        return reset

    def hold_for_quota(self, job_id, reset_ms):
        """Requeue for just after the reset without using up a retry attempt."""
        with self.database.transaction() as db:
            job = db.get(PublishJob, job_id)
            if not job or job.lease_owner != self.owner:
                return
            if job.status == "queued":
                job.attempt_count = max(0, job.attempt_count - 1)  # claim counted it
            job.next_attempt_at = reset_ms + 60_000
            job.error_code, job.error_message_safe = None, QUOTA_NOTICE
            job.lease_owner = job.lease_until = None
            job.updated_at = self.now()
        logger.info("Publish job %s held for quota until %s", job_id, reset_ms)

    def run_once(self):
        job_id = self.claim()
        if not job_id:
            return False
        cp, adapter, action = {}, None, None
        try:
            with self.database.transaction() as db:
                job = db.get(PublishJob, job_id)
                values = job.as_dict()
                batch = db.get(PublishBatch, job.publish_batch_id)
                project = db.get(ProjectRecord, batch.project_id)
                user = db.get(UserRecord, job.user_id)
                account = (
                    db.get(SocialAccount, job.social_account_id) if job.social_account_id else None
                )
                if (
                    not user
                    or not user.active
                    or not user.invited
                    or not project
                    or project.status == "deleted"
                ):
                    raise ProviderError("ACCOUNT_DISCONNECTED")
                if not account or account.platform_user_id != job.platform_user_id:
                    raise ProviderError("ACCOUNT_DISCONNECTED")
                cp = (
                    self.accounts.cipher.decrypt(
                        job.checkpoint_encrypted, job.user_id, job.platform
                    )
                    if job.checkpoint_encrypted
                    else {}
                )
                # Measured from when publishing was due to start, not when it was requested.
                if self.now() - (job.scheduled_at or job.created_at) > 24 * 3600_000:
                    raise ProviderError("PUBLISH_UNCERTAIN")
                # Processing/post creation can finish even after the local media expires.
                adapter = self.accounts.adapter(job.platform)
                path = None
                if job.status in {"queued", "uploading"} and not adapter.upload_complete(cp):
                    _, path = owned_render(
                        db, job.user_id, job.render_id, self.now(), True, self.settings.media_dir
                    )
            token = self.accounts.token(
                values["social_account_id"], values["user_id"], values["platform"]
            )
            if cp.get("post_request_started"):
                raise ProviderError("PUBLISH_UNCERTAIN")
            if values["status"] == "publishing":
                action = "post"
                self.spend(adapter, action, cp)
                cp["post_request_started"] = True
                self.save_checkpoint(job_id, cp)
                try:
                    step = adapter.create_post(token, values["post_metadata"], cp)
                except ProviderError as error:
                    if not error.uncertain:
                        cp.pop("post_request_started", None)
                        self.save_checkpoint(job_id, cp)
                    else:
                        raise ProviderError("PUBLISH_UNCERTAIN") from None
                    raise
            elif values["status"] == "processing" or adapter.upload_complete(cp):
                action = "status"
                self.spend(adapter, action, cp)
                step = adapter.get_status(token, cp)
            else:
                if path is None:
                    raise ProviderError("ASSET_EXPIRED")
                if not cp.get("video_info"):
                    cp["video_info"] = inspect_video(path, self.settings.ffmpeg_binary)
                    self.save_checkpoint(job_id, cp)
                if not adapter.needs_creator_info:  # those were validated at admission
                    adapter.validate(cp["video_info"], values["post_metadata"])
                action = "publish"
                self.spend(adapter, action, cp)
                step = adapter.publish(token, path, cp["video_info"], values["post_metadata"], cp)
            if (
                step.status == "published"
                and adapter.supports_thumbnail
                and values["post_metadata"].get("use_thumbnail")
            ):
                # Replacing the cover for a known video ID cannot create another post.
                try:
                    self.spend(adapter, "thumbnail", cp)
                    with self.database.transaction() as db:
                        _, cover_path = owned_render(
                            db,
                            values["user_id"],
                            values["render_id"],
                            self.now(),
                            True,
                            self.settings.media_dir,
                        )
                    adapter.set_thumbnail(
                        token, step.post_id, cover_image(cover_path, self.settings.ffmpeg_binary)
                    )
                except Exception:  # noqa: BLE001 - optional cover must not repeat a successful video upload
                    step.notice = "Your video was published, but its custom cover could not be applied. You can change it on the platform."
            with self.database.transaction() as db:
                job = db.get(PublishJob, job_id)
                if job.lease_owner != self.owner:
                    return True
                job.status, job.platform_post_id, job.platform_post_url = (
                    step.status,
                    step.post_id,
                    step.post_url,
                )
                checkpoint = {**cp, **step.checkpoint}
                job.checkpoint_encrypted = self.accounts.cipher.encrypt(
                    checkpoint, job.user_id, job.platform
                )
                job.error_code, job.error_message_safe = None, step.notice
                job.next_attempt_at, job.updated_at = self.now() + step.delay * 1000, self.now()
                job.lease_owner, job.lease_until = None, None
                if step.status == "published":
                    job.checkpoint_encrypted = None
                    job.published_at = self.now()
                    job.retryable = False
                    event(
                        db,
                        job.user_id,
                        "platform_publish_succeeded",
                        self.now(),
                        job.platform,
                        job.id,
                    )
        except QuotaWait as wait:
            self.hold_for_quota(job_id, wait.reset_ms)
        except (ProviderError, ApiError) as error:
            if isinstance(error, ApiError):
                error = ProviderError(error.code)
            if error.code == "QUOTA_EXCEEDED" and adapter is not None:
                self.hold_for_quota(job_id, self.exhaust(adapter, action, cp))
            else:
                self.fail(job_id, error)
        except Exception:  # noqa: BLE001 - contain adapter bugs without leaking secrets or repeating posts
            # An unexpected adapter/schema bug is never a reason to duplicate a post.
            self.fail(
                job_id,
                ProviderError(
                    "PUBLISH_UNCERTAIN"
                    if cp.get("post_request_started")
                    else "PROVIDER_RESPONSE_INVALID"
                ),
            )
        return True

    def fail(self, job_id, error):
        with self.database.transaction() as db:
            job = db.get(PublishJob, job_id)
            if not job or job.lease_owner != self.owner:
                return
            automatic = error.retryable and job.attempt_count < self.accounts.settings.max_attempts
            job.status = "queued" if automatic else "failed"
            job.retryable = (
                error.retryable and job.attempt_count < self.accounts.settings.max_attempts
            )
            job.error_code, job.error_message_safe = error.code, safe_error(error)
            job.next_attempt_at = (
                self.now() + min(900, 15 * 2 ** max(0, job.attempt_count - 1)) * 1000
            )
            job.updated_at = self.now()
            job.lease_owner = None
            job.lease_until = None
            if error.code == "RECONNECT_REQUIRED" and job.social_account_id:
                account = db.get(SocialAccount, job.social_account_id)
                if account:
                    account.status = "reconnect_required"
            if not automatic:
                event(db, job.user_id, "platform_publish_failed", self.now(), job.platform, job.id)


def main():
    settings, social = Settings(), SocialSettings()
    database = Database(settings.database_url)
    database.initialize()
    http = ProviderHTTP()
    accounts = AccountService(database, social, time.time, publishers(social, http))
    worker = PublishWorker(database, settings, accounts)
    running = True

    def stop(*args):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            if not worker.run_once():
                time.sleep(1)
    finally:
        http.close()
        database.dispose()


if __name__ == "__main__":
    main()
