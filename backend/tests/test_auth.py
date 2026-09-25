import pytest
from conftest import assert_error, login, record
from sqlalchemy import select

from app.auth import password_hasher, token_digest
from app.db_models import (
    TokenRecord,
    UserRecord,
)


def test_hashed_passwords_and_tokens(client, database, app):
    response = client.post(
        "/v1/auth/google", json={"email": " MAYA@example.com ", "password": "DemoPass123!"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 3600
    user = record(database, UserRecord, data["user"]["id"])
    assert user["passwordHash"].startswith("$argon2id$")
    assert password_hasher.verify("DemoPass123!", user["passwordHash"])
    assert "password" not in user
    assert record(database, TokenRecord, data["access_token"]) is None
    assert record(database, TokenRecord, token_digest(data["access_token"])) is not None
    assert "passwordHash" not in response.text


@pytest.mark.parametrize(
    "email,password,status,code",
    [
        ("maya@example.com", "wrong", 401, "UNAUTHENTICATED"),
        ("unknown@example.com", "DemoPass123!", 401, "UNAUTHENTICATED"),
        ("sam@example.com", "DemoPass123!", 403, "NOT_INVITED"),
        ("disabled@example.com", "DemoPass123!", 403, "ACCOUNT_DISABLED"),
    ],
)
def test_login_failures(client, email, password, status, code):
    response = client.post("/v1/auth/google", json={"email": email, "password": password})
    assert_error(response, status, code)
    assert password not in response.text


def test_password_required_and_never_echoed(client):
    assert_error(
        client.post("/v1/auth/google", json={"email": "maya@example.com"}), 422, "INVALID_INPUT"
    )
    secret = "very-private-value"
    response = client.post("/v1/auth/google", json={"email": "bad", "password": secret})
    assert_error(response, 422, "INVALID_INPUT")
    assert secret not in response.text


@pytest.mark.parametrize("auth", [None, "Bearer fake", "Basic abc", "Bearer"])
def test_invalid_authorization(client, auth):
    response = client.get("/v1/me", headers={"Authorization": auth} if auth else {})
    assert_error(response, 401, "UNAUTHENTICATED")
    assert response.headers["www-authenticate"] == "Bearer"


def test_logout_revokes_only_current_token(client, headers):
    other = login(client)
    assert client.post("/v1/auth/logout", headers=headers).json() == {"ok": True}
    assert_error(client.get("/v1/me", headers=headers), 401, "UNAUTHENTICATED")
    assert client.get("/v1/me", headers=other).status_code == 200
    assert client.post("/v1/auth/logout").json() == {"ok": True}


def test_token_expiry(client, headers, clock):
    clock.advance(3600)
    assert_error(client.get("/v1/me", headers=headers), 401, "UNAUTHENTICATED")


@pytest.mark.parametrize("field,code", [("active", "ACCOUNT_DISABLED"), ("invited", "NOT_INVITED")])
def test_account_changes_invalidate_access(client, headers, database, app, field, code):
    with database.transaction() as session:
        user = session.scalar(select(UserRecord).where(UserRecord.email == "maya@example.com"))
        setattr(user, field, False)
    assert_error(client.get("/v1/me", headers=headers), 403, code)


def test_seeded_history_and_isolation(client, headers):
    projects = client.get("/v1/projects", headers=headers).json()
    assert {"ready", "failed", "expired"} <= {p["status"] for p in projects}
    assert all("userId" not in p and "sourceReady" not in p for p in projects)
    assert client.get("/v1/usage", headers=headers).json()["usedMinutes"] == 19
    assert client.get("/v1/projects", headers=login(client, "ops@example.com")).json() == []
