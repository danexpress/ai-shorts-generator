"""Common provider contract; publish advances one persisted checkpoint at a time."""

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

    def __init__(self, settings, http):
        self.settings, self.http = settings, http
        self.client = settings.clients[self.platform]

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
    def validate_video(self, video, metadata, creator=None): ...

    @abstractmethod
    def publish(self, token, video: Path, info, metadata, checkpoint): ...

    @abstractmethod
    def get_publish_status(self, token, checkpoint): ...

    @abstractmethod
    def disconnect(self, token): ...
