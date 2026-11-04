"""Content-addressed cache for Story Mode results (scripts now, voice audio next).

Entries live at MEDIA_DIR/story-cache/<kind>/<sha256 of the inputs>.json. The key
covers everything that changes the output (prompt version, model, request), so a
re-render or an unchanged retry never pays for the same LLM or TTS call twice.
"""

import hashlib
import json
import logging
import os
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
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable story cache file %s/%s", kind, path.name)
        return None


def save(media_dir: Path, kind: str, key, value: dict):
    path = path_for(media_dir, kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
    temporary.replace(path)
