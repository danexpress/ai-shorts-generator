"""Generate a story's voiceover: one TTS request per scene, joined with a pause.

Each scene's speech (trimmed 48 kHz audio plus word timings) is cached by a hash of
provider, model, voice, speed and text. Editing one scene therefore re-speaks only
that scene, and re-rendering an unchanged story costs nothing. The joined audio is
loudness-normalized to about -14 LUFS.
"""

import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import audio, cache
from .script import MAX_SECONDS, SCENE_PAUSE
from .timing import scene_timings
from .tts import TTSFailed, TTSProvider

logger = logging.getLogger(__name__)

VOICE_VERSION = "voice-v1"


@dataclass(frozen=True)
class VoiceRequest:
    provider: str
    voice: str
    speed: float


def scene_key(provider: TTSProvider, request: VoiceRequest, text: str) -> dict:
    return {
        "version": VOICE_VERSION,
        "provider": provider.id,
        "providerVersion": provider.version,
        "model": provider.model,
        "voice": request.voice,
        "speed": request.speed,
        "text": text,
    }


def speak_scene(provider, request, scenes, index, settings, ffmpeg, transcriber_factory):
    """(pcm, words relative to the scene, meta) for scene `index`, cached."""
    text = scenes[index]["narration"]
    key = scene_key(provider, request, text)
    meta = cache.load(settings.media_dir, "tts", key)
    wav = cache.file_path(settings.media_dir, "tts", key, ".wav")
    if meta and wav.is_file():
        cache.touch(wav)
        return audio.read_wav(wav), meta, True
    context = {
        "previous": scenes[index - 1]["narration"] if index else "",
        "next": scenes[index + 1]["narration"] if index + 1 < len(scenes) else "",
    }
    wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=wav.parent) as work:
        raw = Path(work) / "speech"
        started = time.monotonic()
        speech = provider.synthesize(text, request.voice, request.speed, raw, context)
        synthesis = time.monotonic() - started
        pcm, cut = audio.trim(audio.decode(ffmpeg, raw))
        if not pcm:
            raise TTSFailed("The voice produced no audio for a scene.")
        duration = audio.seconds(pcm)
        provided = (
            [
                {**w, "start": max(0.0, w["start"] - cut), "end": max(0.0, w["end"] - cut)}
                for w in speech.words
            ]
            if speech.words
            else None
        )
        trimmed = Path(work) / "trimmed.wav"
        audio.write_wav(trimmed, pcm)
        words, source = scene_timings(
            text, trimmed, duration, provided, settings, transcriber_factory
        )
        trimmed.replace(wav)
    meta = {
        "words": words,
        "timingSource": source,
        "characters": speech.characters,
        "synthesisSeconds": round(synthesis, 3),
        "duration": round(duration, 3),
    }
    cache.save(settings.media_dir, "tts", key, meta)
    return pcm, meta, False


def generate_voice(
    script: dict,
    request: VoiceRequest,
    provider: TTSProvider,
    settings,
    ffmpeg: str,
    destination: Path,
    on_progress=None,
    transcriber_factory=None,
) -> dict:
    """Speak every scene, join them and write the normalized voiceover to
    `destination`. Returns the voice document: timings, cost and loudness."""
    started = time.monotonic()
    scenes = script["scenes"]
    parts, metas, billed, hits = [], [], 0, 0
    for index in range(len(scenes)):
        pcm, meta, hit = speak_scene(
            provider, request, scenes, index, settings, ffmpeg, transcriber_factory
        )
        parts.append(pcm)
        metas.append(meta)
        hits += hit
        billed += 0 if hit else meta["characters"]
        if on_progress:
            on_progress((index + 1) / (len(scenes) + 1))
    joined, spans = audio.join(parts, SCENE_PAUSE)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as work:
        raw = Path(work) / "joined.wav"
        audio.write_wav(raw, joined)
        loudness = audio.normalize(ffmpeg, raw, Path(work) / "voice.wav")
        (Path(work) / "voice.wav").replace(destination)
    words = [
        {
            "text": word["text"],
            "start": round(span[0] + word["start"], 3),
            "end": round(span[0] + word["end"], 3),
            "scene": scene["id"],
        }
        for scene, span, meta in zip(scenes, spans, metas)
        for word in meta["words"]
    ]
    duration = round(audio.seconds(joined), 3)
    cost = billed * provider.cost_per_character
    sources = sorted({m["timingSource"] for m in metas})
    logger.info(
        "Story voice: %d scene(s), %.1fs of audio in %.1fs (%s/%s, %d cached), "
        "%d characters billed, $%.4f, timings from %s, %s LUFS",
        len(scenes),
        duration,
        time.monotonic() - started,
        provider.id,
        request.voice,
        hits,
        billed,
        cost,
        "+".join(sources),
        loudness["outputLufs"],
    )
    return {
        "provider": provider.id,
        "voice": request.voice,
        "speed": request.speed,
        "durationSec": duration,
        "tooLong": duration > MAX_SECONDS,
        "scenes": [
            {"id": scene["id"], "start": span[0], "end": span[1]}
            for scene, span in zip(scenes, spans)
        ],
        "words": words,
        "timingSources": sources,
        "characters": sum(m["characters"] for m in metas),
        "billedCharacters": billed,
        "costUsd": round(cost, 6),
        "cachedScenes": hits,
        "loudness": loudness,
        "seconds": round(time.monotonic() - started, 3),
    }
