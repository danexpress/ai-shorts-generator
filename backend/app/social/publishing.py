"""Admission, ownership, idempotency, history and retries. No provider uploads in HTTP."""

import hashlib
import json

from sqlalchemy import select

from ..db_models import ProjectRecord, RenderRecord
from ..errors import ApiError
from ..store import oid
from .accounts import event, rate_limit, safe_error
from .adapters.base import ProviderError
from .copy import owned_render
from .db_models import PublishBatch, PublishJob, SocialAccount
from .video import inspect_video

DAY = 86_400_000
MIN_SCHEDULE_LEAD = 60_000  # at least a minute ahead
MAX_SCHEDULE_AHEAD = 30 * DAY


class PublishingService:
    def __init__(self, database, app_settings, accounts, clock):
        self.database, self.app_settings, self.accounts, self.clock = (
            database,
            app_settings,
            accounts,
            clock,
        )

    def now(self):
        return int(self.clock() * 1000)

    def schedule_time(self, when) -> int | None:
        """Validated epoch ms for a scheduled post, or None to post now."""
        if when is None:
            return None
        at = int(when.timestamp() * 1000)
        if at < self.now() + MIN_SCHEDULE_LEAD:
            raise ApiError("SCHEDULE_IN_PAST", "Choose a time at least a minute from now.", 422)
        if at > self.now() + MAX_SCHEDULE_AHEAD:
            raise ApiError("SCHEDULE_TOO_FAR", "Schedule posts up to 30 days ahead.", 422)
        return at

    def keep_render(self, db, render_id, scheduled_at):
        # Renders normally expire after 7 days; keep this one until a day after it posts.
        render = db.get(RenderRecord, render_id)
        if render and scheduled_at:
            render.expiresAt = max(render.expiresAt, scheduled_at + DAY)

    def same_account(self, db, job):
        return db.scalar(
            select(SocialAccount).where(
                SocialAccount.user_id == job.user_id,
                SocialAccount.platform == job.platform,
                SocialAccount.platform_user_id == job.platform_user_id,
            )
        )

    def job_view(self, db, job):
        fields = (
            "id",
            "platform",
            "social_account_id",
            "status",
            "attempt_count",
            "platform_post_id",
            "platform_post_url",
            "error_code",
            "error_message_safe",
            "published_at",
            "scheduled_at",
            "created_at",
            "updated_at",
        )
        result = {key: getattr(job, key) for key in fields}
        account = self.same_account(db, job)
        result["account_name"] = job.post_metadata.get("_account_name") or (
            account.account_name if account else None
        )
        result["retryable"] = (
            job.retryable and job.attempt_count < self.accounts.settings.max_attempts
        )
        if (
            job.error_code in {"RECONNECT_REQUIRED", "PERMISSION_REQUIRED", "ACCOUNT_DISCONNECTED"}
            and account
            and account.status == "connected"
            and account.updated_at > job.updated_at
        ):
            result["retryable"] = job.attempt_count < self.accounts.settings.max_attempts
        return result

    def batch_view(self, db, batch):
        jobs = db.scalars(
            select(PublishJob)
            .where(PublishJob.publish_batch_id == batch.id)
            .order_by(PublishJob.created_at, PublishJob.platform)
        )
        return {
            "id": batch.id,
            "render_id": batch.render_id,
            "created_at": batch.created_at,
            "scheduled_at": batch.scheduled_at,
            "jobs": [self.job_view(db, j) for j in jobs],
        }

    def get(self, user_id, batch_id):
        with self.database.transaction() as db:
            batch = db.get(PublishBatch, batch_id)
            if not batch or batch.user_id != user_id:
                raise ApiError("NOT_FOUND", "Publishing batch not found.", 404)
            project = db.get(ProjectRecord, batch.project_id)
            if not project or project.status == "deleted":
                raise ApiError("NOT_FOUND", "Project not found.", 404)
            return self.batch_view(db, batch)

    def history(self, user_id, render_id):
        with self.database.transaction() as db:
            batches = list(
                db.scalars(
                    select(PublishBatch)
                    .where(PublishBatch.user_id == user_id, PublishBatch.render_id == render_id)
                    .order_by(PublishBatch.created_at.desc())
                )
            )
            if not batches:
                owned_render(db, user_id, render_id, self.now())
            else:
                project = db.get(ProjectRecord, batches[0].project_id)
                if not project or project.status == "deleted":
                    raise ApiError("NOT_FOUND", "Project not found.", 404)
            # A batch whose scheduled posts were all canceled has no history to show.
            views = [self.batch_view(db, b) for b in batches]
            return [view for view in views if view["jobs"]]

    def start(self, user_id, render_id, body, key):
        if not key or not 1 <= len(key) <= 128:
            raise ApiError("INVALID_INPUT", "A publishing request identifier is required.", 422)
        payload = body.model_dump(mode="json")
        payload_hash = hashlib.sha256(
            json.dumps({"render": render_id, **payload}, sort_keys=True).encode()
        ).hexdigest()
        with self.database.transaction() as db:
            previous = db.scalar(
                select(PublishBatch).where(
                    PublishBatch.user_id == user_id, PublishBatch.idempotency_key == key
                )
            )
            if previous:
                project = db.get(ProjectRecord, previous.project_id)
                if not project or project.status == "deleted":
                    raise ApiError("NOT_FOUND", "Project not found.", 404)
                if previous.payload_hash != payload_hash:
                    raise ApiError(
                        "IDEMPOTENCY_CONFLICT",
                        "This request changed. Review the copy and submit it again.",
                        409,
                    )
                return self.batch_view(db, previous)
            scheduled_at = self.schedule_time(body.scheduled_at)
            record, path = owned_render(
                db, user_id, render_id, self.now(), True, self.app_settings.media_dir
            )
            project_id = record.projectId
            account_ids = {}
            for target in body.targets:
                account = db.get(SocialAccount, target.social_account_id)
                if not account or account.user_id != user_id or account.platform != target.platform:
                    raise ApiError("NOT_FOUND", "Connected account not found.", 404)
                existing = db.scalar(
                    select(PublishJob.id).where(
                        PublishJob.user_id == user_id,
                        PublishJob.render_id == render_id,
                        PublishJob.platform == target.platform,
                        PublishJob.platform_user_id == account.platform_user_id,
                    )
                )
                if existing:
                    raise ApiError(
                        "ALREADY_PUBLISHED",
                        "This Short already has a publishing job for a selected account. Check Publishing History or retry its failed job.",
                        409,
                    )
                account_ids[target.platform] = (
                    account.id,
                    account.platform_user_id,
                    account.status,
                )
            rate_limit(db, user_id, "publish", self.now(), self.accounts.settings.rate_limit)
        # Probe outside transactions. Validate the real file, never request metadata.
        try:
            info = inspect_video(path, self.app_settings.ffmpeg_binary)
        except ProviderError as error:
            raise ApiError(error.code, safe_error(error), 422) from None
        errors = {}
        for target in body.targets:
            metadata = getattr(target, target.platform).model_dump()
            try:
                adapter = self.accounts.adapter(target.platform)
                if account_ids[target.platform][2] != "connected":
                    raise ProviderError("RECONNECT_REQUIRED")
                creator = (
                    adapter.creator_info(
                        self.accounts.token(target.social_account_id, user_id, target.platform)
                    )
                    if adapter.needs_creator_info
                    else None
                )
                adapter.validate(info, metadata, creator)
            except ApiError:
                errors[target.platform] = ProviderError("PERMISSION_REQUIRED")
            except ProviderError as error:
                errors[target.platform] = error
        with self.database.transaction() as db:
            # Recheck after I/O; simultaneous clicks/reconnect/disconnect cannot race.
            previous = db.scalar(
                select(PublishBatch).where(
                    PublishBatch.user_id == user_id, PublishBatch.idempotency_key == key
                )
            )
            if previous:
                project = db.get(ProjectRecord, previous.project_id)
                if not project or project.status == "deleted":
                    raise ApiError("NOT_FOUND", "Project not found.", 404)
                if previous.payload_hash != payload_hash:
                    raise ApiError("IDEMPOTENCY_CONFLICT", "This request changed.", 409)
                return self.batch_view(db, previous)
            owned_render(db, user_id, render_id, self.now(), True, self.app_settings.media_dir)
            for target in body.targets:
                account = db.get(SocialAccount, target.social_account_id)
                if (
                    not account
                    or account.user_id != user_id
                    or account.platform_user_id != account_ids[target.platform][1]
                ):
                    raise ApiError(
                        "NOT_FOUND", "The selected connection changed. Review your accounts.", 404
                    )
                if db.scalar(
                    select(PublishJob.id).where(
                        PublishJob.user_id == user_id,
                        PublishJob.render_id == render_id,
                        PublishJob.platform == target.platform,
                        PublishJob.platform_user_id == account.platform_user_id,
                    )
                ):
                    raise ApiError(
                        "ALREADY_PUBLISHED",
                        "This Short already has a publishing job. Check Publishing History.",
                        409,
                    )
            batch = PublishBatch(
                id=oid("pub"),
                user_id=user_id,
                render_id=render_id,
                project_id=project_id,
                idempotency_key=key,
                payload_hash=payload_hash,
                created_at=self.now(),
                scheduled_at=scheduled_at,
            )
            db.add(batch)
            db.flush()
            self.keep_render(db, render_id, scheduled_at)
            for target in body.targets:
                failure = errors.get(target.platform)
                job = PublishJob(
                    id=oid("pjob"),
                    publish_batch_id=batch.id,
                    user_id=user_id,
                    render_id=render_id,
                    platform=target.platform,
                    platform_user_id=account_ids[target.platform][1],
                    social_account_id=target.social_account_id,
                    status="failed" if failure else ("scheduled" if scheduled_at else "queued"),
                    attempt_count=0,
                    post_metadata={
                        **getattr(target, target.platform).model_dump(),
                        "_account_name": db.get(
                            SocialAccount, target.social_account_id
                        ).account_name,
                    },
                    error_code=failure.code if failure else None,
                    error_message_safe=safe_error(failure) if failure else None,
                    retryable=failure.retryable if failure else False,
                    next_attempt_at=scheduled_at or self.now(),
                    scheduled_at=scheduled_at,
                    created_at=self.now(),
                    updated_at=self.now(),
                )
                db.add(job)
                if failure:
                    event(
                        db, user_id, "platform_publish_failed", self.now(), target.platform, job.id
                    )
            db.flush()
            event(
                db,
                user_id,
                "publish_scheduled" if scheduled_at else "publish_started",
                self.now(),
                resource=batch.id,
            )
            return self.batch_view(db, batch)

    def scheduled_jobs(self, db, user_id, batch_id):
        """The batch and its jobs that are still waiting for their time (never started)."""
        batch = db.get(PublishBatch, batch_id)
        if not batch or batch.user_id != user_id:
            raise ApiError("NOT_FOUND", "Publishing batch not found.", 404)
        project = db.get(ProjectRecord, batch.project_id)
        if not project or project.status == "deleted":
            raise ApiError("NOT_FOUND", "Project not found.", 404)
        now = self.now()
        jobs = [
            job
            for job in db.scalars(select(PublishJob).where(PublishJob.publish_batch_id == batch.id))
            if job.status == "scheduled" and not (job.lease_until and job.lease_until > now)
        ]
        if not jobs:
            raise ApiError(
                "NOT_SCHEDULED",
                "These posts have already started publishing or were canceled.",
                409,
            )
        return batch, jobs

    def reschedule(self, user_id, batch_id, when):
        scheduled_at = self.schedule_time(when)
        with self.database.transaction() as db:
            batch, jobs = self.scheduled_jobs(db, user_id, batch_id)
            for job in jobs:
                job.scheduled_at = job.next_attempt_at = scheduled_at
                job.updated_at = self.now()
            batch.scheduled_at = scheduled_at
            self.keep_render(db, batch.render_id, scheduled_at)
            event(db, user_id, "publish_rescheduled", self.now(), resource=batch.id)
            return self.batch_view(db, batch)

    def cancel(self, user_id, batch_id):
        """Cancel posts that have not started. Nothing was sent to any platform, so the
        jobs are removed and the Short can be published to those accounts again."""
        with self.database.transaction() as db:
            batch, jobs = self.scheduled_jobs(db, user_id, batch_id)
            for job in jobs:
                event(db, user_id, "publish_canceled", self.now(), job.platform, job.id)
                db.delete(job)
            db.flush()
            return self.batch_view(db, batch)

    def retry(self, user_id, job_id):
        with self.database.transaction() as db:
            job = db.get(PublishJob, job_id)
            if not job or job.user_id != user_id:
                raise ApiError("NOT_FOUND", "Publishing job not found.", 404)
            if job.status != "failed" or not self.job_view(db, job)["retryable"]:
                raise ApiError(
                    "INVALID_STATE",
                    "This job cannot be safely retried. Check the account and publishing history.",
                    409,
                )
            owned_render(db, user_id, job.render_id, self.now(), True, self.app_settings.media_dir)
            account = self.same_account(db, job)
            if (
                not account
                or account.user_id != user_id
                or account.platform_user_id != job.platform_user_id
            ):
                raise ApiError(
                    "RECONNECT_REQUIRED", "Reconnect the same account before retrying.", 409
                )
            job.social_account_id = account.id
            rate_limit(db, user_id, "retry", self.now(), self.accounts.settings.rate_limit)
            job.status, job.error_code, job.error_message_safe = "queued", None, None
            job.next_attempt_at, job.updated_at = self.now(), self.now()
            event(db, user_id, "retry_initiated", self.now(), job.platform, job.id)
            return self.job_view(db, job)
