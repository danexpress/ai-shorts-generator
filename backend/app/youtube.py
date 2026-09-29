"""Restricted YouTube URL validation and local source-video download."""

import re
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from .errors import ApiError
from .media_limits import MAX_SOURCE_BYTES as MAX_BYTES
from .media_limits import MAX_SOURCE_DURATION_SEC as MAX_DURATION
from .media_limits import MIN_SOURCE_DURATION_SEC as MIN_DURATION
from .renderer import ffmpeg_path

VIDEO_ID_RE = re.compile(r"[\w-]{6,15}", flags=re.ASCII)


class InvalidVideoUrl(ValueError):
    pass


class VideoDownloadError(RuntimeError):
    pass


class VideoLimitError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def normalize_youtube_url(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError
        if (
            parsed.hostname.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"}
            and parsed.path == "/watch"
        ):
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.hostname.lower() == "youtu.be":
            video_id = parsed.path.strip("/")
        else:
            raise ValueError
        if not video_id or not VIDEO_ID_RE.fullmatch(video_id):
            raise ValueError
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        return video_id, canonical
    except (ValueError, TypeError):
        raise InvalidVideoUrl from None


def _validate_info(info: dict) -> tuple[float, int]:
    duration = info.get("duration")
    size = info.get("filesize") or info.get("filesize_approx")
    if duration is None or duration <= 0:
        raise VideoDownloadError("Video duration could not be verified.")
    if duration < MIN_DURATION:
        raise VideoLimitError("SOURCE_TOO_SHORT", "Sources must have at least 30 seconds of video.")
    if duration > MAX_DURATION:
        raise VideoLimitError("SOURCE_TOO_LONG", "Sources can be up to 3 hours.")
    if size is not None and size > MAX_BYTES:
        raise VideoLimitError("SOURCE_TOO_LARGE", "Files can be up to 4 GiB.")
    return float(duration), int(size or 0)


def download_youtube(
    url: str,
    destination_dir: Path,
    ffmpeg_binary: str | None = None,
    before_download: Callable[[float], None] | None = None,
) -> dict:
    """Download one verified YouTube video, returning the actual media metadata and file path."""
    _video_id, canonical_url = normalize_youtube_url(url)
    media_dir = destination_dir.resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    work_dir = media_dir / ".downloads" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / "%(id)s.%(ext)s"
    started = time.monotonic()
    observed_size = 0

    def progress_hook(status):
        nonlocal observed_size
        if status.get("status") == "downloading":
            observed_size = max(observed_size, status.get("downloaded_bytes", 0))
            if observed_size > MAX_BYTES:
                raise DownloadError("Video exceeds the 4 GiB download limit")
            if time.monotonic() - started > 2 * 60 * 60:
                raise DownloadError("Video download exceeded its time limit")

    options = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(output),
        "noplaylist": True,
        "no_warnings": True,
        "quiet": True,
        "max_filesize": MAX_BYTES,
        "socket_timeout": 20,
        "retries": 1,
        "extractor_retries": 1,
        "ffmpeg_location": ffmpeg_path(ffmpeg_binary),
        "progress_hooks": [progress_hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(canonical_url, download=False)
            duration, expected_size = _validate_info(info)
            if before_download:
                before_download(duration)
            downloader.download([canonical_url])
    except (VideoLimitError, ApiError):
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        # yt-dlp errors can contain the submitted URL or extractor diagnostics. Keep them server-side.
        shutil.rmtree(work_dir, ignore_errors=True)
        raise VideoDownloadError("The video could not be downloaded from YouTube.") from exc

    files = [
        path
        for path in work_dir.glob(f"{info['id']}.*")
        if path.is_file() and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
    ]
    if not files:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise VideoDownloadError("YouTube did not produce a downloadable video file.")
    source = max(files, key=lambda path: path.stat().st_size)
    actual_size = source.stat().st_size
    if actual_size > MAX_BYTES:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise VideoLimitError("SOURCE_TOO_LARGE", "Files can be up to 4 GiB.")
    duration, _ = _validate_info({**info, "filesize": actual_size})
    extension = source.suffix.lower()
    final_path = source
    return {
        "path": final_path,
        "fileName": re.sub(r"[^\w.-]+", "_", info.get("title") or f"youtube-{info['id']}")[:200]
        + extension,
        "title": str(info.get("title") or f"YouTube {info['id']}")[:255],
        "durationSec": duration,
        "width": int(info.get("width") or 1920),
        "height": int(info.get("height") or 1080),
        "fps": float(info.get("fps") or 30),
        "sizeBytes": actual_size or expected_size,
        "mimeType": {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
            ".mkv": "video/x-matroska",
        }.get(extension, "video/mp4"),
    }
