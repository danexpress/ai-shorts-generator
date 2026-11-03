"""The Publisher interface every platform adapter implements.

One adapter instance serves one platform. The app (OAuth routes, token refresh,
publishing jobs, UI) only ever talks to this interface, so an adapter can be
replaced, for example by one backed by a unified posting API, by registering it
with SOCIAL_ADAPTER_<PLATFORM>=module:Class (see adapters/__init__.py).

Lifecycle:
    connect(state, verifier) -> authorization URL     (start linking an account)
    exchange(code, verifier) -> Tokens                (finish linking)
    identity(access) -> Identity                      (who was linked)
    refresh_token(refresh) -> Tokens                  (called before tokens expire)
    validate(video, metadata, creator) -> None        (raise ProviderError up front)
    publish(token, video, info, metadata, checkpoint) -> PublishStep
    get_status(token, checkpoint) -> PublishStep      (poll until published/failed)
    disconnect(access) -> None                        (revoke at the platform)

publish() is resumable: each call advances one step (e.g. one upload chunk) and
returns a PublishStep whose checkpoint the worker saves (encrypted) before calling
again, so restarts and retries never upload or post twice. A finished step carries
the platform's post_id and post_url. Raise ProviderError with retryable=True for
transient failures (backoff and retry) and uncertain=True when a post may already
exist (never retried blindly).
"""

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from authlib.integrations.httpx_client import OAuth2Client

from ..security import digest


class ProviderError(Exception):
    def __init__(self, code="PROVIDER_UNAVAILABLE", retryable=False, uncertain=False):
        super().__init__(code)  # Never include provider text, URLs, tokens or captions.
        self.code, self.retryable, self.uncertain = code, retryable, uncertain


@dataclass(repr=False)
class Tokens:
    access: str
    refresh: str | None
    expires_in: int
    scopes: list[str]


@dataclass
class Identity:
    id: str
    name: str
    username: str | None = None
    avatar: str | None = None


@dataclass
class PublishStep:
    status: str
    checkpoint: dict = field(default_factory=dict, repr=False)
    post_id: str | None = None
    post_url: str | None = None
    delay: int = 3
    notice: str | None = None


class SocialPublisher(ABC):
    platform = ""
    scopes = ()
    authorize_url = ""
    token_url = ""
    api_hosts = ()
    # False for adapters with their own credentials (e.g. a unified posting API):
    # the platform's <PLATFORM>_CLIENT_ID/SECRET are then not required.
    requires_client_credentials = True
    # validate() needs creator_info() (live per-account options, e.g. TikTok privacy).
    # Such adapters are validated at admission with fresh creator info only.
    needs_creator_info = False
    # set_thumbnail() is available for an optional custom cover after publishing.
    supports_thumbnail = False

    def __init__(self, settings, http, platform: str | None = None):
        # `platform` lets one adapter class serve several platforms.
        self.settings, self.http = settings, http
        self.platform = platform or self.platform
        self.client = settings.clients.get(self.platform, {"id": "", "secret": ""})

    def connect(self, state, verifier):
        challenge = base64.urlsafe_b64encode(bytes.fromhex(digest(verifier))).rstrip(b"=").decode()
        client = OAuth2Client(
            self.client["id"],
            scope=" ".join(self.scopes),
            redirect_uri=self.settings.callback(self.platform),
        )
        url, _ = client.create_authorization_url(
            self.authorize_url,
            state=state,
            code_challenge=challenge,
            code_challenge_method="S256",
            **self.authorization_options(),
        )
        return url

    def authorization_options(self):
        return {}

    @abstractmethod
    def exchange(self, code, verifier): ...

    @abstractmethod
    def refresh_token(self, refresh): ...

    @abstractmethod
    def identity(self, token): ...

    @abstractmethod
    def validate(self, video, metadata, creator=None): ...

    @abstractmethod
    def publish(self, token, video: Path, info, metadata, checkpoint): ...

    @abstractmethod
    def get_status(self, token, checkpoint): ...

    @abstractmethod
    def disconnect(self, token): ...

    # Optional hooks with safe defaults; override where the platform supports them.

    def creator_info(self, token):
        """Per-account publishing options, when needs_creator_info is set."""
        raise ProviderError("UNSUPPORTED")

    def upload_complete(self, checkpoint) -> bool:
        """True once the media is fully transferred: later steps only poll get_status()
        (or create_post()), so they still work after the local render expires."""
        return False

    def create_post(self, token, metadata, checkpoint):
        """Create the post after a step returned status "publishing" (media ready).

        Not idempotent on most platforms: the worker records an "uncertain" marker
        before calling, and never repeats it blindly.
        """
        raise ProviderError("PROVIDER_RESPONSE_INVALID")

    def set_thumbnail(self, token, post_id, image):
        raise ProviderError("UNSUPPORTED")
