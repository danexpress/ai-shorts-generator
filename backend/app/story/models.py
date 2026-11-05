"""Story Mode API models. The script keeps the documented snake_case shape."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..models import RenderedClip, RequestModel
from .genres import GENRES

TargetSeconds = Literal[30, 45, 60]
StoryStatus = Literal[
    "draft", "voicing", "voiced", "voice_failed", "rendering", "rendered", "render_failed"
]
TitleCard = Literal["title", "hook", "none"]
BackgroundKind = Literal["bundled", "folder", "library"]


class StoryCreateRequest(RequestModel):
    topic: str | None = Field(default=None, max_length=300)
    story: str | None = Field(default=None, max_length=6000)
    genre: str = Field(max_length=40)
    targetSeconds: TargetSeconds = 45
    tone: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def one_source(self):
        topic, story = (self.topic or "").strip(), (self.story or "").strip()
        if bool(topic) == bool(story):
            raise ValueError("Give either a topic or a story")
        if topic and len(topic) < 3 or story and len(story) < 40:
            raise ValueError("Too short")
        if self.genre not in GENRES:
            raise ValueError("Unknown genre")
        return self


class StoryScene(BaseModel):
    id: int
    narration: str = Field(max_length=5000)
    visual_description: str = Field(max_length=5000)
    mood: str = Field(max_length=500)


class StoryScript(BaseModel):
    title: str = Field(max_length=1000)
    hook: str = Field(max_length=1000)
    scenes: list[StoryScene] = Field(max_length=40)
    estimated_seconds: int = 0


class StoryScriptUpdate(RequestModel):
    revision: int
    script: StoryScript


class StoryRegenerateRequest(RequestModel):
    revision: int
    # Rewrite the current script shorter instead of writing a new one.
    shorten: bool = False


class StoryVoiceRequest(RequestModel):
    revision: int
    provider: str = Field(max_length=40)
    voice: str = Field(max_length=100)
    # Omitted: the provider's default speed.
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class StoryVoiceOption(BaseModel):
    id: str
    label: str
    description: str


class StoryVoiceProvider(BaseModel):
    id: str
    label: str
    available: bool
    reason: str | None
    speedRange: list[float]
    defaultSpeed: float
    voices: list[StoryVoiceOption]


class StoryVoices(BaseModel):
    defaultProvider: str | None
    providers: list[StoryVoiceProvider]


class StorySceneTiming(BaseModel):
    id: int
    start: float
    end: float


class StoryWord(BaseModel):
    text: str
    start: float
    end: float
    scene: int


class StoryLoudness(BaseModel):
    inputLufs: float | None
    outputLufs: float | None


class StoryVoice(BaseModel):
    provider: str
    voice: str
    speed: float
    durationSec: float
    tooLong: bool
    scenes: list[StorySceneTiming]
    words: list[StoryWord]
    timingSources: list[str]
    characters: int
    costUsd: float
    cachedScenes: int
    loudness: StoryLoudness
    stale: bool


class StoryVoiceSettings(BaseModel):
    provider: str
    voice: str
    speed: float


class StoryJobError(BaseModel):
    code: str
    message: str


class StoryVoiceJob(BaseModel):
    request: StoryVoiceSettings | None
    progress: float | None
    error: StoryJobError | None


class StoryBackgroundChoice(BaseModel):
    kind: BackgroundKind
    id: str = Field(max_length=255)


class StoryRenderRequest(RequestModel):
    revision: int
    # Omitted: the last render's background, or the genre's default loop.
    background: StoryBackgroundChoice | None = None
    music: str | None = Field(default=None, max_length=255)
    captionPreset: str = Field(default="clean-bold", max_length=40)
    captions: bool = True
    titleCard: TitleCard = "title"


class StoryBackgroundRef(BaseModel):
    kind: BackgroundKind
    id: str | None = None
    name: str | None = None
    projectId: str | None = None


class StoryRenderSettings(BaseModel):
    background: StoryBackgroundRef
    music: str | None
    captionPreset: str
    captions: bool
    titleCard: TitleCard


class StoryRenderJob(BaseModel):
    request: StoryRenderSettings | None
    error: StoryJobError | None


class StoryAsset(BaseModel):
    kind: BackgroundKind
    id: str
    label: str
    description: str


class StoryMusicTrack(BaseModel):
    id: str
    label: str


class StoryCaptionPreset(BaseModel):
    id: str
    label: str


class StoryAssets(BaseModel):
    backgrounds: list[StoryAsset]
    music: list[StoryMusicTrack]
    captionPresets: list[StoryCaptionPreset]


class StoryGenre(BaseModel):
    id: str
    label: str
    defaultTone: str
    moods: list[str]
    defaultBackground: str


class StoryGenres(BaseModel):
    genres: list[StoryGenre]
    lengths: list[int]


class Story(BaseModel):
    id: str
    title: str
    status: StoryStatus
    revision: int
    createdAt: int
    updatedAt: int
    genre: str
    targetSeconds: int
    tone: str
    topic: str | None
    story: str | None
    script: StoryScript
    scriptSource: Literal["generated", "edited"]
    wordCount: int
    wordBudget: list[int]
    warnings: list[str]
    voice: StoryVoice | None
    voiceJob: StoryVoiceJob
    renderJob: StoryRenderJob
    renderSettings: StoryRenderSettings
    lastRenderId: str | None


class StorySummary(BaseModel):
    id: str
    title: str
    status: StoryStatus
    genre: str
    targetSeconds: int
    estimatedSeconds: int
    createdAt: int
    updatedAt: int


class StoryList(BaseModel):
    stories: list[StorySummary]


class StoryRenderList(BaseModel):
    renders: list[RenderedClip]
