#!/usr/bin/env python3
"""autopost: title new clips and upload them to your own YouTube channel.

Clips land in ./inbox/. Each one is hashed, probed, given metadata (from a
sidecar or from Claude), uploaded with the YouTube Data API v3, recorded in
SQLite, and moved to ./posted/ or ./failed/.

Only post clips you own or are licensed to use. This tool never downloads or
scrapes anyone's videos.

Commands:
    auth      OAuth desktop flow; saves token.json
    run       process the inbox (--once for cron, --loop for a service)
    enqueue   copy a clip (and its sidecars) into the inbox atomically
    status    show limits, queue and recent posts
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
import logging.handlers
import mimetypes
import os
import random
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("autopost")

VIDEO_EXTS = {".mp4", ".mov", ".webm"}
DEFAULT_MODEL = "claude-opus-5-5"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHUNK_SIZE = 8 * 1024 * 1024
MAX_ATTEMPTS = 3
MAX_CHUNK_RETRIES = 10
RETRIABLE_STATUS = {500, 502, 503, 504}
# YouTube's daily quota resets at midnight Pacific Time.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
QUOTA_STOP_REASONS = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
SHORTS_MAX_SECONDS = 60

# Models that take output_config.effort and server-side refusal fallbacks.
EFFORT_MODELS = {
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-fable-5-2", "claude-fable-5-1", "claude-fable-5",
    "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5",
}
FALLBACK_MODELS = {
    "claude-opus-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1",
    "claude-sonnet-5-5",
}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class Config:
    home: Path
    privacy: str = "private"
    synthetic: bool = True
    category: str = "22"  # People & Blogs
    max_per_day: int = 3
    min_gap_hours: float = 4.0
    settle_seconds: float = 10.0
    publish_at: dt.datetime | None = None
    publish_delay_hours: float | None = None
    dry_run: bool = False
    model: str = field(default_factory=lambda: os.environ.get("AUTOPOST_MODEL", DEFAULT_MODEL))

    @property
    def inbox(self) -> Path: return self.home / "inbox"
    @property
    def posted(self) -> Path: return self.home / "posted"
    @property
    def failed(self) -> Path: return self.home / "failed"
    @property
    def duplicates(self) -> Path: return self.home / "duplicates"
    @property
    def db_path(self) -> Path: return self.home / "autopost.db"
    @property
    def log_path(self) -> Path: return self.home / "autopost.log"
    @property
    def lock_path(self) -> Path: return self.home / "autopost.lock"

    @property
    def client_secrets(self) -> Path:
        return Path(os.environ.get("YOUTUBE_CLIENT_SECRETS", self.home / "client_secret.json"))

    @property
    def token_path(self) -> Path:
        return Path(os.environ.get("YOUTUBE_TOKEN_FILE", self.home / "token.json"))


def load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from a git-ignored .env without overriding the environment."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip("'\""))


def setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(fmt)
    root.addHandler(out)
    cfg.home.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(cfg.log_path, maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    for noisy in ("googleapiclient.discovery_cache", "httpx", "httpx2", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.astimezone()  # naive means local time
    return t.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    sha256        TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    status        TEXT NOT NULL,          -- pending | posted | failed | rejected
    attempts      INTEGER NOT NULL DEFAULT 0,
    video_id      TEXT,
    title         TEXT,
    privacy       TEXT,
    publish_at    TEXT,
    duration      REAL,
    width         INTEGER,
    height        INTEGER,
    metadata_json TEXT,
    metadata_source TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    posted_at     TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256  TEXT,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    message TEXT
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get(self, sha: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clips WHERE sha256 = ?", (sha,)).fetchone()

    def upsert(self, sha: str, filename: str, **fields: Any) -> None:
        now = iso(utcnow())
        if self.get(sha) is None:
            self.conn.execute(
                "INSERT INTO clips (sha256, filename, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
                (sha, filename, now, now),
            )
        fields.update(filename=filename, updated_at=now)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE clips SET {cols} WHERE sha256 = ?", (*fields.values(), sha))
        self.conn.commit()

    def event(self, sha: str | None, kind: str, message: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (sha256, at, kind, message) VALUES (?, ?, ?, ?)",
            (sha, iso(utcnow()), kind, message[:4000]),
        )
        self.conn.commit()

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM state WHERE key = ?", (key,))
        else:
            self.conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        self.conn.commit()

    def posted_since(self, since: dt.datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM clips WHERE status = 'posted' AND posted_at >= ?", (iso(since),)
        ).fetchone()
        return row["n"]

    def last_posted_at(self) -> dt.datetime | None:
        row = self.conn.execute("SELECT MAX(posted_at) AS t FROM clips WHERE status = 'posted'").fetchone()
        return parse_time(row["t"]) if row and row["t"] else None


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


def quota_day_start(now: dt.datetime) -> dt.datetime:
    local = now.astimezone(QUOTA_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)


def next_quota_reset(now: dt.datetime) -> dt.datetime:
    local = now.astimezone(QUOTA_TZ)
    tomorrow = (local + dt.timedelta(days=1)).date()
    reset = dt.datetime.combine(tomorrow, dt.time(0, 5), tzinfo=QUOTA_TZ)
    return reset.astimezone(dt.timezone.utc)


def upload_blocked(cfg: Config, db: DB, now: dt.datetime) -> str | None:
    """Return why no upload may start now, or None when one may."""
    paused = db.get_state("paused_until")
    if paused:
        until = parse_time(paused)
        if now < until:
            return f"paused after a YouTube quota error until {iso(until)}"
        db.set_state("paused_until", None)
    today = db.posted_since(quota_day_start(now))
    if today >= cfg.max_per_day:
        return f"daily limit reached ({today}/{cfg.max_per_day}); resets {iso(next_quota_reset(now))}"
    last = db.last_posted_at()
    if last is not None:
        ready = last + dt.timedelta(hours=cfg.min_gap_hours)
        if now < ready:
            return f"minimum gap of {cfg.min_gap_hours:g}h not reached; next upload after {iso(ready)}"
    return None


# --------------------------------------------------------------------------
# Inbox, sidecars and probing
# --------------------------------------------------------------------------


@dataclass
class Sidecars:
    json_path: Path | None = None
    srt_path: Path | None = None
    txt_path: Path | None = None
    thumb_path: Path | None = None

    def paths(self) -> list[Path]:
        return [p for p in (self.json_path, self.srt_path, self.txt_path, self.thumb_path) if p]


def find_sidecars(clip: Path) -> Sidecars:
    def first(*names: str) -> Path | None:
        for name in names:
            p = clip.with_name(name)
            if p.is_file():
                return p
        return None

    stem = clip.stem
    return Sidecars(
        json_path=first(f"{stem}.json"),
        srt_path=first(f"{stem}.srt"),
        txt_path=first(f"{stem}.narration.txt", f"{stem}.txt"),
        thumb_path=first(f"{stem}.jpg", f"{stem}.jpeg", f"{stem}.png"),
    )


def scan_inbox(cfg: Config) -> list[Path]:
    """Finished clips in the inbox, oldest first. Hidden and still-changing files are skipped."""
    if not cfg.inbox.is_dir():
        return []
    now = time.time()
    clips = []
    for p in cfg.inbox.iterdir():
        if p.name.startswith(".") or not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        if now - p.stat().st_mtime < cfg.settle_seconds:
            log.info("%s was modified moments ago; waiting for it to settle", p.name)
            continue
        clips.append(p)
    return sorted(clips, key=lambda p: (p.stat().st_mtime, p.name))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class Probe:
    duration: float
    width: int
    height: int


class ProbeError(Exception):
    pass


def probe(path: Path) -> Probe:
    if path.stat().st_size == 0:
        raise ProbeError("file is empty")
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffprobe is not installed (install ffmpeg)") from e
    if out.returncode != 0:
        raise ProbeError(f"ffprobe could not read the file: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout or "{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no video stream")
    duration = info.get("format", {}).get("duration") or video.get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        raise ProbeError("unknown duration") from None
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if duration <= 0 or width <= 0 or height <= 0:
        raise ProbeError(f"invalid stream (duration={duration}, {width}x{height})")
    return Probe(duration, width, height)


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def srt_to_text(srt: str) -> str:
    lines = []
    for line in srt.splitlines():
        s = line.strip()
        if not s or s.isdigit() or "-->" in s:
            continue
        lines.append(re.sub(r"<[^>]+>", "", s))
    return " ".join(lines)


def clean_text(s: Any) -> str:
    s = str(s or "").replace("<", "").replace(">", "")
    return "".join(ch for ch in s if ch in "\n\t" or ch.isprintable())


def truncate_bytes(s: str, limit: int) -> str:
    data = s.encode("utf-8")
    if len(data) <= limit:
        return s
    return data[:limit].decode("utf-8", errors="ignore").rstrip()


def sanitize_title(title: Any, fallback: str) -> str:
    t = re.sub(r"\s+", " ", clean_text(title)).strip()
    if not t:
        t = fallback
    if len(t) > TITLE_MAX:
        cut = t[:TITLE_MAX]
        space = cut.rfind(" ")
        t = (cut[:space] if space > TITLE_MAX * 0.6 else cut).rstrip(" -,:;")
    return t


def tag_cost(tag: str) -> int:
    # YouTube counts quotes around tags that contain spaces toward the 500-character limit.
    return len(tag) + (2 if " " in tag else 0)


def sanitize_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in tags or []:
        tag = re.sub(r"\s+", " ", clean_text(raw).replace(",", " ").replace('"', "")).strip().lstrip("#")
        if not tag or tag.lower() in seen:
            continue
        cost = tag_cost(tag) + (1 if out else 0)  # comma separator
        if total + cost > TAGS_MAX_CHARS:
            continue
        out.append(tag)
        seen.add(tag.lower())
        total += cost
    return out


def title_from_filename(path: Path) -> str:
    name = re.sub(r"[_\-.]+", " ", path.stem)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:1].upper() + name[1:] if name else "New clip"


@dataclass
class Metadata:
    title: str
    description: str
    tags: list[str]
    source: str  # sidecar | claude | filename

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "description": self.description, "tags": self.tags, "source": self.source}


METADATA_SYSTEM = """You write YouTube metadata for short video clips that the channel owner made.

Rules:
- Use ONLY facts stated in the provided context. Never invent names, numbers, dates, places, quotes or claims. If the context is thin, keep the metadata general rather than guessing.
- title: specific and compelling, at most 70 characters, no clickbait that the clip cannot back up, no hashtags, no angle brackets.
- description: 2 to 4 short paragraphs separated by blank lines. Summarize what the clip shows using only the context. If sources are listed, end with a line "Sources:" followed by one source per line, exactly as given.
- tags: 8 to 12 relevant search tags, each a short phrase without '#'.
- Never use the characters < or >."""

METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "tags"],
    "additionalProperties": False,
}


def claude_metadata(cfg: Config, context: str, sources: list[str], clip: Path, pr: Probe) -> dict[str, Any]:
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    client = anthropic.Anthropic(max_retries=3, timeout=120)
    source_block = "\n".join(f"- {s}" for s in sources) if sources else "(none)"
    user = (
        f"Clip file name: {clip.name}\n"
        f"Duration: {pr.duration:.0f} seconds, {pr.width}x{pr.height}\n\n"
        f"<context>\n{context}\n</context>\n\n"
        f"<sources>\n{source_block}\n</sources>\n\n"
        "Write the title, description and tags."
    )
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": METADATA_SCHEMA}}
    if cfg.model in EFFORT_MODELS:
        output_config["effort"] = "low"
    kwargs: dict[str, Any] = dict(
        model=cfg.model,
        max_tokens=8000,
        system=METADATA_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config=output_config,
    )
    if cfg.model in FALLBACK_MODELS:
        resp = client.beta.messages.create(
            **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    else:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"Claude stopped with stop_reason={resp.stop_reason}")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    if not isinstance(data, dict) or not data.get("title"):
        raise ValueError("Claude returned JSON without a title")
    return data


def credit_sources(description: str, sources: list[str]) -> str:
    missing = [s for s in sources if s and s not in description]
    if not missing:
        return description
    block = "\n".join(missing)
    if "Sources:" in description:
        return f"{description.rstrip()}\n{block}"
    return f"{description.rstrip()}\n\nSources:\n{block}".strip()


def build_metadata(cfg: Config, clip: Path, side: Sidecars, pr: Probe) -> tuple[Metadata, dict[str, Any]]:
    """Return metadata and any extra sidecar options (privacy, publish_at)."""
    data: dict[str, Any] = {}
    if side.json_path:
        try:
            data = json.loads(side.json_path.read_text())
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except (ValueError, OSError) as e:
            log.warning("%s: ignoring unreadable sidecar %s (%s)", clip.name, side.json_path.name, e)
            data = {}

    sources = data.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    sources = [clean_text(s).strip() for s in sources if str(s).strip()]
    fallback_title = title_from_filename(clip)

    if str(data.get("title") or "").strip():
        title, description, tags, source = data["title"], data.get("description", ""), data.get("tags", []), "sidecar"
    else:
        parts = [str(data.get("context") or "").strip()]
        if side.srt_path:
            parts.append("Transcript: " + srt_to_text(side.srt_path.read_text(errors="replace")))
        if side.txt_path:
            parts.append("Narration: " + side.txt_path.read_text(errors="replace").strip())
        if data.get("description"):
            parts.append("Draft description: " + str(data["description"]))
        context = "\n\n".join(p for p in parts if p) or f"No context was provided. File name: {clip.name}"
        try:
            gen = claude_metadata(cfg, context, sources, clip, pr)
            title, description, tags, source = gen["title"], gen.get("description", ""), gen.get("tags", []), "claude"
            if data.get("tags"):
                tags = list(data["tags"]) + list(tags)
        except Exception as e:  # noqa: BLE001 - any API failure falls back to the file name
            log.warning("%s: metadata generation failed, using the file name (%s)", clip.name, e)
            title, description, tags, source = fallback_title, data.get("description", ""), data.get("tags", []), "filename"

    description = clean_text(description).strip()
    description = credit_sources(description, sources)
    if pr.duration <= SHORTS_MAX_SECONDS and "#shorts" not in description.lower():
        description = truncate_bytes(description, DESCRIPTION_MAX_BYTES - len("\n\n#Shorts"))
        description = f"{description}\n\n#Shorts".strip()
    description = truncate_bytes(description, DESCRIPTION_MAX_BYTES)

    meta = Metadata(
        title=sanitize_title(title, fallback_title),
        description=description,
        tags=sanitize_tags(tags),
        source=source,
    )
    extras = {k: data[k] for k in ("privacy", "publish_at") if data.get(k)}
    return meta, extras


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------


class QuotaExhausted(Exception):
    pass


class AuthError(Exception):
    pass


def load_credentials(cfg: Config):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not cfg.token_path.is_file():
        raise AuthError(f"{cfg.token_path} not found; run `autopost.py auth` first")
    creds = Credentials.from_authorized_user_file(str(cfg.token_path), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError("token.json has no refresh token; run `autopost.py auth` again")
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(
                f"could not refresh the YouTube token ({e}). While the OAuth consent screen is in Testing, "
                "Google ends the sign-in 7 days after `auth`; run `autopost.py auth` again"
            ) from e
        save_token(cfg.token_path, creds.to_json())
    return creds


AUTHORIZED_AT_KEY = "autopost_authorized_at"


def token_authorized_at(path: Path) -> tuple[dt.datetime | None, bool]:
    """When `auth` last ran, and whether that is estimated from the file's creation time."""
    try:
        stored = json.loads(path.read_text()).get(AUTHORIZED_AT_KEY)
        if stored:
            return parse_time(stored), False
        st = path.stat()
    except (OSError, ValueError):
        return None, False
    created = getattr(st, "st_birthtime", st.st_mtime)
    return dt.datetime.fromtimestamp(created, dt.timezone.utc), True


def save_token(path: Path, data: str, authorized_at: dt.datetime | None = None) -> None:
    """Write token.json, keeping the time of the last `auth` across refreshes."""
    token = json.loads(data)
    if authorized_at is None:
        authorized_at, _ = token_authorized_at(path)
    if authorized_at is not None:
        token[AUTHORIZED_AT_KEY] = iso(authorized_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(token))
    os.replace(tmp, path)


def token_expiry_days() -> float:
    """Days a sign-in lasts. 7 while the consent screen is in Testing; 0 turns tracking off."""
    try:
        return float(os.environ.get("AUTOPOST_TOKEN_EXPIRY_DAYS", 7))
    except ValueError:
        return 7.0


def token_status(cfg: Config, now: dt.datetime) -> tuple[str, bool]:
    """A one-line description of the YouTube sign-in, and whether it needs attention."""
    if not cfg.token_path.is_file():
        return "not authorized; run `autopost.py auth`", True
    authorized, estimated = token_authorized_at(cfg.token_path)
    days = token_expiry_days()
    if authorized is None:
        return "token.json present (sign-in time unknown)", False
    local = lambda t: t.astimezone().strftime("%b %d %H:%M")  # noqa: E731
    since = f"signed in {local(authorized)}" + (" (estimated from the file date)" if estimated else "")
    if days <= 0:
        return f"{since}; expiry not tracked (AUTOPOST_TOKEN_EXPIRY_DAYS=0)", False
    expires = authorized + dt.timedelta(days=days)
    left = expires - now
    if left <= dt.timedelta(0):
        return (f"EXPIRED {local(expires)}: {since}, and a Testing app's sign-in lasts {days:g} days. "
                "Run `autopost.py auth`"), True
    if left <= dt.timedelta(days=1):
        hours = max(1, round(left.total_seconds() / 3600))
        return (f"WARNING: sign-in expires in about {hours}h ({local(expires)}). "
                "Run `autopost.py auth` before then"), True
    hours = round(left.total_seconds() / 3600)
    return (f"{since}; expires {local(expires)} (in {hours // 24}d {hours % 24}h) "
            "while the consent screen is in Testing"), False


def youtube_client(cfg: Config):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=load_credentials(cfg), cache_discovery=False)


def http_error_reason(e: Exception) -> str:
    try:
        payload = json.loads(e.content.decode("utf-8"))  # type: ignore[attr-defined]
        return payload["error"]["errors"][0]["reason"]
    except Exception:  # noqa: BLE001
        pass
    try:
        return e.error_details[0]["reason"]  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return ""


def retriable_exceptions() -> tuple[type[BaseException], ...]:
    import http.client
    import ssl

    import httplib2

    return (httplib2.HttpLib2Error, http.client.HTTPException, ConnectionError, TimeoutError, ssl.SSLError)


def upload_video(youtube, clip: Path, meta: Metadata, privacy: str, publish_at: dt.datetime | None,
                 cfg: Config) -> dict[str, Any]:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    status: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": cfg.synthetic,
    }
    if publish_at is not None:
        status["privacyStatus"] = "private"  # required for scheduled publishing
        status["publishAt"] = iso(publish_at)
    body = {
        "snippet": {
            "title": meta.title,
            "description": meta.description,
            "tags": meta.tags,
            "categoryId": cfg.category,
        },
        "status": status,
    }
    mimetype = mimetypes.guess_type(clip.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(clip), mimetype=mimetype, chunksize=CHUNK_SIZE, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    retriable = retriable_exceptions()
    response = None
    retry = 0
    while response is None:
        error = None
        try:
            progress, response = request.next_chunk()
            if progress is not None:
                log.info("%s: uploaded %d%%", clip.name, int(progress.progress() * 100))
                retry = 0
        except HttpError as e:
            reason = http_error_reason(e)
            if reason in QUOTA_STOP_REASONS:
                raise QuotaExhausted(reason) from e
            if e.resp.status in RETRIABLE_STATUS:
                error = f"HTTP {e.resp.status} {reason}".strip()
            else:
                raise
        except retriable as e:
            error = f"{type(e).__name__}: {e}"
        if error is not None:
            retry += 1
            if retry > MAX_CHUNK_RETRIES:
                raise RuntimeError(f"upload gave up after {MAX_CHUNK_RETRIES} retries: {error}")
            delay = min(2 ** retry, 300) * random.uniform(0.5, 1.0)
            log.warning("%s: %s; retry %d/%d in %.0fs", clip.name, error, retry, MAX_CHUNK_RETRIES, delay)
            time.sleep(delay)
    if "id" not in response:
        raise RuntimeError(f"upload finished without a video id: {response}")
    return response


def set_thumbnail(youtube, video_id: str, thumb: Path) -> None:
    """Best effort: a thumbnail failure never fails the post."""
    from googleapiclient.http import MediaFileUpload

    try:
        mimetype = mimetypes.guess_type(thumb.name)[0] or "image/jpeg"
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb), mimetype=mimetype)).execute()
        log.info("thumbnail set for %s", video_id)
    except Exception as e:  # noqa: BLE001
        log.warning("thumbnail not set for %s (%s); custom thumbnails need a verified channel", video_id,
                    http_error_reason(e) or e)


# --------------------------------------------------------------------------
# Moving files
# --------------------------------------------------------------------------


def move_bundle(clip: Path, side: Sidecars, dest_dir: Path, sha: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if (dest_dir / clip.name).exists():
        prefix = f"{sha[:8]}-"
    for p in side.paths():
        if p.exists():
            shutil.move(str(p), dest_dir / f"{prefix}{p.name}")
    target = dest_dir / f"{prefix}{clip.name}"
    shutil.move(str(clip), target)
    return target


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


@contextlib.contextmanager
def run_lock(cfg: Config):
    cfg.home.mkdir(parents=True, exist_ok=True)
    with open(cfg.lock_path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def resolve_publish_at(cfg: Config, extras: dict[str, Any], clip: Path, now: dt.datetime) -> dt.datetime | None:
    publish_at = cfg.publish_at
    if extras.get("publish_at"):
        try:
            publish_at = parse_time(str(extras["publish_at"]))
        except ValueError:
            log.warning("%s: ignoring invalid publish_at %r in sidecar", clip.name, extras["publish_at"])
    elif publish_at is None and cfg.publish_delay_hours:
        publish_at = now + dt.timedelta(hours=cfg.publish_delay_hours)
    if publish_at is not None and publish_at <= now + dt.timedelta(minutes=15):
        log.warning("%s: publish time %s is not at least 15 minutes ahead; uploading without a schedule",
                    clip.name, iso(publish_at))
        return None
    return publish_at


def resolve_privacy(cfg: Config, extras: dict[str, Any], clip: Path) -> str:
    privacy = str(extras.get("privacy") or cfg.privacy).lower()
    if privacy not in {"private", "unlisted", "public"}:
        log.warning("%s: unknown privacy %r in sidecar; using %s", clip.name, privacy, cfg.privacy)
        privacy = cfg.privacy
    return privacy


def process_clip(cfg: Config, db: DB, clip: Path, state: dict[str, Any]) -> str:
    """Handle one clip. Returns 'continue' or 'stop' (stop the tick)."""
    side = find_sidecars(clip)
    sha = sha256_file(clip)
    row = db.get(sha)

    if row is not None and row["status"] == "posted":
        log.info("%s: already posted as %s; skipping", clip.name, row["video_id"])
        if not cfg.dry_run:
            move_bundle(clip, side, cfg.duplicates, sha)
            db.event(sha, "duplicate", f"{clip.name} matches posted video {row['video_id']}")
        return "continue"

    try:
        pr = probe(clip)
    except ProbeError as e:
        log.error("%s: rejected: %s", clip.name, e)
        if not cfg.dry_run:
            db.upsert(sha, clip.name, status="rejected", last_error=str(e))
            db.event(sha, "rejected", str(e))
            move_bundle(clip, side, cfg.failed, sha)
        return "continue"

    now = utcnow()
    if not cfg.dry_run:
        blocked = upload_blocked(cfg, db, now)
        if blocked:
            log.info("not uploading %s: %s", clip.name, blocked)
            return "stop"

    if not cfg.dry_run and state.get("youtube") is None:
        # Authenticate before spending a Claude call; AuthError propagates and ends the tick.
        state["youtube"] = youtube_client(cfg)

    cached = json.loads(row["metadata_json"]) if row is not None and row["metadata_json"] else None
    if cached and cached.get("source") != "filename":
        # Reuse sidecar or Claude metadata on retries; a file-name fallback gets another try at Claude.
        meta = Metadata(cached["title"], cached["description"], cached["tags"], cached["source"])
        extras = cached.get("extras", {})
    else:
        meta, extras = build_metadata(cfg, clip, side, pr)

    privacy = resolve_privacy(cfg, extras, clip)
    publish_at = resolve_publish_at(cfg, extras, clip, now)

    if cfg.dry_run:
        out = {
            "file": clip.name, "sha256": sha, "duration": round(pr.duration, 2),
            "resolution": f"{pr.width}x{pr.height}", **meta.as_dict(),
            "privacy": "private" if publish_at else privacy,
            "publish_at": iso(publish_at) if publish_at else None,
            "contains_synthetic_media": cfg.synthetic,
            "thumbnail": side.thumb_path.name if side.thumb_path else None,
        }
        print("DRY-RUN " + json.dumps(out, ensure_ascii=False), flush=True)
        return "continue"

    db.upsert(
        sha, clip.name, duration=pr.duration, width=pr.width, height=pr.height,
        title=meta.title, metadata_source=meta.source, privacy=privacy,
        publish_at=iso(publish_at) if publish_at else None,
        metadata_json=json.dumps({**meta.as_dict(), "extras": extras}),
    )

    youtube = state["youtube"]

    log.info("%s: uploading %r (%s%s)", clip.name, meta.title, privacy,
             f", publishes {iso(publish_at)}" if publish_at else "")
    try:
        response = upload_video(youtube, clip, meta, privacy, publish_at, cfg)
    except QuotaExhausted as e:
        until = next_quota_reset(utcnow())
        db.set_state("paused_until", iso(until))
        db.upsert(sha, clip.name, last_error=f"YouTube {e}; paused until {iso(until)}")
        db.event(sha, "quota", f"{e}; paused until {iso(until)}")
        log.error("YouTube returned %s; no uploads until %s", e, iso(until))
        return "stop"
    except Exception as e:  # noqa: BLE001
        from googleapiclient.errors import HttpError

        msg = f"{type(e).__name__}: {http_error_reason(e) or e}" if isinstance(e, HttpError) else f"{type(e).__name__}: {e}"
        attempts = (row["attempts"] if row else 0) + 1
        final = attempts >= MAX_ATTEMPTS
        db.upsert(sha, clip.name, attempts=attempts, last_error=msg, status="failed" if final else "pending")
        db.event(sha, "upload_failed", msg)
        log.error("%s: upload attempt %d/%d failed: %s", clip.name, attempts, MAX_ATTEMPTS, msg)
        if final:
            move_bundle(clip, side, cfg.failed, sha)
            log.error("%s: moved to %s after %d attempts", clip.name, cfg.failed, attempts)
        return "continue"

    video_id = response["id"]
    actual = response.get("status", {}).get("privacyStatus")
    requested = "private" if publish_at else privacy
    if actual and actual != requested:
        log.warning("%s: YouTube set privacy to %s, not %s (unaudited API projects and channel settings can force"
                    " private)", clip.name, actual, requested)
    db.upsert(sha, clip.name, status="posted", video_id=video_id, posted_at=iso(utcnow()),
              privacy=actual or requested, attempts=(row["attempts"] if row else 0) + 1, last_error=None)
    db.event(sha, "posted", f"https://youtu.be/{video_id}")
    log.info("%s: posted https://youtu.be/%s", clip.name, video_id)

    if side.thumb_path:
        set_thumbnail(youtube, video_id, side.thumb_path)
    move_bundle(clip, side, cfg.posted, sha)
    return "continue"


def warn_token_expiry(cfg: Config, db: DB) -> None:
    """Log the sign-in warning at most every 6 hours, so a service doesn't repeat it every pass."""
    now = utcnow()
    line, attention = token_status(cfg, now)
    if not attention:
        return
    last = db.get_state("token_warned_at")
    if last and now - parse_time(last) < dt.timedelta(hours=6):
        return
    log.warning("YouTube auth: %s", line)
    db.set_state("token_warned_at", iso(now))


def run_once(cfg: Config) -> int:
    for d in (cfg.inbox, cfg.posted, cfg.failed):
        d.mkdir(parents=True, exist_ok=True)
    with run_lock(cfg) as locked:
        if not locked:
            log.info("another run holds %s; skipping this tick", cfg.lock_path)
            return 0
        db = DB(cfg.db_path)
        if not cfg.dry_run:
            warn_token_expiry(cfg, db)
        clips = scan_inbox(cfg)
        if not clips:
            log.info("inbox is empty")
            return 0
        if not cfg.dry_run:
            blocked = upload_blocked(cfg, db, utcnow())
            if blocked:
                log.info("%d clip(s) waiting; %s", len(clips), blocked)
                return 0
        state: dict[str, Any] = {}
        for clip in clips:
            try:
                if process_clip(cfg, db, clip, state) == "stop":
                    break
            except AuthError as e:
                log.error("YouTube auth: %s", e)
                db.event(None, "auth_error", str(e))
                return 1
            except FileNotFoundError:
                log.warning("%s disappeared while processing; skipping", clip.name)
        return 0


def run_loop(cfg: Config, interval: float) -> int:
    stop = threading.Event()

    def handle(signum, _frame):
        log.info("received signal %d; stopping after this tick", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    log.info("autopost loop started; checking every %ss", interval)
    while not stop.is_set():
        try:
            run_once(cfg)
        except Exception:  # noqa: BLE001 - a service must survive one bad tick
            log.exception("tick failed")
        stop.wait(interval)
    return 0


# --------------------------------------------------------------------------
# Other commands
# --------------------------------------------------------------------------


def cmd_auth(cfg: Config, args) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not cfg.client_secrets.is_file():
        print(f"Missing {cfg.client_secrets}. Download the Desktop OAuth client JSON from Google Cloud "
              "(see README) and save it there, or set YOUTUBE_CLIENT_SECRETS.", file=sys.stderr)
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.client_secrets), SCOPES)
    # Desktop clients accept any loopback port, so a browser sign-in on this machine uses a free one.
    # Headless sign-in needs a known port for the SSH tunnel.
    port = args.port if args.port is not None else (8765 if args.no_browser else 0)
    if args.no_browser:
        print(f"Headless mode. On your laptop run:  ssh -L {port}:localhost:{port} <this-server>\n"
              "then open the URL below in your laptop's browser.\n")
    try:
        creds = flow.run_local_server(
            host="localhost", port=port, open_browser=not args.no_browser,
            authorization_prompt_message="Open this URL to authorize autopost:\n{url}\n",
            success_message="autopost is authorized. You can close this tab.",
            access_type="offline", prompt="consent",
        )
    except OSError as e:
        print(f"Could not listen on localhost:{port} ({e.strerror}). Pick another port with --port.", file=sys.stderr)
        return 1
    if not creds.refresh_token:
        print("Google returned no refresh token; remove the app at https://myaccount.google.com/permissions "
              "and run auth again.", file=sys.stderr)
        return 1
    save_token(cfg.token_path, creds.to_json(), authorized_at=utcnow())
    print(f"Saved {cfg.token_path} (mode 600). Keep it out of git.")
    return 0


def atomic_copy(src: Path, dest: Path) -> None:
    tmp = dest.with_name(f".{dest.name}.partial")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def cmd_enqueue(cfg: Config, args) -> int:
    src = Path(args.clip).expanduser()
    if not src.is_file():
        print(f"{src} is not a file", file=sys.stderr)
        return 1
    if src.suffix.lower() not in VIDEO_EXTS:
        print(f"{src.name}: expected one of {', '.join(sorted(VIDEO_EXTS))}", file=sys.stderr)
        return 1
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    name = args.name or src.name
    if Path(name).suffix.lower() != src.suffix.lower():
        name = Path(name).stem + src.suffix
    dest = cfg.inbox / name
    if dest.exists():
        print(f"{dest} already exists", file=sys.stderr)
        return 1
    stem = Path(name).stem
    sidecars = list(find_sidecars(src).paths()) + [Path(p).expanduser() for p in args.sidecar or []]
    # Sidecars first, so the clip is never visible without them.
    for sc in sidecars:
        if not sc.is_file():
            print(f"sidecar {sc} not found", file=sys.stderr)
            return 1
        suffix = ".narration.txt" if sc.name.endswith(".narration.txt") else sc.suffix
        if sc.name == "narration.txt":
            suffix = ".narration.txt"
        atomic_copy(sc, cfg.inbox / f"{stem}{suffix}")
    atomic_copy(src, dest)
    print(f"queued {dest}")
    return 0


def cmd_status(cfg: Config, args) -> int:
    for d in (cfg.inbox, cfg.posted, cfg.failed):
        d.mkdir(parents=True, exist_ok=True)
    db = DB(cfg.db_path)
    now = utcnow()
    today = db.posted_since(quota_day_start(now))
    blocked = upload_blocked(cfg, db, now)
    waiting = [p.name for p in sorted(cfg.inbox.iterdir())
               if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in VIDEO_EXTS]
    counts = {r["status"]: r["n"] for r in db.conn.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
    print(f"home:          {cfg.home}")
    print(f"auth:          {token_status(cfg, now)[0]}")
    print(f"uploads today: {today}/{cfg.max_per_day} (Pacific day)")
    print(f"next upload:   {'now' if blocked is None else blocked}")
    print(f"inbox:         {len(waiting)} clip(s)" + (f": {', '.join(waiting[:10])}" if waiting else ""))
    print(f"totals:        {json.dumps(counts) if counts else 'none yet'}")
    rows = db.conn.execute(
        "SELECT filename, status, video_id, title, attempts, last_error, updated_at FROM clips "
        "ORDER BY updated_at DESC LIMIT ?", (args.limit,)
    ).fetchall()
    if rows:
        print("\nrecent:")
        for r in rows:
            link = f"https://youtu.be/{r['video_id']}" if r["video_id"] else "-"
            err = f"  error: {r['last_error'][:120]}" if r["last_error"] and r["status"] != "posted" else ""
            print(f"  {r['updated_at']}  {r['status']:<8} {link:<29} {r['title'] or r['filename']}"
                  f"  (attempts {r['attempts']}){err}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autopost.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", default=os.environ.get("AUTOPOST_HOME", "."),
                   help="working directory holding inbox/, posted/, failed/, the database and tokens "
                        "(default: $AUTOPOST_HOME or the current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("auth", help="authorize your YouTube channel (OAuth desktop flow)")
    a.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    a.add_argument("--port", type=int, default=None,
                   help="local port for the OAuth redirect (default: any free port; 8765 with --no-browser)")

    r = sub.add_parser("run", help="process the inbox")
    mode = r.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="one pass, for cron")
    mode.add_argument("--loop", action="store_true", help="keep running, for a service")
    r.add_argument("--interval", type=float, default=300, help="seconds between passes with --loop (default 300)")
    r.add_argument("--dry-run", action="store_true", help="print generated metadata; upload and move nothing")
    r.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    r.add_argument("--no-synthetic", action="store_true",
                   help="do not declare altered or synthetic content (containsSyntheticMedia=false)")
    r.add_argument("--max-per-day", type=int, default=int(os.environ.get("AUTOPOST_MAX_PER_DAY", 3)))
    r.add_argument("--min-gap-hours", type=float, default=float(os.environ.get("AUTOPOST_MIN_GAP_HOURS", 4)))
    sched = r.add_mutually_exclusive_group()
    sched.add_argument("--publish-at", help="schedule publishing at this ISO 8601 time (uploads as private)")
    sched.add_argument("--publish-delay-hours", type=float,
                       help="schedule each upload to publish this many hours later (uploads as private)")
    r.add_argument("--category", default="22", help="YouTube category id (default 22, People & Blogs)")
    r.add_argument("--settle-seconds", type=float, default=10,
                   help="ignore clips modified more recently than this (default 10)")

    e = sub.add_parser("enqueue", help="copy a clip into the inbox atomically")
    e.add_argument("clip")
    e.add_argument("--name", help="file name to use in the inbox")
    e.add_argument("--sidecar", action="append",
                   help="extra sidecar file (.json, .srt, .txt, narration.txt, .jpg); repeatable")

    s = sub.add_parser("status", help="show limits, queue and recent posts")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--max-per-day", type=int, default=int(os.environ.get("AUTOPOST_MAX_PER_DAY", 3)))
    s.add_argument("--min-gap-hours", type=float, default=float(os.environ.get("AUTOPOST_MIN_GAP_HOURS", 4)))
    return p


def main(argv: list[str] | None = None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--home", default=os.environ.get("AUTOPOST_HOME", "."))
    known, _ = pre.parse_known_args(argv)
    load_dotenv(Path(known.home).expanduser() / ".env")

    args = build_parser().parse_args(argv)
    cfg = Config(home=Path(args.home).expanduser().resolve())
    if hasattr(args, "max_per_day"):
        cfg.max_per_day = args.max_per_day
        cfg.min_gap_hours = args.min_gap_hours
    setup_logging(cfg)

    if args.command == "auth":
        return cmd_auth(cfg, args)
    if args.command == "enqueue":
        return cmd_enqueue(cfg, args)
    if args.command == "status":
        return cmd_status(cfg, args)

    cfg.privacy = args.privacy
    cfg.synthetic = not args.no_synthetic
    cfg.category = args.category
    cfg.settle_seconds = args.settle_seconds
    cfg.dry_run = args.dry_run
    cfg.publish_delay_hours = args.publish_delay_hours
    if args.publish_at:
        try:
            cfg.publish_at = parse_time(args.publish_at)
        except ValueError:
            log.error("--publish-at must be an ISO 8601 time, e.g. 2026-12-01T17:00:00-08:00")
            return 2
    if cfg.max_per_day * 1600 > 10_000:
        log.warning("--max-per-day %d may exceed a 10,000-unit quota if uploads cost 1,600 units each; "
                    "check your project's Quotas page", cfg.max_per_day)
    log.info("model=%s privacy=%s synthetic=%s max/day=%d gap=%gh%s", cfg.model, cfg.privacy, cfg.synthetic,
             cfg.max_per_day, cfg.min_gap_hours, " DRY-RUN" if cfg.dry_run else "")
    if args.loop:
        return run_loop(cfg, args.interval)
    return run_once(cfg)


if __name__ == "__main__":
    sys.exit(main())
