"""Optional YouTube cover from the approved, server-owned rendered video."""

import subprocess

from ..renderer import ffmpeg_path


def cover_image(path, configured_ffmpeg=None):
    result = subprocess.run(
        [
            ffmpeg_path(configured_ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=720:-2",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    if not result.stdout or len(result.stdout) > 2 * 1024**2:
        raise ValueError("Cover unavailable")
    return result.stdout
