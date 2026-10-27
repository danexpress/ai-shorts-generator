"""Content-addressed transcript cache: a source file is never transcribed twice.

Files live at MEDIA_DIR/transcripts/<sha256 of source>.<transcriber key>.json, so the
same video uploaded to several projects, retried, or re-rendered reuses one result.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from .service import Progress, Transcriber

logger = logging.getLogger(__name__)


def source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_root(media_dir: Path) -> Path:
    return (media_dir / "transcripts").resolve()


def cache_path(media_dir: Path, digest: str, key: str) -> Path:
    return cache_root(media_dir) / f"{digest}.{key}.json"


def load_or_transcribe(
    source: Path,
    media_dir: Path,
    transcriber: Transcriber,
    on_progress: Progress | None = None,
    digest: str | None = None,
) -> tuple[dict, str, bool]:
    """Return (transcript, source digest, cache hit)."""
    digest = digest or source_digest(source)
    path = cache_path(media_dir, digest, transcriber.key)
    if path.is_file():
        try:
            result = json.loads(path.read_text("utf-8"))
            logger.info("Transcript cache hit for %s (%s)", digest[:12], transcriber.key)
            return result, digest, True
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable transcript cache file %s", path.name)
    started = time.monotonic()
    result = transcriber.transcribe(source, on_progress)
    elapsed = time.monotonic() - started
    duration = result.get("duration") or 0
    logger.info(
        "Transcribed %.1fs of audio in %.1fs (%.2fx realtime, %s, %d segments)",
        duration,
        elapsed,
        elapsed / duration if duration else 0,
        transcriber.key,
        len(result["segments"]),
    )
    result["transcriptionSeconds"] = round(elapsed, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    temporary.replace(path)
    return result, digest, False


def remove_cached(media_dir: Path, digest: str):
    root = cache_root(media_dir)
    for path in root.glob(f"{digest}.*.json"):
        if path.parent == root:
            path.unlink(missing_ok=True)
