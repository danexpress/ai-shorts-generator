"""FFmpeg-backed rendering of a continuous source interval as a vertical MP4."""

import functools
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class RendererUnavailable(RuntimeError):
    pass


class RenderFailed(RuntimeError):
    pass


def _bundled_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


@functools.lru_cache(maxsize=8)
def has_filter(executable: str, name: str) -> bool:
    """Whether this FFmpeg build includes a filter (e.g. "ass" needs libass)."""
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", result.stdout, re.MULTILINE) is not None


def ffmpeg_path(configured: str | None = None, need_subtitles: bool = False) -> str:
    """FFMPEG_BIN if set, else the bundled build (known to include libass), else PATH.

    With need_subtitles, a candidate without the libass "ass" filter is skipped: some
    package-manager builds (e.g. Homebrew's) omit it and could not burn in captions.
    """
    if configured:
        if need_subtitles and not has_filter(configured, "ass"):
            raise RendererUnavailable(
                f"FFMPEG_BIN ({configured}) has no subtitle support; use a build with libass."
            )
        return configured
    candidates = [c for c in (_bundled_ffmpeg(), shutil.which("ffmpeg")) if c]
    for candidate in candidates:
        if not need_subtitles or has_filter(candidate, "ass"):
            return candidate
    raise RendererUnavailable("No FFmpeg with subtitle support (libass) was found.")


def fill_filter(width: int, height: int) -> str:
    """Scale and center-crop any video to fill a width x height canvas."""
    # Square the pixels first so the crop keeps the true display aspect ratio.
    return (
        "scale=w='trunc(iw*sar/2)*2':h='trunc(ih/2)*2',setsar=1,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def caption_workdir(parent: Path, captions_ass: str, fonts_dir: Path | None = None):
    """A private directory holding captions.ass (and the fonts), plus the `ass` filter
    that reads them. Run FFmpeg with cwd set to it: relative names inside a private
    directory avoid filtergraph path escaping. The caller cleans the directory up."""
    workdir = tempfile.TemporaryDirectory(prefix="captions-", dir=parent)
    work = Path(workdir.name)
    (work / "captions.ass").write_text(captions_ass, "utf-8")
    if fonts_dir:
        shutil.copytree(fonts_dir, work / "fonts", ignore=shutil.ignore_patterns("*.txt"))
    return workdir, "ass=captions.ass" + (":fontsdir=fonts" if fonts_dir else "")


def render_short(
    source: Path,
    destination: Path,
    start: float,
    duration: float,
    resolution: int,
    configured_ffmpeg: str | None = None,
    framing: str = "fit",
    captions_ass: str | None = None,
    fonts_dir: Path | None = None,
):
    """Render [start, start+duration) as a vertical MP4.

    framing "fit" keeps the whole frame on black padding; "crop" fills the 9:16
    canvas with a centred crop. `captions_ass` is burned in with libass using only
    the fonts in `fonts_dir`.
    """
    executable = ffmpeg_path(configured_ffmpeg, need_subtitles=bool(captions_ass))
    height = 1920 if resolution == 1080 else 1280
    width = 1080 if resolution == 1080 else 720
    if framing == "crop":
        vf = fill_filter(width, height)
    else:
        # Fit the full display aspect ratio (including non-square source pixels), then
        # center it on the fixed export canvas. Even dimensions keep H.264 compatible.
        vf = (
            f"scale=w='trunc(min({width},{height}*dar)/2)*2':"
            f"h='trunc(min({height},{width}/dar)/2)*2',setsar=1,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    workdir = None
    if captions_ass:
        workdir, ass_filter = caption_workdir(destination.parent, captions_ass, fonts_dir)
        vf += "," + ass_filter
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(Path(source).resolve()),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(Path(destination).resolve()),
    ]
    started = time.monotonic()
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
        logger.warning("FFmpeg render failed: %s", result.stderr[-500:])
        raise RenderFailed
    logger.info(
        "Rendered %.1fs at %dp (%s, captions=%s) in %.1fs",
        duration,
        resolution,
        framing,
        bool(captions_ass),
        time.monotonic() - started,
    )
