"""The text-to-speech provider interface used by Story Mode.

A provider turns one scene's narration into an audio file and, when it can, word
timestamps. Everything else (pauses, loudness, caching, alignment fallbacks) is
handled by app/story/voice.py, so a provider only has to speak. See README:
"Adding a voice provider".
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class TTSUnavailable(RuntimeError):
    """The provider is not installed, configured or reachable (worth retrying later)."""


class TTSFailed(RuntimeError):
    """The provider rejected this text or voice (retrying will not help)."""


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    # Free-form hints shown in the picker, e.g. "American English · female".
    description: str = ""


@dataclass
class Speech:
    # Word timestamps in seconds relative to the start of the file, or None when the
    # provider has none (the voice step then aligns the audio itself).
    words: list[dict] | None
    characters: int
    extra: dict = field(default_factory=dict)


class TTSProvider(ABC):
    id: str = ""
    label: str = ""
    speed_range: tuple[float, float] = (0.8, 1.2)
    # Where the picker starts: the speed closest to the ~140 words per minute that
    # script lengths are planned for.
    default_speed: float = 1.0
    # Bump when output for the same text/voice/speed changes (part of the cache key).
    version: str = "1"

    def __init__(self, settings):
        self.settings = settings

    @property
    def cost_per_character(self) -> float:
        """USD per input character, for cost logs. Local providers are free."""
        return 0.0

    def unavailable_reason(self) -> str | None:
        """Why this provider cannot be used right now, or None when it can."""
        return None

    @property
    def model(self) -> str:
        """The model or engine name, part of the cache key."""
        return ""

    @abstractmethod
    def voices(self) -> list[Voice]: ...

    @abstractmethod
    def synthesize(
        self, text: str, voice: str, speed: float, destination: Path, context: dict
    ) -> Speech:
        """Speak `text` into `destination` (any format FFmpeg can decode).

        `context` has the neighbouring narration ("previous", "next") for providers
        that use it to keep intonation natural across scenes.
        """

    def public(self) -> dict:
        reason = self.unavailable_reason()
        return {
            "id": self.id,
            "label": self.label,
            "available": reason is None,
            "reason": reason,
            "speedRange": list(self.speed_range),
            "defaultSpeed": self.default_speed,
            "voices": [
                {"id": v.id, "label": v.label, "description": v.description}
                for v in (self.voices() if reason is None else [])
            ],
        }
