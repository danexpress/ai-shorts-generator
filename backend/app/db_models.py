"""Portable SQLAlchemy records; API payload models remain in models.py.

Scalar fields used for ownership, lifecycle and accounting are normal columns.
Transcript segments and analysis results are JSON documents, replaced atomically.
No backend-specific JSON operators, enum types, or upsert syntax are used.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    def as_dict(self) -> dict:
        return {prop.key: getattr(self, prop.key) for prop in self.__mapper__.column_attrs}

    def update(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class DatabaseState(Base):
    __tablename__ = "database_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    demoSeeded: Mapped[bool] = mapped_column("demo_seeded", Boolean, default=False)


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("limit_minutes >= 0", name="ck_user_allowance"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    displayName: Mapped[str] = mapped_column("display_name", String(255))
    role: Mapped[str] = mapped_column(String(20))
    invited: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean)
    limitMinutes: Mapped[int] = mapped_column("limit_minutes", Integer)
    unlimitedUsage: Mapped[bool] = mapped_column("unlimited_usage", Boolean, default=False)
    passwordHash: Mapped[str] = mapped_column("password_hash", String(255))


class TokenRecord(Base):
    __tablename__ = "auth_tokens"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), index=True)
    expiresAt: Mapped[int] = mapped_column("expires_at", BigInteger, index=True)


class ProjectRecord(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    sourceType: Mapped[str] = mapped_column("source_type", String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    sourceReady: Mapped[bool] = mapped_column("source_ready", Boolean)
    durationSec: Mapped[float] = mapped_column("duration_sec", Float)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[float] = mapped_column(Float)
    sizeBytes: Mapped[int] = mapped_column("size_bytes", BigInteger)
    language: Mapped[str | None] = mapped_column(String(40))
    goal: Mapped[str | None] = mapped_column(String(20))
    instruction: Mapped[str] = mapped_column(Text)
    avoid: Mapped[list] = mapped_column(JSON)
    createdAt: Mapped[int] = mapped_column("created_at", BigInteger, index=True)
    suggestionCount: Mapped[int] = mapped_column("suggestion_count", Integer)
    renderCount: Mapped[int] = mapped_column("render_count", Integer)
    sourceExpiresAt: Mapped[int | None] = mapped_column("source_expires_at", BigInteger)
    sourceExpired: Mapped[bool] = mapped_column("source_expired", Boolean)
    analysisExpiresAt: Mapped[int | None] = mapped_column("analysis_expires_at", BigInteger)
    historyExpiresAt: Mapped[int] = mapped_column("history_expires_at", BigInteger)
    clipsStale: Mapped[bool] = mapped_column("clips_stale", Boolean)
    failureCode: Mapped[str | None] = mapped_column("failure_code", String(60))
    regenError: Mapped[str | None] = mapped_column("regen_error", String(60))
    transcriptRevision: Mapped[int | None] = mapped_column("transcript_revision", Integer)
    # Current run is a pointer within this project's retained analysis documents.
    currentRunId: Mapped[str | None] = mapped_column("current_run_id", String(40))


class UploadRecord(Base):
    __tablename__ = "uploads"
    uploadId: Mapped[str] = mapped_column("upload_id", String(40), primary_key=True)
    projectId: Mapped[str] = mapped_column("project_id", ForeignKey("projects.id"), index=True)
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), index=True)
    uploadUrl: Mapped[str] = mapped_column("upload_url", Text)
    expiresAt: Mapped[int] = mapped_column("expires_at", BigInteger)
    storedName: Mapped[str | None] = mapped_column("stored_name", String(80))
    contentType: Mapped[str | None] = mapped_column("content_type", String(128))


class RenderRecord(Base):
    __tablename__ = "renders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    projectId: Mapped[str] = mapped_column("project_id", ForeignKey("projects.id"), index=True)
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), index=True)
    clipId: Mapped[str] = mapped_column("clip_id", String(40))
    storedName: Mapped[str] = mapped_column("stored_name", String(80))
    fileName: Mapped[str] = mapped_column("file_name", String(255))
    resolution: Mapped[int] = mapped_column(Integer)
    durationSec: Mapped[float] = mapped_column("duration_sec", Float)
    sizeBytes: Mapped[int] = mapped_column("size_bytes", BigInteger)
    createdAt: Mapped[int] = mapped_column("created_at", BigInteger, index=True)
    expiresAt: Mapped[int] = mapped_column("expires_at", BigInteger, index=True)


class TranscriptRecord(Base):
    __tablename__ = "transcripts"
    projectId: Mapped[str] = mapped_column(
        "project_id", ForeignKey("projects.id"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    userCorrected: Mapped[bool] = mapped_column("user_corrected", Boolean)
    segments: Mapped[list] = mapped_column(JSON)


class AnalysisRunRecord(Base):
    __tablename__ = "analysis_runs"
    runId: Mapped[str] = mapped_column("run_id", String(40), primary_key=True)
    projectId: Mapped[str] = mapped_column("project_id", ForeignKey("projects.id"), index=True)
    goal: Mapped[str] = mapped_column(String(20))
    instruction: Mapped[str] = mapped_column(Text)
    avoid: Mapped[list] = mapped_column(JSON)
    stale: Mapped[bool] = mapped_column(Boolean)
    promptVersion: Mapped[str] = mapped_column("prompt_version", String(60))
    modelName: Mapped[str] = mapped_column("model_name", String(120))
    clips: Mapped[list] = mapped_column(JSON)


class JobRecord(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    projectId: Mapped[str] = mapped_column("project_id", ForeignKey("projects.id"), index=True)
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(30), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON)
    stages: Mapped[list] = mapped_column(JSON)
    index: Mapped[int] = mapped_column("stage_index", Integer)
    stageStartedAt: Mapped[int] = mapped_column("stage_started_at", BigInteger)
    startedAt: Mapped[int] = mapped_column("started_at", BigInteger)
    queuedAt: Mapped[int] = mapped_column("queued_at", BigInteger)
    finishedAt: Mapped[int | None] = mapped_column("finished_at", BigInteger)
    failureCode: Mapped[str | None] = mapped_column("failure_code", String(60))
    idempotencyKey: Mapped[str | None] = mapped_column("idempotency_key", String(255))


class ActiveJobRecord(Base):
    __tablename__ = "active_jobs"
    # Portable constraint: at most one active compute job per creator, no partial index.
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), primary_key=True)
    jobId: Mapped[str] = mapped_column("job_id", ForeignKey("jobs.id"), unique=True)


class LedgerRecord(Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        UniqueConstraint("project_id", "usage_type", name="uq_usage_project_type"),
        CheckConstraint("processed_seconds >= 0", name="ck_usage_processed_seconds"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), index=True)
    projectId: Mapped[str] = mapped_column("project_id", ForeignKey("projects.id"), index=True)
    jobId: Mapped[str | None] = mapped_column("job_id", ForeignKey("jobs.id"))
    usageType: Mapped[str] = mapped_column("usage_type", String(40))
    processedSeconds: Mapped[int] = mapped_column("processed_seconds", BigInteger)
    adjustmentSeconds: Mapped[int] = mapped_column("adjustment_seconds", BigInteger)
    createdAt: Mapped[int] = mapped_column("created_at", BigInteger, index=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    userId: Mapped[str] = mapped_column("user_id", ForeignKey("users.id"), primary_key=True)
    projectId: Mapped[str] = mapped_column(
        "project_id", ForeignKey("projects.id"), primary_key=True
    )
    action: Mapped[str] = mapped_column(String(30), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    jobId: Mapped[str] = mapped_column("job_id", ForeignKey("jobs.id"))
    response: Mapped[dict] = mapped_column(JSON)
