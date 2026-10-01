"""Check real MP4 probing/cover extraction and distinct provider acceptance rules."""

import subprocess
from dataclasses import replace

import imageio_ffmpeg
import pytest

from app.social.adapters.base import ProviderError
from app.social.adapters.tiktok import TikTokPublisher
from app.social.adapters.x import XPublisher
from app.social.adapters.youtube import YouTubePublisher
from app.social.config import SocialSettings
from app.social.thumbnail import cover_image
from app.social.video import inspect_video


def test_probe_actual_vertical_h264_aac_video_and_cover(tmp_path, monkeypatch):
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    path = tmp_path / "short.mp4"
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=720x1280:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    for fallback in [False, True]:
        if fallback:
            monkeypatch.setattr("app.social.video.shutil.which", lambda _: None)
        info = inspect_video(path, executable)
        assert info["width"] == 720 and info["height"] == 1280
        assert info["video_codec"] == "h264" and info["audio_codec"] == "aac" and info["fps"] == 30
        assert 0.9 <= info["duration"] <= 1.1
    cover = cover_image(path, executable)
    assert cover.startswith(b"\xff\xd8") and cover.endswith(b"\xff\xd9")
    path.write_bytes(b"not a video")
    with pytest.raises(ProviderError, match="MEDIA_REJECTED"):
        inspect_video(path, executable)


def test_video_rules_are_isolated_per_provider():
    settings = replace(
        SocialSettings(),
        clients={p: {"id": "id", "secret": "secret"} for p in ["youtube", "tiktok", "x"]},
    )
    video = {"size": 5 * 1024**3, "duration": 30, "width": 1080, "height": 1920, "fps": 30}
    YouTubePublisher(settings, None).validate_video(video, {"visibility": "private"})
    XPublisher(settings, None).validate_video(video, {})
    creator = {
        "max_duration": 60,
        "privacy_options": ["SELF_ONLY"],
        "comment_disabled": False,
        "duet_disabled": False,
        "stitch_disabled": False,
    }
    with pytest.raises(ProviderError, match="MEDIA_REJECTED"):
        TikTokPublisher(settings, None).validate_video(
            video, {"privacy": "SELF_ONLY", "consent": True}, creator
        )
    with pytest.raises(ProviderError, match="MEDIA_REJECTED"):
        YouTubePublisher(settings, None).validate_video(
            {**video, "size": 1000, "duration": 190}, {"visibility": "private"}
        )
    XPublisher(settings, None).validate_video({**video, "size": 1000, "duration": 190}, {})
