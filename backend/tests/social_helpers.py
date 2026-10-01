from urllib.parse import parse_qs, urlparse

import pytest
from conftest import ready
from cryptography.fernet import Fernet

from app.social.accounts import AccountService
from app.social.adapters.base import Identity, Tokens
from app.social.config import PLATFORMS, SocialSettings


class FakeAccountAdapter:
    scopes = ("publish", "profile")

    def connect(self, state, verifier):
        return "https://provider.example/authorize?state=" + state

    def exchange(self, code, verifier):
        assert code == "approved"
        return Tokens("private-access", "private-refresh", 3600, list(self.scopes))

    def identity(self, token):
        return Identity("creator-1", "Fred Creates", "@fred", "https://provider.example/avatar.png")

    def refresh_token(self, refresh):
        assert refresh == "private-refresh"
        return Tokens("rotated-access", "rotated-refresh", 3600, list(self.scopes))

    def disconnect(self, token):
        pass


@pytest.fixture
def social(client, app):
    settings = SocialSettings(
        public_url="https://api.example",
        frontend_url="https://studio.example",
        encryption_keys=Fernet.generate_key().decode(),
        enabled={p: True for p in PLATFORMS},
        clients={p: {"id": "client", "secret": "secret"} for p in PLATFORMS},
    )
    adapter = FakeAccountAdapter()
    service = AccountService(
        app.state.database, settings, app.state.clock, {p: adapter for p in PLATFORMS}
    )
    app.state.social_accounts = service
    return service, adapter


def connect(client, headers, platform="youtube"):
    result = client.get(f"/v1/social-accounts/{platform}/connect", headers=headers)
    assert result.status_code == 200, result.text
    state = parse_qs(urlparse(result.json()["authorization_url"]).query)["state"][0]
    return state, result.cookies[f"social_oauth_{platform}"]


def callback(client, state, binding, platform="youtube", **params):
    return client.get(
        f"/v1/social-accounts/{platform}/callback",
        params={"state": state, "code": "approved", **params},
        headers={"Cookie": f"social_oauth_{platform}={binding}"},
    )


@pytest.fixture
def render(client, headers, clock, monkeypatch):
    from app.routers import renders

    def fake(source, destination, *args):
        destination.write_bytes(b"video")

    monkeypatch.setattr(renders, "render_short", fake)
    project = ready(client, headers, clock)
    clip = client.get(f"/v1/projects/{project}/clips", headers=headers).json()["clips"][0]
    result = client.post(
        f"/v1/projects/{project}/render", headers=headers, json={"clipId": clip["id"]}
    )
    assert result.status_code == 200, result.text
    return result.json()["id"]
