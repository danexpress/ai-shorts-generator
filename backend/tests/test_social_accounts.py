import pytest
from conftest import assert_error, login, record, rows
from cryptography.fernet import Fernet

from app.social.adapters.base import ProviderError
from app.social.db_models import OAuthState, SocialAccount, SocialEvent
from app.social.security import TokenCipher


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


from social_helpers import callback, connect


def test_refresh_lease_prevents_concurrent_rotating_refreshes(
    client, headers, database, clock, social
):
    from app.social.db_models import SocialRefreshLease

    state, binding = connect(client, headers)
    callback(client, state, binding)
    account = rows(database, SocialAccount)[0]
    clock.advance(3500)
    with database.transaction() as db:
        db.add(
            SocialRefreshLease(
                account_id=account["id"],
                owner="another-worker",
                expires_at=int(clock() * 1000) + 60_000,
            )
        )
    with pytest.raises(ProviderError) as result:
        social[0].token(account["id"], account["user_id"], "youtube")
    assert result.value.retryable
    clock.advance(61)
    assert social[0].token(account["id"], account["user_id"], "youtube") == "rotated-access"


def test_disconnect_during_oauth_exchange_cancels_connection(client, headers, social):
    from conftest import rows

    from app.social.db_models import SocialAccount

    service, adapter = social
    state, binding = connect(client, headers)
    original = adapter.identity

    def identity(token):
        from app.social.db_models import OAuthState

        with service.database.transaction() as db:
            user = db.get(
                OAuthState, __import__("hashlib").sha256(state.encode()).hexdigest()
            ).user_id
        service.disconnect(user, "youtube")
        return original(token)

    adapter.identity = identity
    result = callback(client, state, binding)
    assert "canceled" in result.text and not rows(service.database, SocialAccount)
