"""FFmpeg-backed rendering of a continuous source interval as a vertical MP4."""

import shutil
import subprocess
from pathlib import Path


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
):
    executable = ffmpeg_path(configured_ffmpeg)
    height = 1920 if resolution == 1080 else 1280
    width = 1080 if resolution == 1080 else 720
    # Fit the full display aspect ratio (including non-square source pixels), then
    # center it on the fixed export canvas. Even dimensions keep H.264 compatible.
    vf = (
        f"scale=w='trunc(min({width},{height}*dar)/2)*2':"
        f"h='trunc(min({height},{width}/dar)/2)*2',setsar=1,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
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
        str(destination),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderFailed from exc
    if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
        raise RenderFailed
