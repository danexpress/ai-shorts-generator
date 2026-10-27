"""FFmpeg-backed rendering of a continuous source interval as a vertical MP4."""

import logging
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


def ffmpeg_path(configured: str | None = None) -> str:
    if configured:
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RendererUnavailable from exc


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
    executable = ffmpeg_path(configured_ffmpeg)
    height = 1920 if resolution == 1080 else 1280
    width = 1080 if resolution == 1080 else 720
    if framing == "crop":
        # Square the pixels first so the crop keeps the true display aspect ratio.
        vf = (
            "scale=w='trunc(iw*sar/2)*2':h='trunc(ih/2)*2',setsar=1,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1"
        )
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
        # Relative names inside a private directory avoid filtergraph path escaping.
        workdir = tempfile.TemporaryDirectory(prefix="captions-", dir=destination.parent)
        work = Path(workdir.name)
        (work / "captions.ass").write_text(captions_ass, "utf-8")
        if fonts_dir:
            shutil.copytree(fonts_dir, work / "fonts", ignore=shutil.ignore_patterns("*.txt"))
        vf += ",ass=captions.ass" + (":fontsdir=fonts" if fonts_dir else "")
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
