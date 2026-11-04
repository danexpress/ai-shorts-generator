"""Story Mode API models. The script keeps the documented snake_case shape."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..models import RequestModel
from .genres import GENRES

TargetSeconds = Literal[30, 45, 60]


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


class StoryGenre(BaseModel):
    id: str
    label: str
    defaultTone: str
    moods: list[str]


class StoryGenres(BaseModel):
    genres: list[StoryGenre]
    lengths: list[int]


class Story(BaseModel):
    id: str
    title: str
    status: Literal["draft"]
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


class StorySummary(BaseModel):
    id: str
    title: str
    status: Literal["draft"]
    genre: str
    targetSeconds: int
    estimatedSeconds: int
    createdAt: int
    updatedAt: int


class StoryList(BaseModel):
    stories: list[StorySummary]
