"""Local speech-to-text with word timestamps behind a replaceable interface.

A transcriber returns the canonical transcript document shared by captions and
highlight detection:

    {"language", "duration", "model", "segments": [
        {"start", "end", "text", "words": [{"text", "start", "end", "probability"}]}]}
"""

import logging
import re
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


def build_transcriber(settings) -> Transcriber:
    aligner = None
    if settings.whisper_align == "whisperx":
        from .align import WhisperXAligner

        aligner = WhisperXAligner()
    elif settings.whisper_align:
        raise ValueError(f"Unknown WHISPER_ALIGN value: {settings.whisper_align}")
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
