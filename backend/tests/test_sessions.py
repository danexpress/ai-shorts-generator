import dataclasses

import pytest
from conftest import SOURCE, assert_error
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db_models import AuthSessionRecord, UserRecord
from app.main import create_app
from app.sessions import ACCESS_COOKIE, REFRESH_COOKIE

ORIGIN = {"Origin": "http://127.0.0.1:3000"}


def browser_login(client, email="maya@example.com"):
    response = client.post("/v1/auth/google", json={"email": email, "password": "DemoPass123!"})
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def cookie(client, name):
    return next(c.value for c in client.cookies.jar if c.name == name)


def test_password_login_starts_a_cookie_session(client):
    csrf = browser_login(client)
    client.headers.pop("Authorization", None)
    me = client.get("/v1/me")
    assert me.status_code == 200 and me.json()["csrfToken"] == csrf
    assert me.json()["user"]["googleLinked"] is False


def test_cookie_writes_require_csrf_and_an_allowed_origin(client):
    csrf = browser_login(client)
    body = {**SOURCE, "sizeBytes": 12}
    assert_error(client.post("/v1/uploads", json=body, headers=ORIGIN), 403, "CSRF_FAILED")
    wrong = {**ORIGIN, "X-CSRF-Token": "guess"}
    assert_error(client.post("/v1/uploads", json=body, headers=wrong), 403, "CSRF_FAILED")
    evil = {"Origin": "https://evil.example.com", "X-CSRF-Token": csrf}
    assert_error(client.post("/v1/uploads", json=body, headers=evil), 403, "CSRF_FAILED")
    ok = client.post("/v1/uploads", json=body, headers={**ORIGIN, "X-CSRF-Token": csrf})
    assert ok.status_code == 200, ok.text
    assert client.get("/v1/projects").status_code == 200  # safe methods need no token


def test_bearer_clients_are_unaffected_by_csrf(client, headers):
    body = {**SOURCE, "sizeBytes": 12}
    client.cookies.clear()
    assert client.post("/v1/uploads", json=body, headers=headers).status_code == 200


def test_refresh_rotates_tokens_and_access_cookies_expire(client, clock, settings):
    browser_login(client)
    first_access, first_refresh = cookie(client, ACCESS_COOKIE), cookie(client, REFRESH_COOKIE)
    clock.advance(settings.session_access_seconds + 1)
    assert_error(client.get("/v1/me"), 401, "UNAUTHENTICATED")
    refreshed = client.post("/v1/auth/refresh", headers=ORIGIN)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["expiresIn"] == settings.session_access_seconds
    assert cookie(client, ACCESS_COOKIE) != first_access
    assert cookie(client, REFRESH_COOKIE) != first_refresh
    assert client.get("/v1/me").status_code == 200


def replay(client, refresh_token):
    client.cookies.clear()
    return client.post(
        "/v1/auth/refresh", headers={**ORIGIN, "Cookie": f"{REFRESH_COOKIE}={refresh_token}"}
    )


def test_replayed_refresh_token_revokes_the_whole_session(client, clock, database):
    browser_login(client)
    stolen = cookie(client, REFRESH_COOKIE)
    assert client.post("/v1/auth/refresh", headers=ORIGIN).status_code == 200
    current_access = cookie(client, ACCESS_COOKIE)
    clock.advance(30)  # past the concurrent-refresh grace window
    response = replay(client, stolen)
    assert_error(response, 401, "UNAUTHENTICATED")
    assert f"{ACCESS_COOKIE}=" in response.headers["set-cookie"]  # cleared
    with database.sessions() as session:
        assert session.scalar(select(AuthSessionRecord)).revokedAt is not None
    # The legitimate browser's newer tokens are revoked too.
    client.cookies.clear()
    assert_error(
        client.get("/v1/me", headers={"Cookie": f"{ACCESS_COOKIE}={current_access}"}),
        401,
        "UNAUTHENTICATED",
    )


def test_concurrent_refresh_within_grace_does_not_revoke(client, database):
    browser_login(client)
    old = cookie(client, REFRESH_COOKIE)
    assert client.post("/v1/auth/refresh", headers=ORIGIN).status_code == 200
    fresh = cookie(client, ACCESS_COOKIE)
    assert_error(replay(client, old), 409, "REFRESH_IN_PROGRESS")
    client.cookies.clear()
    assert client.get("/v1/me", headers={"Cookie": f"{ACCESS_COOKIE}={fresh}"}).status_code == 200


def test_logout_revokes_the_session_and_clears_cookies(client):
    browser_login(client)
    refresh = cookie(client, REFRESH_COOKIE)
    response = client.post("/v1/auth/logout", headers=ORIGIN)
    assert response.json() == {"ok": True}
    assert any(
        c.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in c
        for c in response.headers.get_list("set-cookie")
    )
    assert_error(client.get("/v1/me"), 401, "UNAUTHENTICATED")
    assert_error(replay(client, refresh), 401, "UNAUTHENTICATED")


def test_sessions_end_after_the_refresh_lifetime_or_when_access_is_removed(
    client, clock, settings, database
):
    browser_login(client)
    clock.advance(settings.session_refresh_seconds + 1)
    assert_error(client.post("/v1/auth/refresh", headers=ORIGIN), 401, "UNAUTHENTICATED")
    browser_login(client)
    with database.transaction() as session:
        session.scalar(
            select(UserRecord).where(UserRecord.email == "maya@example.com")
        ).active = False
    clock.advance(settings.session_access_seconds + 1)
    assert_error(client.post("/v1/auth/refresh", headers=ORIGIN), 401, "UNAUTHENTICATED")


def test_refresh_rejects_foreign_origins(client):
    browser_login(client)
    assert_error(
        client.post("/v1/auth/refresh", headers={"Origin": "https://evil.example.com"}),
        403,
        "CSRF_FAILED",
    )


def test_cookies_are_secure_on_https_deployments(database, settings, clock):
    https = dataclasses.replace(
        settings,
        backend_public_url="https://api.shorts.example",
        cors_origins=("https://app.shorts.example",),
    )
    with TestClient(create_app(database=database, settings=https, clock=clock)) as client:
        response = client.post(
            "/v1/auth/google", json={"email": "maya@example.com", "password": "DemoPass123!"}
        )
        cookies = response.headers.get_list("set-cookie")
        assert all("Secure" in c and "HttpOnly" in c and "SameSite=lax" in c for c in cookies)


@pytest.mark.parametrize("enabled", [False])
def test_password_login_can_be_disabled(database, settings, clock, enabled):
    closed = dataclasses.replace(settings, allow_password_login=enabled)
    with TestClient(create_app(database=database, settings=closed, clock=clock)) as client:
        assert client.get("/v1/auth/options").json() == {"google": False, "password": False}
        response = client.post(
            "/v1/auth/google", json={"email": "maya@example.com", "password": "DemoPass123!"}
        )
        assert_error(response, 403, "PASSWORD_LOGIN_DISABLED")
