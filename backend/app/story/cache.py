"""Content-addressed cache for Story Mode results (scripts now, voice audio next).

Entries live at MEDIA_DIR/story-cache/<kind>/<sha256 of the inputs>.json. The key
covers everything that changes the output (prompt version, model, request), so a
re-render or an unchanged retry never pays for the same LLM or TTS call twice.

Every hit refreshes an entry's modification time; entries unused for
STORY_CACHE_DAYS (default 30) are pruned by the processing worker.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def digest(key) -> str:
    canonical = json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def cache_root(media_dir: Path) -> Path:
    return (Path(media_dir) / "story-cache").resolve()


def path_for(media_dir: Path, kind: str, key) -> Path:
    return cache_root(media_dir) / kind / f"{digest(key)}.json"


def load(media_dir: Path, kind: str, key) -> dict | None:
    path = path_for(media_dir, kind, key)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text("utf-8"))
        touch(path)
        return value
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable story cache file %s/%s", kind, path.name)
        return None


def save(media_dir: Path, kind: str, key, value: dict):
    path = path_for(media_dir, kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    temporary.replace(path)


def file_path(media_dir: Path, kind: str, key, suffix: str) -> Path:
    """A content-addressed binary entry (e.g. scene audio) next to its JSON metadata."""
    return cache_root(media_dir) / kind / f"{digest(key)}{suffix}"


def touch(path: Path):
    """Mark an entry as used so pruning keeps it."""
    try:
        os.utime(path)
    except OSError:
        pass


def prune(media_dir: Path, max_age_days: float, now: float | None = None) -> int:
    """Delete cache entries not used for `max_age_days`. Returns how many were removed."""
    root = cache_root(media_dir)
    if not root.is_dir() or max_age_days <= 0:
        return 0
    cutoff = (now or time.time()) - max_age_days * 86_400
    removed = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Pruned %d story cache file(s) unused for %g days", removed, max_age_days)
    return removed
