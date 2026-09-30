"""Inspect actual stored MP4 streams, never trust browser-reported video metadata."""

import json
import re
import shutil
import subprocess

from ..renderer import ffmpeg_path
from .adapters.base import ProviderError


def inspect_video(path, configured_ffmpeg=None):
    ffprobe = shutil.which("ffprobe")
    try:
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            data = json.loads(result.stdout)
            video = next(s for s in data["streams"] if s["codec_type"] == "video")
            audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
            n, d = video.get("avg_frame_rate", "0/1").split("/")
            info = {
                "duration": float(data["format"]["duration"]),
                "width": video["width"],
                "height": video["height"],
                "fps": float(n) / float(d),
                "video_codec": video["codec_name"],
                "audio_codec": audio["codec_name"] if audio else None,
                "format": data["format"]["format_name"],
            }
        else:
            result = subprocess.run(
                [
                    ffmpeg_path(configured_ffmpeg),
                    "-hide_banner",
                    "-i",
                    str(path),
                    "-t",
                    "0.001",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            text = result.stderr.split("Output #")[0]
            duration = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
            video = re.search(r"Video: (\w+).*?, (\d+)x(\d+).*?(\d+(?:\.\d+)?) fps", text)
            audio = re.search(r"Audio: (\w+)", text)
            if not duration or not video:
                raise ValueError
            info = {
                "duration": int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]),
                "width": int(video[2]),
                "height": int(video[3]),
                "fps": float(video[4]),
                "video_codec": video[1],
                "audio_codec": audio[1] if audio else None,
                "format": "mp4" if "mp4" in text else "",
            }
        info["size"] = path.stat().st_size
        if (
            "mp4" not in info["format"]
            or info["video_codec"] != "h264"
            or info["audio_codec"] not in {None, "aac"}
            or not info["size"]
            or not 0.5 <= info["duration"]
            or not info["height"]
            or not info["width"]
        ):
            raise ValueError
        return info
    except (
        OSError,
        ValueError,
        KeyError,
        StopIteration,
        ZeroDivisionError,
        subprocess.TimeoutExpired,
    ):
        raise ProviderError("MEDIA_REJECTED") from None
