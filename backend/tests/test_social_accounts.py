from urllib.parse import parse_qs, urlparse

import pytest
from conftest import assert_error, login, record, rows
from cryptography.fernet import Fernet

from app.social.accounts import AccountService
from app.social.adapters.base import Identity, ProviderError, Tokens
from app.social.config import PLATFORMS, SocialSettings
from app.social.db_models import OAuthState, SocialAccount, SocialEvent
from app.social.security import TokenCipher


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


def test_disabled_platforms_and_no_secrets(client, headers):
    result = client.get("/v1/social-accounts", headers=headers)
    assert result.status_code == 200
    assert not any(p["enabled"] for p in result.json()["platforms"])
    assert_error(
        client.get("/v1/social-accounts/youtube/connect", headers=headers),
        503,
        "SOCIAL_UNAVAILABLE",
    )
    assert "secret" not in result.text


def test_connect_encrypts_tokens_and_consumes_browser_bound_state(
    client, headers, database, social
):
    state, binding = connect(client, headers)
    assert rows(database, OAuthState)[0]["digest"] != state
    result = callback(client, state, binding)
    assert "Account connected" in result.text
    assert not rows(database, OAuthState)
    saved = rows(database, SocialAccount)[0]
    assert "private-access" not in saved["access_token_encrypted"]
    assert "private-refresh" not in saved["refresh_token_encrypted"]
    assert (
        social[0].cipher.decrypt(saved["access_token_encrypted"], saved["user_id"], "youtube")
        == "private-access"
    )
    public = client.get("/v1/social-accounts", headers=headers)
    assert public.json()["accounts"][0]["account_name"] == "Fred Creates"
    assert "token" not in public.text and "private-access" not in result.text
    assert "expired" in callback(client, state, binding).text
    assert len(rows(database, SocialAccount)) == 1
    assert rows(database, SocialEvent)[0]["event"] == "social_account_connected"


@pytest.mark.parametrize("wrong", ["state", "binding", "platform", "expired"])
def test_state_rejects_forgery_wrong_platform_and_expiration(
    client, headers, clock, database, social, wrong
):
    state, binding = connect(client, headers)
    if wrong == "expired":
        clock.advance(601)
    result = callback(
        client,
        "forged" if wrong == "state" else state,
        "forged" if wrong == "binding" else binding,
        "x" if wrong == "platform" else "youtube",
    )
    assert "expired" in result.text
    assert not rows(database, SocialAccount)


def test_oauth_denial_does_not_create_account(client, headers, database, social):
    state, binding = connect(client, headers)
    assert "not approved" in callback(client, state, binding, error="access_denied").text
    assert not rows(database, SocialAccount)
    assert not rows(database, OAuthState)


def test_refresh_rotation_and_reconnect_status(client, headers, database, clock, social):
    state, binding = connect(client, headers)
    callback(client, state, binding)
    account = rows(database, SocialAccount)[0]
    clock.advance(3500)
    assert social[0].token(account["id"], account["user_id"], "youtube") == "rotated-access"
    rotated = record(database, SocialAccount, account["id"])
    assert (
        social[0].cipher.decrypt(rotated["refresh_token_encrypted"], account["user_id"], "youtube")
        == "rotated-refresh"
    )

    def invalid(refresh):
        raise ProviderError("RECONNECT_REQUIRED")

    social[1].refresh_token = invalid
    clock.advance(3500)
    with pytest.raises(ProviderError, match="RECONNECT_REQUIRED"):
        social[0].token(account["id"], account["user_id"], "youtube")
    assert record(database, SocialAccount, account["id"])["status"] == "reconnect_required"


def test_disconnect_deletes_secrets_and_preserves_other_users(client, headers, database, social):
    state, binding = connect(client, headers)
    callback(client, state, binding)
    other = login(client, "jon@example.com")
    assert client.delete("/v1/social-accounts/youtube", headers=other).status_code == 200
    assert len(rows(database, SocialAccount)) == 1
    assert client.delete("/v1/social-accounts/youtube", headers=headers).status_code == 200
    assert not rows(database, SocialAccount)


def test_token_encryption_is_bound_to_owner_and_platform():
    cipher = TokenCipher(Fernet.generate_key().decode())
    saved = cipher.encrypt("secret", "creator", "youtube")
    for owner, platform in [("other", "youtube"), ("creator", "x")]:
        with pytest.raises(Exception, match="reconnected"):
            cipher.decrypt(saved, owner, platform)


def test_connection_rate_limit(client, headers, social):
    for _ in range(6):
        connect(client, headers)
    assert_error(
        client.get("/v1/social-accounts/youtube/connect", headers=headers),
        429,
        "SOCIAL_RATE_LIMITED",
    )
