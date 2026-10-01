"""Durable polling worker: run separately with `make worker`.

Each claim/checkpoint commits before provider I/O. Jobs are leased across processes;
upload session capabilities are encrypted. No raw provider errors are logged.
"""

import signal
import time

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
from .db_models import PublishBatch, PublishJob, SocialAccount
from .http import ProviderHTTP
from .thumbnail import cover_image
from .video import inspect_video


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
                    PublishJob.status.in_(["queued", "uploading", "processing", "publishing"]),
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

    def run_once(self):
        job_id = self.claim()
        if not job_id:
            return False
        cp = {}
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
                if self.now() - job.created_at > 24 * 3600_000:
                    raise ProviderError("PUBLISH_UNCERTAIN")
                # Processing/post creation can finish even after the local media expires.
                path = None
                if (
                    job.status in {"queued", "uploading"}
                    and not cp.get("video_id")
                    and not cp.get("transfer_complete")
                    and not (job.platform == "x" and cp.get("finalized"))
                ):
                    _, path = owned_render(
                        db, job.user_id, job.render_id, self.now(), True, self.settings.media_dir
                    )
            adapter = self.accounts.adapter(values["platform"])
            token = self.accounts.token(
                values["social_account_id"], values["user_id"], values["platform"]
            )
            if cp.get("post_request_started"):
                raise ProviderError("PUBLISH_UNCERTAIN")
            if values["status"] == "publishing" and values["platform"] == "x":
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
            elif (
                values["status"] == "processing"
                or cp.get("video_id")
                or cp.get("transfer_complete")
                or (values["platform"] == "x" and cp.get("finalized"))
            ):
                step = adapter.get_publish_status(token, cp)
            else:
                if path is None:
                    raise ProviderError("ASSET_EXPIRED")
                if not cp.get("video_info"):
                    cp["video_info"] = inspect_video(path, self.settings.ffmpeg_binary)
                    self.save_checkpoint(job_id, cp)
                if values["platform"] != "tiktok":
                    adapter.validate_video(cp["video_info"], values["post_metadata"])
                step = adapter.publish(token, path, cp["video_info"], values["post_metadata"], cp)
            if (
                step.status == "published"
                and values["platform"] == "youtube"
                and values["post_metadata"].get("use_thumbnail")
            ):
                # Replacing the cover for a known video ID cannot create another post.
                try:
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
                    step.notice = "Your video was published, but its custom cover could not be applied. You can change it in YouTube Studio."
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
        except (ProviderError, ApiError) as error:
            if isinstance(error, ApiError):
                error = ProviderError(error.code)
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
