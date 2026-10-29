"""Local speech-to-text with word timestamps behind a replaceable interface.

A transcriber returns the canonical transcript document shared by captions and
highlight detection:

    {"language", "duration", "model", "segments": [
        {"start", "end", "text", "words": [{"text", "start", "end", "probability"}]}]}
"""

import itertools
import logging
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

Progress = Callable[[float], None]


class TranscriptionError(RuntimeError):
    pass


class Transcriber(Protocol):
    # Identifies model and options in the transcript cache key.
    key: str

    def transcribe(self, source: Path, on_progress: Progress | None = None) -> dict: ...


def pick_device(device: str = "auto", compute_type: str = "auto") -> tuple[str, str]:
    """Use CUDA when available, otherwise CPU. CTranslate2 has no Apple GPU backend."""
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except (ImportError, RuntimeError):
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def keep_segment(text: str, no_speech_prob: float, avg_logprob: float, compression: float) -> bool:
    """Drop segments Whisper typically hallucinates over music, noise or silence."""
    if not re.search(r"\w", text):
        return False  # "♪", "...", and other symbol-only output
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return False
    return compression <= 2.4  # highly repetitive output is a decoding loop


class FasterWhisperTranscriber:
    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: str = "",
        aligner=None,
    ):
        self.model_size = model_size
        self.device, self.compute_type = pick_device(device, compute_type)
        self.language = language or None
        self.aligner = aligner
        self.key = f"faster-whisper-{model_size}-{language or 'auto'}" + (
            f"-{aligner.key}" if aligner else ""
        )
        self._model = None

    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            started = time.monotonic()
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
            logger.info(
                "Loaded Whisper %s on %s/%s in %.1fs",
                self.model_size,
                self.device,
                self.compute_type,
                time.monotonic() - started,
            )
        return self._model

    def transcribe(self, source: Path, on_progress: Progress | None = None) -> dict:
        try:
            segments, info = self.model().transcribe(
                str(source),
                language=self.language,
                word_timestamps=True,
                vad_filter=True,
                # Avoids repetition loops carrying over on long podcast audio.
                condition_on_previous_text=False,
            )
            kept = []
            # `segments` is lazy: decoding happens while iterating.
            for segment in segments:
                if on_progress and info.duration:
                    on_progress(min(1.0, segment.end / info.duration))
                if not keep_segment(
                    segment.text,
                    segment.no_speech_prob,
                    segment.avg_logprob,
                    segment.compression_ratio,
                ):
                    continue
                words = [
                    {
                        "text": word.word.strip(),
                        "start": round(float(word.start), 3),
                        "end": round(float(word.end), 3),
                        "probability": round(float(word.probability), 3),
                    }
                    for word in segment.words or []
                    if word.word.strip()
                ]
                if words:
                    kept.append(
                        {
                            "start": words[0]["start"],
                            "end": words[-1]["end"],
                            "text": segment.text.strip(),
                            "words": words,
                        }
                    )
        except Exception as exc:  # decoder, model download or runtime failures
            raise TranscriptionError(str(exc)) from exc
        result = {
            "language": info.language,
            "duration": round(float(info.duration), 3),
            "model": self.key,
            "segments": kept,
        }
        if self.aligner and kept:
            result = self.aligner.align(source, result)
        return result


SAMPLE_RATE = 16_000
# Whisper model names -> MLX conversions on Hugging Face (mlx-community).
MLX_MODELS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}


def mlx_repo(model: str) -> str:
    if "/" in model:
        return model  # an explicit Hugging Face repo
    if model.endswith(".en"):
        return f"mlx-community/whisper-{model}-mlx"
    return MLX_MODELS.get(model, f"mlx-community/whisper-{model}-mlx")


def decode_audio(source: Path, ffmpeg: str):
    """The first audio track as 16 kHz mono float32 samples."""
    import numpy as np

    result = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(source), "-map", "0:a:0"]
        + ["-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
        timeout=3600,
        check=False,
    )
    if result.returncode != 0:
        raise TranscriptionError("The audio track could not be decoded.")
    return np.frombuffer(result.stdout, np.int16).astype(np.float32) / 32768.0


def split_points(samples, chunk_seconds: float = 300, search_seconds: float = 10):
    """(start, end) sample ranges of about chunk_seconds, cut at the quietest 100 ms
    within search_seconds of each boundary so a cut never lands inside a word."""
    total = len(samples)
    step, search, frame = (
        int(chunk_seconds * SAMPLE_RATE),
        int(search_seconds * SAMPLE_RATE),
        SAMPLE_RATE // 10,
    )
    bounds, target = [0], int(chunk_seconds * SAMPLE_RATE)
    # Leave a final chunk of at least 30 s rather than a fragment.
    while target < total - 30 * SAMPLE_RATE:
        low, high = max(bounds[-1] + frame, target - search), min(total, target + search)
        window = samples[low:high]
        frames = len(window) // frame
        energy = (window[: frames * frame].reshape(frames, frame) ** 2).mean(axis=1)
        cut = low + int(energy.argmin()) * frame + frame // 2
        bounds.append(cut)
        target = cut + step
    bounds.append(total)
    return [(a, b) for a, b in itertools.pairwise(bounds) if b > a]


class MLXWhisperTranscriber:
    """Whisper on the Apple-silicon GPU (MLX). Roughly 35x realtime with `small` on an
    M4 Pro, versus about 0.5x for faster-whisper on its CPU."""

    def __init__(
        self, model_size="small", language="", ffmpeg=None, aligner=None, chunk_seconds=300
    ):
        self.repo = mlx_repo(model_size)
        self.language = language or None
        self.ffmpeg, self.aligner, self.chunk_seconds = ffmpeg, aligner, chunk_seconds
        self.key = f"mlx-whisper-{model_size}-{language or 'auto'}" + (
            f"-{aligner.key}" if aligner else ""
        )

    def transcribe(self, source: Path, on_progress: Progress | None = None) -> dict:
        try:
            import mlx_whisper

            from ..renderer import ffmpeg_path

            samples = decode_audio(source, self.ffmpeg or ffmpeg_path())
        except TranscriptionError:
            raise
        except Exception as exc:  # missing package/FFmpeg, unreadable file
            raise TranscriptionError(str(exc)) from exc
        duration = len(samples) / SAMPLE_RATE
        language, kept = self.language, []
        for start, end in split_points(samples, self.chunk_seconds):
            offset = start / SAMPLE_RATE
            try:
                out = mlx_whisper.transcribe(
                    samples[start:end],
                    path_or_hf_repo=self.repo,
                    language=language,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                    # Skips text Whisper invents over long silences.
                    hallucination_silence_threshold=2.0,
                    verbose=None,
                )
            except Exception as exc:  # model download or runtime failures
                raise TranscriptionError(str(exc)) from exc
            # Detect the language once, then keep it so chunks stay consistent.
            language = language or out.get("language")
            for segment in out.get("segments", []):
                if not keep_segment(
                    segment["text"],
                    segment.get("no_speech_prob", 0.0),
                    segment.get("avg_logprob", 0.0),
                    segment.get("compression_ratio", 0.0),
                ):
                    continue
                words = [
                    {
                        "text": word["word"].strip(),
                        "start": round(float(word["start"]) + offset, 3),
                        "end": round(float(word["end"]) + offset, 3),
                        "probability": round(float(word.get("probability", 0.0)), 3),
                    }
                    for word in segment.get("words", [])
                    if word["word"].strip()
                ]
                if words:
                    kept.append(
                        {
                            "start": words[0]["start"],
                            "end": words[-1]["end"],
                            "text": segment["text"].strip(),
                            "words": words,
                        }
                    )
            # Outside the try: the worker's heartbeat may abandon a canceled job here.
            if on_progress and duration:
                on_progress(min(1.0, end / SAMPLE_RATE / duration))
        result = {
            "language": language or "en",
            "duration": round(duration, 3),
            "model": self.key,
            "segments": kept,
        }
        if self.aligner and kept:
            result = self.aligner.align(source, result)
        return result


def mlx_available() -> bool:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def build_transcriber(settings) -> Transcriber:
    aligner = None
    if settings.whisper_align == "whisperx":
        from .align import WhisperXAligner

        aligner = WhisperXAligner()
    elif settings.whisper_align:
        raise ValueError(f"Unknown WHISPER_ALIGN value: {settings.whisper_align}")
    backend = settings.whisper_backend
    if backend not in {"auto", "mlx", "faster-whisper"}:
        raise ValueError(f"Unknown WHISPER_BACKEND value: {backend}")
    if backend == "mlx" or (backend == "auto" and mlx_available()):
        from ..renderer import ffmpeg_path

        return MLXWhisperTranscriber(
            settings.whisper_model,
            settings.whisper_language,
            ffmpeg_path(settings.ffmpeg_binary),
            aligner,
        )
    return FasterWhisperTranscriber(
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.whisper_language,
        aligner,
    )


def to_segments(result: dict) -> list[dict]:
    """Convert the canonical transcript to the stored/API segment format (w/s/e words)."""
    return [
        {
            "id": f"seg_{index}",
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"],
            "timing": "word",
            "words": [
                {"w": w["text"], "s": w["start"], "e": w["end"], "p": w.get("probability")}
                for w in segment["words"]
            ],
        }
        for index, segment in enumerate(result["segments"])
    ]
