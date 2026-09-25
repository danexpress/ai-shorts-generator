"""Request-scoped SQLAlchemy repository and clock-driven processing simulator.

Database records, not process dictionaries, are the source of truth. The caller
owns the session transaction; no store method commits independently.
"""

import copy
import math
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .config import Settings
from .db_models import (
    ActiveJobRecord,
    AnalysisRunRecord,
    IdempotencyRecord,
    JobRecord,
    LedgerRecord,
    ProjectRecord,
    TokenRecord,
    TranscriptRecord,
    UploadRecord,
    UserRecord,
)
from .errors import ApiError
from .models import Guidance, Project, UploadRequest

DAY = 86_400_000


def oid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


# Each demo moment is a complete thought with goal affinities and topical tags.
MOMENTS = [
    (
        "Most founders hire backwards",
        "Hire for the company you will have in eighteen months, not only for the work you have today. Write down the decisions your next hire will own before choosing a title.",
        ["hiring", "career"],
        [0.9, 0.5, 1.0],
    ),
    (
        "The three-question hiring framework",
        "Before every senior hire, define the decisions they will own, what a great first quarter looks like, and who they should disagree with. These questions make the work clearer.",
        ["hiring", "framework"],
        [1.0, 0.4, 0.6],
    ),
    (
        "My worst hire was my best friend",
        "We shared the same instincts and the same blind spots. Nobody challenged our decisions. I learned to build a team whose strengths were different from mine.",
        ["hiring", "story"],
        [0.6, 1.0, 0.9],
    ),
    (
        "Remote work exposed our culture",
        "Remote work made us notice how much knowledge lived in hallway conversations. Writing decisions down helped the whole team understand the reasons behind the work.",
        ["remote", "culture"],
        [0.8, 0.6, 0.95],
    ),
    (
        "Pricing should reflect the value",
        "We asked customers which problems our product solved and what those problems cost them. That conversation helped us understand our pricing and explain the value more clearly.",
        ["pricing", "growth"],
        [0.95, 0.5, 0.8],
    ),
    (
        "The real cost of growth was meetings",
        "As the team grew, every decision required more calendars. We gave decisions clear owners and wrote down the context so fewer people needed to attend every meeting.",
        ["growth", "operations"],
        [0.9, 0.7, 0.85],
    ),
    (
        "Innovation starts near the customer",
        "The people closest to customers hear recurring problems first. Give them room to test small improvements and share what they learn with the rest of the company.",
        ["leadership", "customers"],
        [0.8, 0.5, 0.9],
    ),
    (
        "More signups did not mean more sales",
        "Our free tier brought in many curious visitors, but they needed different things from our paying customers. We learned to measure whether people solved their problem instead of counting signups alone.",
        ["sales", "story"],
        [0.85, 0.9, 0.9],
    ),
    (
        "Fix the boring process first",
        "Onboarding, invoicing, and handoffs make a larger difference than they appear to. When these routines work reliably, people have more time to spend on their customers.",
        ["operations", "advice"],
        [1.0, 0.5, 0.7],
    ),
    (
        "One customer, one metric, one quarter",
        "Choose one customer problem and one useful way to measure progress. Give the team a quarter to learn, then review the evidence together before making the next plan.",
        ["career", "framework"],
        [0.95, 0.6, 0.8],
    ),
]


class Store:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
        fail_next: set[str] | None = None,
    ):
        self.db = db
        self.settings = settings or Settings()
        self.clock = clock
        self.fail_next = fail_next if fail_next is not None else set()

    def user_by_email(self, email: str) -> UserRecord | None:
        return self.db.scalar(select(UserRecord).where(UserRecord.email == email))

    def user(self, user_id: str) -> UserRecord | None:
        return self.db.get(UserRecord, user_id)

    def token(self, digest: str) -> TokenRecord | None:
        return self.db.get(TokenRecord, digest)

    def add_token(self, digest: str, user_id: str):
        self.db.add(
            TokenRecord(
                digest=digest,
                userId=user_id,
                expiresAt=self.now() + self.settings.token_ttl_seconds * 1000,
            )
        )

    def revoke_token(self, digest: str):
        self.db.execute(delete(TokenRecord).where(TokenRecord.digest == digest))

    def list_projects(self, user: UserRecord) -> list[dict]:
        projects = self.db.scalars(
            select(ProjectRecord)
            .where(ProjectRecord.userId == user.id, ProjectRecord.status != "deleted")
            .order_by(ProjectRecord.createdAt.desc(), ProjectRecord.id)
        )
        return [self.project_view(p) for p in projects]

    def add_upload(self, fields: dict):
        self.db.add(UploadRecord(**fields))

    def upload(self, upload_id: str) -> UploadRecord | None:
        return self.db.get(UploadRecord, upload_id)

    def save_transcript(self, project_id: str, fields: dict):
        self.db.get(TranscriptRecord, project_id).update(**fields)

    def run(self, run_id: str) -> dict:
        return self.db.get(AnalysisRunRecord, run_id).as_dict()

    def release_job(self, job: JobRecord):
        self.db.execute(delete(ActiveJobRecord).where(ActiveJobRecord.jobId == job.id))

    def now(self) -> int:
        return int(self.clock() * 1000)

    def month_start(self) -> int:
        current = datetime.fromtimestamp(self.clock(), UTC)
        return int(
            current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )

    def usage_entries(self, user: UserRecord) -> list[dict]:
        entries = self.db.scalars(
            select(LedgerRecord)
            .where(LedgerRecord.userId == user.id, LedgerRecord.createdAt >= self.month_start())
            .order_by(LedgerRecord.createdAt, LedgerRecord.id)
        )
        return [entry.as_dict() for entry in entries]

    def used_seconds(self, user: UserRecord) -> int:
        return self.db.scalar(
            select(
                func.coalesce(
                    func.sum(LedgerRecord.processedSeconds + LedgerRecord.adjustmentSeconds), 0
                )
            ).where(LedgerRecord.userId == user.id, LedgerRecord.createdAt >= self.month_start())
        )

    def usage(self, user: UserRecord) -> dict:
        used = math.ceil(self.used_seconds(user) / 60)
        return {
            "usedMinutes": used,
            "limitMinutes": user.limitMinutes,
            "remainingMinutes": max(0, user.limitMinutes - used),
        }

    def quota(self, user: UserRecord, seconds: float):
        if math.ceil((self.used_seconds(user) + seconds) / 60) > user.limitMinutes:
            raise ApiError(
                "MONTHLY_LIMIT_REACHED", "This source exceeds your monthly allowance.", 402
            )

    def charge(
        self, project: ProjectRecord, job: JobRecord | None, seconds: float, at: int | None = None
    ):
        existing = self.db.scalar(
            select(LedgerRecord.id).where(
                LedgerRecord.projectId == project.id, LedgerRecord.usageType == "transcription"
            )
        )
        if existing:
            return
        self.db.add(
            LedgerRecord(
                id=oid("led"),
                userId=project.userId,
                projectId=project.id,
                jobId=job.id if job else None,
                usageType="transcription",
                processedSeconds=round(seconds),
                adjustmentSeconds=0,
                createdAt=self.now() if at is None else at,
            )
        )
        self.db.flush()

    def owned(self, user: UserRecord, project_id: str) -> ProjectRecord:
        p = self.db.scalar(
            select(ProjectRecord).where(
                ProjectRecord.id == project_id,
                ProjectRecord.userId == user.id,
                ProjectRecord.status != "deleted",
            )
        )
        if p is None:
            raise ApiError("NOT_FOUND", "Project not found.", 404)
        return p

    def active_job(self, user_id: str) -> JobRecord | None:
        return self.db.scalar(
            select(JobRecord)
            .join(ActiveJobRecord, ActiveJobRecord.jobId == JobRecord.id)
            .where(ActiveJobRecord.userId == user_id)
        )

    def project_job(self, project_id: str, active: bool = False) -> JobRecord | None:
        query = select(JobRecord).where(JobRecord.projectId == project_id)
        if active:
            query = query.where(JobRecord.state == "running")
        return self.db.scalar(query.order_by(JobRecord.attempt.desc()).limit(1))

    def session(self, user: UserRecord) -> dict:
        job = self.active_job(user.id)
        return {
            "user": {k: getattr(user, k) for k in ("id", "email", "displayName", "role")},
            "features": {"youtubeImport": self.settings.youtube_import},
            "usage": self.usage(user),
            "activeJob": {
                "id": job.id,
                "projectId": job.projectId,
                "type": job.type,
                "stage": job.stages[job.index],
            }
            if job
            else None,
        }

    def new_project(
        self, user: UserRecord, source: UploadRequest, source_type="upload"
    ) -> ProjectRecord:
        public = Project(
            id=oid("prj"),
            name=source.title or source.fileName.rsplit(".", 1)[0],
            sourceType=source_type,
            status="uploading",
            durationSec=source.durationSec,
            width=source.width,
            height=source.height,
            fps=source.fps,
            sizeBytes=source.sizeBytes,
            createdAt=self.now(),
        ).model_dump()
        p = ProjectRecord(
            **{
                **public,
                "userId": user.id,
                "sourceReady": False,
                "currentRunId": None,
                "regenError": None,
                "historyExpiresAt": self.now() + self.settings.history_retention_days * DAY,
            }
        )
        self.db.add(p)
        self.db.flush()
        return p

    def project_view(self, p: ProjectRecord) -> dict:
        return Project.model_validate(p, from_attributes=True).model_dump()

    def transcript(self, p: ProjectRecord) -> dict:
        if p.status == "expired":
            raise ApiError("ASSET_EXPIRED", "Transcript and analysis have expired.", 410)
        record = self.db.get(TranscriptRecord, p.id)
        if record is None:
            raise ApiError("INVALID_STATE", "The transcript is not ready yet.", 409)
        return record.as_dict()

    def build_transcript(self, p: ProjectRecord):
        # Short sources contain fewer complete moments, never fabricated sub-15s suggestions.
        count = min(len(MOMENTS), max(1, int(p.durationSec // 30)))
        interval = p.durationSec / count
        segments = []
        for i, (hook, body, *_rest) in enumerate(MOMENTS[:count]):
            start = round(i * interval, 2)
            end = round(start + min(interval, 45), 2)
            text = hook + ". " + body
            words = text.split()
            step = (end - start) / len(words)
            segments.append(
                {
                    "id": f"seg_{i}",
                    "start": start,
                    "end": end,
                    "text": text,
                    "timing": "word",
                    "words": [
                        {
                            "w": w,
                            "s": round(start + n * step, 2),
                            "e": round(start + (n + 1) * step, 2),
                        }
                        for n, w in enumerate(words)
                    ],
                }
            )
        self.db.add(
            TranscriptRecord(
                projectId=p.id, language="en", revision=1, userCorrected=False, segments=segments
            )
        )
        p.language = "en"
        p.transcriptRevision = 1

    def analyze(self, p: ProjectRecord, params: dict):
        transcript = self.transcript(p)
        goal_index = ["educational", "entertaining", "viral"].index(params["goal"])
        candidates = []
        for index, seg in enumerate(transcript["segments"]):
            _, _, tags, affinities = MOMENTS[index % len(MOMENTS)]
            haystack = (seg["text"] + " " + " ".join(tags)).lower()
            if any(topic.lower() in haystack for topic in params["avoid"]):
                continue
            hook = seg["text"].split(". ")[0].strip()[:80]
            scores = {
                "hook": round(75 + affinities[goal_index] * 20),
                "value": 90 - index,
                "standalone": 92,
                "visual": 82,
            }
            combined = round(scores["hook"] * 0.3 + scores["value"] * 0.3 + 92 * 0.25 + 82 * 0.15)
            boost = 60 if any(tag in params["instruction"].lower() for tag in tags) else 0
            candidates.append(
                {
                    "id": oid("clp"),
                    "projectId": p.id,
                    "start": seg["start"],
                    "end": seg["end"],
                    "duration": round(seg["end"] - seg["start"], 1),
                    "hook": hook,
                    "scores": scores,
                    "combined": combined,
                    "reason": "A complete demo thought with a clear takeaway and natural boundaries.",
                    "tags": tags,
                    "_rank": combined * affinities[goal_index] + boost,
                }
            )
        candidates.sort(key=lambda c: c["_rank"], reverse=True)
        duration = p.durationSec
        count = 3 if duration < 600 else 5 if duration < 1200 else 7 if duration < 2400 else 10
        run_id = oid("run")
        clips = candidates[:count]
        for rank, clip in enumerate(clips, 1):
            clip.pop("_rank")
            clip.update(rank=rank, runId=run_id)
        self.db.add(
            AnalysisRunRecord(
                projectId=p.id,
                runId=run_id,
                **params,
                stale=False,
                promptVersion="demo-v1",
                modelName="demo-simulator",
                clips=clips,
            )
        )
        self.db.flush()
        p.update(currentRunId=run_id, suggestionCount=len(clips), clipsStale=False, **params)

    def expire(self):
        now = self.now()
        for p in self.db.scalars(select(ProjectRecord).where(ProjectRecord.status != "deleted")):
            if p.status == "deleted":
                continue
            if now >= p.historyExpiresAt:
                self.delete(p)
                continue
            if p.sourceExpiresAt is not None and now >= p.sourceExpiresAt:
                p.sourceExpired = True
            if p.analysisExpiresAt is not None and now >= p.analysisExpiresAt:
                job = self.project_job(p.id, active=True)
                if job:
                    job.state = "canceled"
                    job.finishedAt = now
                    self.release_job(job)
                p.status = "expired"
                self.purge_content(p)
        self.db.execute(delete(TokenRecord).where(TokenRecord.expiresAt <= now))

    def tick(self):
        self.expire()
        for j in self.db.scalars(select(JobRecord).where(JobRecord.state == "running")):
            if j.state != "running":
                continue
            p = self.db.get(ProjectRecord, j.projectId)
            while j.index < len(j.stages):
                stage = j.stages[j.index]
                end = j.stageStartedAt + self.stage_ms(stage)
                if self.now() < end:
                    break
                if stage in self.fail_next:
                    self.fail_next.remove(stage)
                    code = "TRANSCRIPTION_FAILED" if stage == "transcribing" else "ANALYSIS_FAILED"
                    j.update(state="failed_retryable", failureCode=code, finishedAt=end)
                    p.sourceExpiresAt = (
                        p.sourceExpiresAt or end + self.settings.source_retention_hours * 3_600_000
                    )
                    if self.db.get(TranscriptRecord, p.id) is not None:
                        p.analysisExpiresAt = (
                            p.analysisExpiresAt or end + self.settings.analysis_retention_days * DAY
                        )
                    if j.type == "analysis" and p.currentRunId:
                        p.update(status="ready", regenError=code)
                    else:
                        p.update(status="failed", failureCode=code)
                    break
                if stage == "transcribing":
                    self.build_transcript(p)
                    self.charge(p, j, p.durationSec, at=end)
                else:
                    self.analyze(p, j.params)
                    p.update(
                        status="ready",
                        failureCode=None,
                        regenError=None,
                        sourceExpiresAt=p.sourceExpiresAt
                        or end + self.settings.source_retention_hours * 3_600_000,
                        analysisExpiresAt=end + self.settings.analysis_retention_days * DAY,
                    )
                j.index += 1
                j.stageStartedAt = end
                if j.index == len(j.stages):
                    j.update(state="succeeded", finishedAt=end)
                else:
                    p.status = j.stages[j.index]
            if j.state != "running":
                self.release_job(j)
        # A clock jump can complete a job whose retention window is already over.
        self.expire()

    def stage_ms(self, stage: str) -> int:
        seconds = (
            self.settings.transcription_seconds
            if stage == "transcribing"
            else self.settings.analysis_seconds
        )
        return max(1, round(seconds * 1000))

    def status(self, p: ProjectRecord) -> dict:
        j = self.project_job(p.id)
        running = j is not None and j.state == "running"
        stage = j.stages[j.index] if running else None
        progress = 1.0 if p.status == "ready" else 0.0
        if running:
            fraction = min(1, max(0, (self.now() - j.stageStartedAt) / self.stage_ms(stage)))
            progress = (
                fraction
                if j.type == "analysis"
                else 0.6 * fraction
                if stage == "transcribing"
                else 0.6 + 0.4 * fraction
            )
        return {
            "projectId": p.id,
            "status": p.status,
            "jobId": j.id if j else None,
            "jobType": j.type if j else None,
            "stage": stage,
            "progress": round(progress, 2),
            "failureCode": p.failureCode,
            "regenError": p.regenError,
            "canCancel": bool(running and j.type == "process"),
            "canRetry": p.status == "failed",
        }

    def start(
        self,
        user: UserRecord,
        p: ProjectRecord,
        action: str,
        guidance: Guidance | None,
        key: str | None,
    ):
        scope = (user.id, p.id, action, key)
        payload = guidance.model_dump() if guidance else {}
        previous_request = self.db.get(IdempotencyRecord, scope) if key else None
        if previous_request is not None:
            if previous_request.payload != payload:
                raise ApiError(
                    "INVALID_INPUT", "This idempotency key was used with different input.", 422
                )
            return copy.deepcopy(previous_request.response)
        if action == "process":
            if p.status != "uploading" or not p.sourceReady:
                raise ApiError("INVALID_STATE", "Complete the upload before processing.", 409)
            self.quota(user, p.durationSec)
            stages, params = ["transcribing", "analyzing"], payload
        elif action == "retry":
            previous = self.project_job(p.id)
            if p.status != "failed" or not previous:
                raise ApiError("INVALID_STATE", "Only failed projects can be retried.", 409)
            if p.sourceExpired and self.db.get(TranscriptRecord, p.id) is None:
                raise ApiError("INVALID_STATE", "The source expired. Upload it again.", 409)
            stages = (
                ["analyzing"]
                if self.db.get(TranscriptRecord, p.id) is not None
                else ["transcribing", "analyzing"]
            )
            if "transcribing" in stages:
                self.quota(user, p.durationSec)
            params = previous.params
        else:
            self.transcript(p)
            if p.status != "ready":
                raise ApiError("INVALID_STATE", "Wait for the current step to finish.", 409)
            stages, params = ["analyzing"], payload
        if self.active_job(user.id):
            raise ApiError("ACTIVE_JOB_EXISTS", "Another processing job is active.", 409)
        previous = self.project_job(p.id)
        job = JobRecord(
            id=oid("job"),
            projectId=p.id,
            userId=user.id,
            type="analysis" if action == "analysis" else "process",
            state="running",
            attempt=(previous.attempt + 1 if previous else 1),
            params=copy.deepcopy(params),
            stages=stages,
            index=0,
            stageStartedAt=self.now(),
            startedAt=self.now(),
            queuedAt=self.now(),
            finishedAt=None,
            failureCode=None,
            idempotencyKey=key,
        )
        self.db.add(job)
        self.db.flush()
        self.db.add(ActiveJobRecord(userId=user.id, jobId=job.id))
        self.db.flush()
        p.update(status=stages[0], failureCode=None, regenError=None)
        if key:
            self.db.add(
                IdempotencyRecord(
                    userId=user.id,
                    projectId=p.id,
                    action=action,
                    key=key,
                    payload=payload,
                    jobId=job.id,
                    response=self.status(p),
                )
            )
        return self.status(p)

    def cancel(self, p: ProjectRecord):
        job = self.project_job(p.id, active=True)
        if not job or job.type != "process":
            raise ApiError("INVALID_STATE", "There is no cancelable processing job.", 409)
        if job.stages[job.index] == "transcribing":
            fraction = min(
                1, max(0, (self.now() - job.stageStartedAt) / self.stage_ms("transcribing"))
            )
            self.charge(p, job, p.durationSec * fraction)
        job.update(state="canceled", finishedAt=self.now())
        self.release_job(job)
        p.update(
            status="canceled",
            sourceExpiresAt=self.now() + self.settings.source_retention_hours * 3_600_000,
        )
        if self.db.get(TranscriptRecord, p.id) is not None:
            p.analysisExpiresAt = self.now() + self.settings.analysis_retention_days * DAY
        return self.status(p)

    def purge_content(self, p: ProjectRecord):
        self.db.execute(delete(TranscriptRecord).where(TranscriptRecord.projectId == p.id))
        self.db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.projectId == p.id))
        self.db.execute(delete(AnalysisRunRecord).where(AnalysisRunRecord.projectId == p.id))
        self.db.execute(update(JobRecord).where(JobRecord.projectId == p.id).values(params={}))
        p.update(
            currentRunId=None,
            suggestionCount=0,
            clipsStale=False,
            transcriptRevision=None,
            instruction="",
            avoid=[],
        )

    def delete(self, p: ProjectRecord):
        job = self.project_job(p.id, active=True)
        if job:
            if job.type == "process":
                self.cancel(p)
            else:
                job.update(state="canceled", finishedAt=self.now())
                self.release_job(job)
        self.purge_content(p)
        p.update(status="deleted", name="Deleted project", sourceExpired=True)
        self.db.execute(delete(UploadRecord).where(UploadRecord.projectId == p.id))

    def seed(self):
        from .auth import hash_password

        for email, name, role, invited, active in [
            ("maya@example.com", "Maya Ortiz", "creator", True, True),
            ("jon@example.com", "Jon Park", "creator", True, True),
            ("ops@example.com", "Beta Ops", "operator", True, True),
            ("sam@example.com", "Sam Lee", "creator", False, True),
            ("disabled@example.com", "Disabled Creator", "creator", True, False),
        ]:
            user = UserRecord(
                id=oid("usr"),
                email=email,
                displayName=name,
                role=role,
                invited=invited,
                active=active,
                limitMinutes=60,
                passwordHash=hash_password("DemoPass123!"),
            )
            self.db.add(user)
        self.db.flush()
        maya = self.user_by_email("maya@example.com")
        jon = self.user_by_email("jon@example.com")
        for user, name, seconds, age in [
            (maya, "The Operator Hour — Ep. 41: Live Q&A", 1100, 2),
            (maya, "The Operator Hour — Ep. 38: Hiring in 2026", 2460, 40),
            (jon, "Jon’s private strategy call", 900, 1),
        ]:
            source = UploadRequest(
                fileName="demo.mp4",
                title=name,
                mimeType="video/mp4",
                sizeBytes=seconds * 900_000,
                durationSec=seconds,
                width=1920,
                height=1080,
                fps=30,
                hasVideo=True,
                hasAudio=True,
            )
            p = self.new_project(user, source)
            p.createdAt = self.now() - age * DAY
            p.historyExpiresAt = p.createdAt + self.settings.history_retention_days * DAY
            p.sourceReady = True
            self.build_transcript(p)
            self.analyze(p, Guidance(goal="educational").model_dump())
            p.update(
                status="ready",
                sourceExpiresAt=p.createdAt + DAY,
                analysisExpiresAt=p.createdAt + self.settings.analysis_retention_days * DAY,
            )
            # Keep one current-month charge even when today is the first of a month.
            self.charge(
                p,
                None,
                seconds,
                at=max(self.month_start(), p.createdAt) if age < 30 else p.createdAt,
            )
        failed = self.new_project(
            maya,
            UploadRequest(
                fileName="interview-retry-demo.mp4",
                mimeType="video/mp4",
                sizeBytes=1000000,
                durationSec=300,
                width=1920,
                height=1080,
                fps=30,
                hasVideo=True,
                hasAudio=True,
            ),
        )
        failed.sourceReady = True
        self.start(maya, failed, "process", Guidance(goal="educational"), None)
        job = self.project_job(failed.id)
        job.update(
            state="failed_retryable", failureCode="TRANSCRIPTION_FAILED", finishedAt=self.now()
        )
        self.release_job(job)
        failed.update(
            status="failed",
            failureCode="TRANSCRIPTION_FAILED",
            sourceExpiresAt=self.now() + self.settings.source_retention_hours * 3_600_000,
        )
        self.expire()


def get_store(request: Request) -> Iterator[Store]:
    with request.app.state.database.transaction() as session:
        store = Store(
            session,
            request.app.state.settings,
            request.app.state.clock,
            request.app.state.fail_next,
        )
        store.tick()
        yield store
