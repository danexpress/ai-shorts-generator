"""One FFmpeg pass: background + voice + optional music + burned-in captions.

Output is 1080x1920, 30 fps, H.264 (yuv420p) with AAC stereo at 48 kHz and
+faststart, as long as the voiceover plus a short outro (kept within 60 seconds
when the voice is). The captions ASS and fonts go through the same private
working directory as clip renders (renderer.caption_workdir).
"""

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..captions.presets import FONTS_DIR
from ..renderer import RenderFailed, caption_workdir
from .music import bed_gain, music_filter
from .script import MAX_SECONDS
from .titlecard import TITLE_SECONDS, story_captions, with_title_card
from .visuals import FPS, VisualSource

logger = logging.getLogger(__name__)

OUTRO = 0.8  # seconds after the last word: the music swells and fades, or silence


@dataclass
class StoryRenderPlan:
    background: VisualSource
    voice: Path
    voice_seconds: float
    words: list[dict]
    timeline: list[dict]  # scenes with id, start, end, visual_description, mood
    title: str | None  # the title card text; None for no card
    caption_preset: str | None
    captions: bool = True
    music: Path | None = None


def story_duration(voice_seconds: float) -> float:
    """Voice plus the outro, trimmed so a voice under 60 s never yields a video over it."""
    with_outro = voice_seconds + OUTRO
    if voice_seconds <= MAX_SECONDS:
        return round(min(with_outro, MAX_SECONDS), 3)
    return round(with_outro, 3)


def build_command(ffmpeg: str, plan: StoryRenderPlan, destination: Path, ass_filter: str | None):
    duration = story_duration(plan.voice_seconds)
    inputs = plan.background.inputs()
    voice_index = inputs.count("-i")
    inputs += ["-i", str(plan.voice)]
    graph = [plan.background.filter(0, duration, plan.timeline)]
    graph.append(f"[bg]{ass_filter}[v]" if ass_filter else "[bg]null[v]")
    graph.append(
        f"[{voice_index}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={duration:.3f}[voice]"
    )
    if plan.music:
        inputs += ["-stream_loop", "-1", "-i", str(plan.music)]
        gain = bed_gain(ffmpeg, plan.music)
        graph.append(music_filter(voice_index + 1, gain, plan.voice_seconds, duration))
        graph.append(
            "[voice][music]amix=inputs=2:duration=first:normalize=0:dropout_transition=0,"
            "alimiter=limit=0.89:level=false[a]"
        )
    else:
        graph.append("[voice]anull[a]")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs]
    command += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]"]
    command += ["-t", f"{duration:.3f}", "-r", str(FPS)]
    command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
    command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    command += ["-movflags", "+faststart", str(Path(destination).resolve())]
    return command, duration


def render_story(ffmpeg: str, plan: StoryRenderPlan, destination: Path) -> dict:
    """Render the Short; returns {"durationSec", "captions" snapshot or None, "seconds"}."""
    started = time.monotonic()
    snapshot, ass = None, None
    if plan.captions and plan.words:
        ass, snapshot, preset = story_captions(plan.words, plan.voice_seconds, plan.caption_preset)
        if plan.title:
            ass = with_title_card(ass, plan.title, preset, min(TITLE_SECONDS, plan.voice_seconds))
    workdir, ass_filter = (None, None)
    if ass:
        workdir, ass_filter = caption_workdir(Path(destination).parent, ass, FONTS_DIR)
    command, duration = build_command(ffmpeg, plan, destination, ass_filter)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
            cwd=workdir.name if workdir else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderFailed from exc
    finally:
        if workdir:
            workdir.cleanup()
    if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
        logger.warning("Story render failed: %s", result.stderr[-500:])
        raise RenderFailed
    elapsed = time.monotonic() - started
    logger.info(
        "Rendered story: %.1fs at 1080p (music=%s, captions=%s) in %.1fs",
        duration,
        bool(plan.music),
        bool(ass),
        elapsed,
    )
    return {"durationSec": duration, "captions": snapshot, "seconds": round(elapsed, 3)}
