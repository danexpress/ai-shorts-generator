"""Word timings for the voiceover, always expressed in the script's own words.

Sources, best first:
1. the TTS provider's timestamps (Kokoro tokens, ElevenLabs characters);
2. WhisperX forced alignment of the known narration (WHISPER_ALIGN=whisperx);
3. Whisper transcription of the generated audio;
4. an estimate that spreads the words over the audio by length.

Whatever the source, its words are matched to the script's words (so "3" spoken as
"three", or a misheard name, never changes the captions) and any unmatched script
words are spread over the gaps between matched ones.
"""

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN = re.compile(r"\S+")


def script_words(narration: str) -> list[str]:
    """The narration's spoken words, punctuation attached as written."""
    return [t for t in TOKEN.findall(narration) if any(c.isalnum() for c in t)]


def _key(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def transfer(words: list[str], timed: list[dict] | None, duration: float) -> list[dict]:
    """Give each script word a start/end from `timed` words ({text, start, end})."""
    times: list[tuple[float, float] | None] = [None] * len(words)
    timed = [w for w in timed or [] if w["end"] >= w["start"]]
    matcher = SequenceMatcher(
        None, [_key(w) for w in words], [_key(w["text"]) for w in timed], autojunk=False
    )
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            word = timed[block.b + offset]
            times[block.a + offset] = (word["start"], word["end"])
    index = 0
    while index < len(words):  # spread each run of unmatched words over its gap
        if times[index] is not None:
            index += 1
            continue
        run_end = index
        while run_end < len(words) and times[run_end] is None:
            run_end += 1
        left = times[index - 1][1] if index else 0.0
        right = times[run_end][0] if run_end < len(words) else duration
        right = max(right, left)
        weights = [max(1, len(_key(w))) for w in words[index:run_end]]
        total, clock = sum(weights), left
        for position, weight in zip(range(index, run_end), weights):
            span = (right - left) * weight / total
            times[position] = (clock, clock + span)
            clock += span
        index = run_end
    return [
        {"text": word, "start": round(start, 3), "end": round(max(start, end), 3)}
        for word, (start, end) in zip(words, times)
    ]


def forced_alignment(audio: Path, narration: str, duration: float) -> list[dict] | None:
    """WhisperX alignment of the known text, or None when WhisperX is not installed."""
    try:
        import whisperx
    except ImportError:
        return None
    model, metadata = whisperx.load_align_model(language_code="en", device="cpu")
    aligned = whisperx.align(
        [{"start": 0.0, "end": duration, "text": narration}],
        model,
        metadata,
        whisperx.load_audio(str(audio)),
        "cpu",
        return_char_alignments=False,
    )
    words = [w for segment in aligned["segments"] for w in segment.get("words", [])]
    return [
        {"text": w["word"], "start": w["start"], "end": w["end"]}
        for w in words
        if "start" in w and "end" in w
    ]


def transcribed(audio: Path, transcriber) -> list[dict] | None:
    if transcriber is None:
        return None
    result = transcriber.transcribe(audio)
    return [
        {"text": w["text"], "start": w["start"], "end": w["end"]}
        for segment in result["segments"]
        for w in segment["words"]
    ]


def scene_timings(
    narration: str,
    audio: Path,
    duration: float,
    provided: list[dict] | None,
    settings,
    transcriber_factory=None,
) -> tuple[list[dict], str]:
    """(script words with times, source name) for one scene's audio file."""
    words = script_words(narration)
    if provided:
        return transfer(words, provided, duration), "provider"
    attempts = []
    if getattr(settings, "whisper_align", "") == "whisperx":
        attempts.append(("forced-alignment", lambda: forced_alignment(audio, narration, duration)))
    if transcriber_factory:
        attempts.append(("transcription", lambda: transcribed(audio, transcriber_factory())))
    for source, attempt in attempts:
        try:
            timed = attempt()
        except Exception:
            logger.exception("Story voice: %s failed, trying the next timing source", source)
            continue
        if timed:
            return transfer(words, timed, duration), source
    return transfer(words, None, duration), "estimate"
