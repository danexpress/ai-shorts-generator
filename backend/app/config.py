import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL",
            "sqlite+pysqlite:///" + str(Path(__file__).resolve().parents[1] / "ai_shorts.db"),
        )
    )
    media_dir: Path = field(
        default_factory=lambda: (
            Path(os.environ.get("MEDIA_DIR", str(Path(__file__).resolve().parents[1] / "media")))
            .expanduser()
            .resolve()
        )
    )
    media_signing_key: str = field(
        default_factory=lambda: os.environ.get("MEDIA_SIGNING_KEY", "local-development-media-key")
    )
    ffmpeg_binary: str | None = field(default_factory=lambda: os.environ.get("FFMPEG_BIN"))
    seed_demo_data: bool = field(
        default_factory=lambda: (
            os.environ.get("SEED_DEMO_DATA", "true").lower() in {"1", "true", "yes"}
        )
    )
    token_ttl_seconds: int = 3600
    upload_ttl_seconds: int = 900
    transcription_seconds: float = 4
    analysis_seconds: float = 3
    source_retention_hours: int = 24
    analysis_retention_days: int = 30
    render_retention_days: int = 7
    history_retention_days: int = 90
    youtube_import: bool = field(
        default_factory=lambda: (
            os.environ.get("YOUTUBE_IMPORT", "false").lower() in {"1", "true", "yes"}
        )
    )
    # Explicit allowlist: no wildcard origins with browser credentials.
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
