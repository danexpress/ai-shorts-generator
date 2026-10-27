"""Optional WhisperX forced alignment for tighter word timing.

Install with `uv sync --extra align` and set WHISPER_ALIGN=whisperx. Words WhisperX
cannot align (numbers, symbols) keep their faster-whisper timing.
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class WhisperXAligner:
    key = "whisperx"

    def __init__(self, device: str | None = None):
        try:
            import torch
            import whisperx  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "WHISPER_ALIGN=whisperx requires the optional dependency: uv sync --extra align"
            ) from exc
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._models = {}

    def align(self, source: Path, result: dict) -> dict:
        import whisperx

        started = time.monotonic()
        language = result["language"]
        if language not in self._models:
            self._models[language] = whisperx.load_align_model(
                language_code=language, device=self.device
            )
        model, metadata = self._models[language]
        audio = whisperx.load_audio(str(source))
        segments = []
        for segment in result["segments"]:
            aligned = whisperx.align(
                [{"start": segment["start"], "end": segment["end"], "text": segment["text"]}],
                model,
                metadata,
                audio,
                self.device,
                return_char_alignments=False,
            )
            words = [w for seg in aligned["segments"] for w in seg.get("words", [])]
            # Keep the original words unless the alignment covers every one of them.
            if len(words) == len(segment["words"]):
                segment = {
                    **segment,
                    "words": [
                        {
                            **original,
                            "start": round(new.get("start", original["start"]), 3),
                            "end": round(new.get("end", original["end"]), 3),
                        }
                        for original, new in zip(segment["words"], words)
                    ],
                }
                segment["start"] = segment["words"][0]["start"]
                segment["end"] = segment["words"][-1]["end"]
            segments.append(segment)
        logger.info("WhisperX alignment took %.1fs", time.monotonic() - started)
        return {**result, "segments": segments}
