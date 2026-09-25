"""Request/response models preserve the frontend's camelCase field names."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Goal = Literal["educational", "entertaining", "viral"]
ProjectState = Literal[
    "draft",
    "uploading",
    "transcribing",
    "analyzing",
    "ready",
    "failed",
    "canceled",
    "expired",
    "deleted",
]
JobType = Literal["process", "analysis"]
Stage = Literal["transcribing", "analyzing"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SignInRequest(RequestModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("Invalid email")
        return value


class Guidance(RequestModel):
    goal: Goal
    instruction: str = Field(default="", max_length=200)
    avoid: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("instruction", mode="before")
    @classmethod
    def clean_instruction(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("avoid")
    @classmethod
    def clean_avoid(cls, value):
        value = [item.strip() for item in value if item.strip()]
        if any(len(item) > 40 for item in value):
            raise ValueError("Topics must have at most 40 characters")
        return value


class UploadRequest(RequestModel):
    fileName: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    mimeType: str = Field(max_length=128)
    sizeBytes: int = Field(ge=0)
    durationSec: float
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    fps: float = Field(ge=0)
    hasVideo: bool
    hasAudio: bool


class YoutubeRequest(RequestModel):
    url: str = Field(max_length=2048)
    rightsConfirmed: bool = False


class SegmentEdit(RequestModel):
    id: str
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text", mode="before")
    @classmethod
    def clean_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class TranscriptEdit(RequestModel):
    segments: list[SegmentEdit]


class RetryRequest(RequestModel):
    pass


class User(BaseModel):
    id: str
    email: str
    displayName: str
    role: Literal["creator", "operator"]


class UsageSummary(BaseModel):
    usedMinutes: int
    limitMinutes: int | None
    remainingMinutes: int | None


class LedgerEntry(BaseModel):
    id: str
    userId: str
    projectId: str
    jobId: str | None
    usageType: str
    processedSeconds: int
    adjustmentSeconds: int
    createdAt: int


class Usage(UsageSummary):
    entries: list[LedgerEntry]


class ActiveJob(BaseModel):
    id: str
    projectId: str
    type: JobType
    stage: Stage


class Session(BaseModel):
    user: User
    features: dict[str, bool]
    usage: UsageSummary
    activeJob: ActiveJob | None


class LoginResponse(Session):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class Project(BaseModel):
    id: str
    name: str
    sourceType: Literal["upload", "youtube_url"]
    status: ProjectState
    durationSec: float
    width: int
    height: int
    fps: float
    sizeBytes: int
    language: str | None = None
    goal: Goal | None = None
    instruction: str = ""
    avoid: list[str] = Field(default_factory=list)
    createdAt: int
    suggestionCount: int = 0
    renderCount: int = 0
    sourceExpiresAt: int | None = None
    sourceExpired: bool = False
    analysisExpiresAt: int | None = None
    clipsStale: bool = False
    failureCode: str | None = None
    transcriptRevision: int | None = None


class UploadSession(BaseModel):
    uploadId: str
    projectId: str
    uploadUrl: str
    expiresAt: int
    project: Project


class PlaybackLink(BaseModel):
    url: str
    expiresAt: int


class MediaUploadResult(BaseModel):
    ok: bool
    bytes: int


class Status(BaseModel):
    projectId: str
    status: ProjectState
    jobId: str | None
    jobType: JobType | None
    stage: Stage | None
    progress: float = Field(ge=0, le=1)
    failureCode: str | None
    regenError: str | None
    canCancel: bool
    canRetry: bool


class Word(BaseModel):
    w: str
    s: float
    e: float


class Segment(BaseModel):
    id: str
    start: float
    end: float
    text: str
    timing: Literal["word", "segment"]
    words: list[Word]


class Transcript(BaseModel):
    projectId: str
    language: str
    revision: int
    userCorrected: bool
    segments: list[Segment]


class Download(BaseModel):
    kind: Literal["txt", "json", "mp4"]
    filename: str
    url: str
    bytes: int
    downloadUrl: str | None = None


class RenderRequest(RequestModel):
    clipId: str
    resolution: Literal[720, 1080] = 1080


class RenderedClip(BaseModel):
    id: str
    projectId: str
    clipId: str
    filename: str
    resolution: Literal[720, 1080]
    durationSec: float
    bytes: int
    createdAt: int
    expiresAt: int
    url: str
    downloadUrl: str


class Scores(BaseModel):
    hook: float = Field(ge=0, le=100)
    value: float = Field(ge=0, le=100)
    standalone: float = Field(ge=0, le=100)
    visual: float = Field(ge=0, le=100)


class Clip(BaseModel):
    id: str
    projectId: str
    runId: str
    rank: int
    start: float
    end: float
    duration: float
    hook: str
    scores: Scores
    combined: int
    reason: str
    tags: list[str]


class Clips(BaseModel):
    runId: str | None = None
    goal: Goal | None = None
    instruction: str = ""
    avoid: list[str] = Field(default_factory=list)
    stale: bool = False
    promptVersion: str = "demo-v1"
    modelName: str = "demo-simulator"
    clips: list[Clip] = Field(default_factory=list)


class Ok(BaseModel):
    ok: Literal[True] = True


class Deleted(Ok):
    deletionQueued: Literal[True] = True


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
