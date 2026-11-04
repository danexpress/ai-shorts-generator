"""Text-to-speech providers, selected with STORY_TTS_PROVIDER (default "kokoro")."""

from .base import Speech, TTSFailed, TTSProvider, TTSUnavailable, Voice
from .demo import DemoProvider
from .elevenlabs import ElevenLabsProvider
from .kokoro import KokoroProvider

PROVIDERS: dict[str, type[TTSProvider]] = {
    "kokoro": KokoroProvider,
    "elevenlabs": ElevenLabsProvider,
    "demo": DemoProvider,
}


def build_providers(settings) -> dict[str, TTSProvider]:
    return {name: cls(settings) for name, cls in PROVIDERS.items()}


__all__ = [
    "PROVIDERS",
    "Speech",
    "TTSFailed",
    "TTSProvider",
    "TTSUnavailable",
    "Voice",
    "build_providers",
]
