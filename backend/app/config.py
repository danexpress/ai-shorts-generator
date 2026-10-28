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
    # "local" runs real transcription in `make worker`; "demo" keeps the clock-driven
    # simulator (canned transcript, no worker needed).
    processing_mode: str = field(
        default_factory=lambda: os.environ.get("PROCESSING_MODE", "local").strip().lower()
    )
    whisper_model: str = field(default_factory=lambda: os.environ.get("WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: os.environ.get("WHISPER_DEVICE", "auto"))
    whisper_compute_type: str = field(
        default_factory=lambda: os.environ.get("WHISPER_COMPUTE_TYPE", "auto")
    )
    # Empty means auto-detect the spoken language.
    whisper_language: str = field(default_factory=lambda: os.environ.get("WHISPER_LANGUAGE", ""))
    # "whisperx" adds forced alignment (install with `uv sync --extra align`).
    whisper_align: str = field(
        default_factory=lambda: os.environ.get("WHISPER_ALIGN", "").strip().lower()
    )
    # Highlight detection (Claude API). "demo" keeps the built-in demo analyzer.
    highlight_model: str = field(
        default_factory=lambda: os.environ.get("HIGHLIGHT_MODEL", "claude-opus-5-5").strip()
    )
    highlight_effort: str = field(
        default_factory=lambda: os.environ.get("HIGHLIGHT_EFFORT", "high").strip().lower()
    )
    highlight_candidates: int = field(
        default_factory=lambda: int(os.environ.get("HIGHLIGHT_CANDIDATES", "3"))
    )
    highlight_min_seconds: float = 20
    highlight_max_seconds: float = 40
    # Transcripts estimated above this many tokens are split into overlapping windows.
    highlight_window_tokens: int = field(
        default_factory=lambda: int(os.environ.get("HIGHLIGHT_WINDOW_TOKENS", "100000"))
    )
    # "default" = server-side refusal fallback chosen by Anthropic; "off" disables it.
    highlight_fallbacks: str = field(
        default_factory=lambda: os.environ.get("HIGHLIGHT_FALLBACKS", "default").strip().lower()
    )
    # Re-rank candidates with audio loudness peaks and scene changes (FFmpeg).
    highlight_signals: bool = field(
        default_factory=lambda: (
            os.environ.get("HIGHLIGHT_SIGNALS", "false").lower() in {"1", "true", "yes"}
        )
    )
    job_lease_seconds: int = 300
    # Sign-in. Google OIDC is enabled when both client values are set.
    google_client_id: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_CLIENT_SECRET", ""), repr=False
    )
    # Email/password sign-in (demo accounts, local development). Disable in production.
    allow_password_login: bool = field(
        default_factory=lambda: (
            os.environ.get("ALLOW_PASSWORD_LOGIN", "true").lower() in {"1", "true", "yes"}
        )
    )
    # Private beta: Google accounts may create a user only when their verified email
    # is invited (an existing invited user, or listed here), unless signup is open.
    beta_invite_emails: frozenset = field(
        default_factory=lambda: frozenset(
            e.strip().lower()
            for e in os.environ.get("BETA_INVITE_EMAILS", "").split(",")
            if e.strip()
        )
    )
    open_signup: bool = field(
        default_factory=lambda: (
            os.environ.get("OPEN_SIGNUP", "false").lower() in {"1", "true", "yes"}
        )
    )
    backend_public_url: str = field(
        default_factory=lambda: os.environ.get(
            "BACKEND_PUBLIC_URL", "http://127.0.0.1:8000"
        ).rstrip("/")
    )
    frontend_public_url: str = field(
        default_factory=lambda: os.environ.get(
            "FRONTEND_PUBLIC_URL", "http://127.0.0.1:3000"
        ).rstrip("/")
    )
    session_access_seconds: int = 900
    session_refresh_seconds: int = 30 * 86_400
    login_state_seconds: int = 600
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
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            origin.strip()
            for origin in os.environ.get(
                "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
            ).split(",")
            if origin.strip()
        )
    )
