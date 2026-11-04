"""Kokoro-82M, a free local TTS model (Apache-2.0). Install: uv sync --extra kokoro.

The first use downloads the model (about 330 MB) from Hugging Face. Kokoro returns
per-token timestamps for English, so no alignment pass is needed.
"""

import logging
import threading
import time
import wave
from pathlib import Path

from .base import Speech, TTSFailed, TTSProvider, TTSUnavailable, Voice

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
REPO = "hexgrad/Kokoro-82M"
VOICES = [
    Voice("af_heart", "Heart", "American English · female · warm"),
    Voice("af_bella", "Bella", "American English · female · bright"),
    Voice("af_nicole", "Nicole", "American English · female · soft"),
    Voice("af_sarah", "Sarah", "American English · female"),
    Voice("am_michael", "Michael", "American English · male"),
    Voice("am_fenrir", "Fenrir", "American English · male · deep"),
    Voice("am_puck", "Puck", "American English · male · lively"),
    Voice("am_onyx", "Onyx", "American English · male · low"),
    Voice("bf_emma", "Emma", "British English · female"),
    Voice("bf_isabella", "Isabella", "British English · female"),
    Voice("bm_george", "George", "British English · male"),
    Voice("bm_fable", "Fable", "British English · male · storyteller"),
]


def has_kokoro() -> bool:
    try:
        import kokoro  # noqa: F401
    except ImportError:
        return False
    return True


def words_from_tokens(results) -> list[dict]:
    """Kokoro tokens (words and punctuation, per chunk) to words with absolute times."""
    words, offset = [], 0.0
    for result in results:
        for token in result.tokens or []:
            if token.start_ts is None or token.end_ts is None:
                continue
            if any(c.isalnum() for c in token.text):
                words.append(
                    {
                        "text": token.text,
                        "start": round(offset + token.start_ts, 3),
                        "end": round(offset + token.end_ts, 3),
                    }
                )
            elif words:  # punctuation belongs to the word before it
                words[-1]["text"] += token.text
        offset += len(result.audio) / SAMPLE_RATE
    return words


def write_wav(path: Path, chunks):
    import torch

    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for audio in chunks:
            samples = (audio.clamp(-1, 1) * 32767).to(dtype=torch.int16)
            out.writeframes(samples.numpy().tobytes())


class KokoroProvider(TTSProvider):
    id = "kokoro"
    label = "Kokoro (free, runs locally)"
    speed_range = (0.7, 1.4)
    # Kokoro reads about 180-200 wpm at 1.0; 0.8 is close to the planned ~150 wpm.
    default_speed = 0.8

    def __init__(self, settings):
        super().__init__(settings)
        self._pipelines = {}
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return REPO

    def unavailable_reason(self):
        if not has_kokoro():
            return "Kokoro is not installed on the server (uv sync --extra kokoro)."
        return None

    def voices(self):
        return VOICES

    def pipeline(self, voice: str):
        lang = voice[0]  # "a" American English, "b" British English
        with self._lock:
            if lang not in self._pipelines:
                if not has_kokoro():
                    raise TTSUnavailable(self.unavailable_reason())
                from kokoro import KPipeline

                started = time.monotonic()
                self._pipelines[lang] = KPipeline(lang_code=lang, repo_id=REPO)
                logger.info("Loaded Kokoro (%s) in %.1fs", lang, time.monotonic() - started)
            return self._pipelines[lang]

    def synthesize(self, text, voice, speed, destination, context):
        if voice not in {v.id for v in VOICES}:
            raise TTSFailed(f"Unknown Kokoro voice {voice!r}.")
        try:
            # One paragraph: never split on newlines, keep the scene as one utterance.
            results = list(self.pipeline(voice)(text, voice=voice, speed=speed, split_pattern=None))
        except TTSUnavailable:
            raise
        except Exception as exc:  # model download or inference failure
            raise TTSUnavailable(f"Kokoro could not speak this scene: {exc}") from exc
        if not results:
            raise TTSFailed("Kokoro produced no audio for this scene.")
        write_wav(destination, [r.audio for r in results])
        return Speech(words_from_tokens(results), len(text))
