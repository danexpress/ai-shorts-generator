"""Server-only credentials and explicit rollout/audit settings."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

PLATFORMS = ("youtube", "tiktok", "x")


def flag(name):
    return os.environ.get(name, "false").lower() in {"true", "1", "yes"}


@dataclass(frozen=True)
class SocialSettings:
    public_url: str = field(
        default_factory=lambda: os.getenv("BACKEND_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    )
    frontend_url: str = field(
        default_factory=lambda: os.getenv("FRONTEND_PUBLIC_URL", "http://127.0.0.1:3000").rstrip(
            "/"
        )
    )
    encryption_keys: str = field(
        default_factory=lambda: os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEYS", ""), repr=False
    )
    allow_local_http: bool = field(default_factory=lambda: flag("SOCIAL_ALLOW_INSECURE_LOCALHOST"))
    enabled: dict = field(
        default_factory=lambda: {p: flag(f"ENABLE_{p.upper()}_PUBLISHING") for p in PLATFORMS}
    )
    clients: dict = field(
        default_factory=lambda: {
            p: {
                "id": os.getenv(f"{p.upper()}_CLIENT_ID", ""),
                "secret": os.getenv(f"{p.upper()}_CLIENT_SECRET", ""),
            }
            for p in PLATFORMS
        },
        repr=False,
    )
    youtube_verified: bool = field(default_factory=lambda: flag("YOUTUBE_PUBLISHING_VERIFIED"))
    tiktok_audited: bool = field(default_factory=lambda: flag("TIKTOK_PUBLISHING_AUDITED"))
    copy_url: str = field(default_factory=lambda: os.getenv("SOCIAL_COPY_API_URL", ""))
    copy_key: str = field(default_factory=lambda: os.getenv("SOCIAL_COPY_API_KEY", ""), repr=False)
    copy_model: str = field(default_factory=lambda: os.getenv("SOCIAL_COPY_MODEL", ""))
    max_attempts: int = 4
    lease_seconds: int = 180
    rate_limit: int = 6

    def callback(self, platform):
        return f"{self.public_url}/v1/social-accounts/{platform}/callback"

    def valid_url(self, url, platform=None):
        parsed = urlparse(url)
        return bool(
            parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (
                    self.allow_local_http
                    and platform != "tiktok"
                    and parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1"}
                )
            )
        )

    def available(self, platform):
        client = self.clients.get(platform, {})
        return bool(
            self.enabled.get(platform)
            and client.get("id")
            and client.get("secret")
            and self.encryption_keys
            and self.valid_url(self.public_url, platform)
            and self.valid_url(self.frontend_url, platform)
        )

    def notice(self, platform):
        if not self.available(platform):
            return "This connection is not enabled yet. Your workspace administrator needs to finish setup."
        if platform == "youtube" and not self.youtube_verified:
            return "This YouTube integration is configured for private uploads until its API project is verified."
        if platform == "tiktok" and not self.tiktok_audited:
            return "TikTok audit pending: only private accounts and Only me posts are supported."
        return None
