import base64
import dataclasses
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey
from sqlalchemy import select

from app.db_models import UserRecord
from app.google_auth import GoogleOIDC
from app.main import create_app
from app.sessions import ACCESS_COOKIE, REFRESH_COOKIE

CLIENT_ID = "client-123.apps.googleusercontent.com"
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1", "alg": "RS256"}, private=True)
OTHER = RSAKey.generate_key(2048, parameters={"kid": "k1", "alg": "RS256"}, private=True)


class FakeGoogle:
    """Token and JWKS endpoints. The token endpoint checks PKCE and the client secret."""

    def __init__(self, clock):
        self.clock, self.claims, self.sign_with, self.header = clock, {}, KEY, {}
        self.challenge, self.jwks_calls, self.published = None, 0, [KEY]

    def handler(self, request: httpx.Request):
        if request.url.path == "/oauth2/v3/certs":
            self.jwks_calls += 1
            keys = KeySet(self.published).as_dict(private=False)
            return httpx.Response(200, json=keys, headers={"cache-control": "public, max-age=3600"})
        form = parse_qs(request.content.decode())
        verifier = form["code_verifier"][0]
        derived = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
        if derived.decode() != self.challenge or form["client_secret"] != ["secret"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        header = {"alg": "RS256", "kid": "k1", **self.header}
        return httpx.Response(
            200, json={"id_token": jwt.encode(header, self.claims, self.sign_with)}
        )


@pytest.fixture
def google_settings(settings):
    return dataclasses.replace(
        settings,
        google_client_id=CLIENT_ID,
        google_client_secret="secret",
        beta_invite_emails=frozenset({"new@example.com"}),
    )


@pytest.fixture
def idp(clock):
    return FakeGoogle(clock)


@pytest.fixture
def gclient(database, google_settings, clock, idp):
    google = GoogleOIDC(google_settings, transport=httpx.MockTransport(idp.handler), clock=clock)
    app = create_app(database=database, settings=google_settings, clock=clock, google=google)
    with TestClient(app) as client:
        yield client


def claims(idp, issued_nonce, **override):
    now = int(idp.clock())
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "new@example.com",
        "email_verified": True,
        "name": "New Creator",
        "picture": "https://lh3.googleusercontent.com/a/avatar",
        "iat": now,
        "exp": now + 3600,
        "nonce": issued_nonce,
    }
    return {**base, **override}


def sign_in(client, idp, drop=(), **override):
    start = client.get("/v1/auth/google/start", follow_redirects=False)
    assert start.status_code == 303, start.text
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.challenge = query["code_challenge"][0]
    issued = claims(idp, query["nonce"][0], **override)
    idp.claims = {k: v for k, v in issued.items() if k not in drop}
    return client.get(
        "/v1/auth/google/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        follow_redirects=False,
    )


def auth_error(response):
    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.netloc == "127.0.0.1:3000"
    return parse_qs(location.query).get("auth_error", [None])[0]


def test_start_requests_only_basic_scopes_with_pkce_and_browser_binding(gclient):
    start = gclient.get("/v1/auth/google/start", follow_redirects=False)
    url = urlparse(start.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["scope"] == "openid email profile"  # YouTube upload is requested later
    assert query["client_id"] == CLIENT_ID and query["response_type"] == "code"
    assert query["redirect_uri"] == "http://127.0.0.1:8000/v1/auth/google/callback"
    assert query["code_challenge_method"] == "S256" and len(query["state"]) >= 32
    cookie = start.headers["set-cookie"]
    assert "shorts_login=" in cookie and "HttpOnly" in cookie and "Path=/v1/auth/google" in cookie
    assert gclient.get("/v1/auth/options").json() == {"google": True, "password": True}


def test_new_invited_user_is_created_by_sub_and_signed_in(gclient, idp, database):
    response = sign_in(gclient, idp)
    assert auth_error(response) is None
    cookies = response.headers.get_list("set-cookie")
    assert any(
        c.startswith(f"{ACCESS_COOKIE}=") and "HttpOnly" in c and "SameSite=lax" in c
        for c in cookies
    )
    assert any(c.startswith(f"{REFRESH_COOKIE}=") and "Path=/v1/auth" in c for c in cookies)
    assert "auth-code" not in response.headers["location"]
    me = gclient.get("/v1/me").json()  # cookie only, no bearer header
    assert me["user"]["email"] == "new@example.com" and me["user"]["googleLinked"] is True
    assert me["user"]["avatarUrl"].startswith("https://") and me["csrfToken"]
    with database.sessions() as session:
        user = session.scalar(select(UserRecord).where(UserRecord.email == "new@example.com"))
        assert user.googleSub == "google-sub-1" and user.invited and user.role == "creator"


def test_users_are_found_by_sub_not_email(gclient, idp, database):
    first = sign_in(gclient, idp)
    user_id = gclient.get("/v1/me").json()["user"]["id"]
    gclient.post("/v1/auth/logout", headers={"Origin": "http://127.0.0.1:3000"})
    # Same Google account, changed email: still the same user.
    assert auth_error(sign_in(gclient, idp, email="renamed@example.com")) is None
    assert gclient.get("/v1/me").json()["user"]["id"] == user_id
    assert auth_error(first) is None
    # A different Google account presenting the linked email cannot take it over.
    gclient.cookies.clear()
    assert auth_error(sign_in(gclient, idp, sub="google-sub-2")) == "ACCOUNT_CONFLICT"


def test_existing_invited_account_is_linked_on_first_google_sign_in(gclient, idp, database):
    assert auth_error(sign_in(gclient, idp, email="maya@example.com", sub="maya-sub")) is None
    me = gclient.get("/v1/me").json()
    assert me["user"]["displayName"] == "Maya Ortiz" and me["user"]["googleLinked"]


@pytest.mark.parametrize(
    "email,code",
    [
        ("stranger@example.com", "NOT_INVITED"),
        ("sam@example.com", "NOT_INVITED"),
        ("disabled@example.com", "ACCOUNT_DISABLED"),
    ],
)
def test_access_rules_apply_to_google_accounts(gclient, idp, email, code):
    assert auth_error(sign_in(gclient, idp, email=email, sub="sub-" + email)) == code
    assert gclient.get("/v1/me").status_code == 401


def test_open_signup_creates_any_verified_google_account(database, google_settings, clock, idp):
    settings = dataclasses.replace(google_settings, open_signup=True)
    google = GoogleOIDC(settings, transport=httpx.MockTransport(idp.handler), clock=clock)
    with TestClient(
        create_app(database=database, settings=settings, clock=clock, google=google)
    ) as c:
        assert auth_error(sign_in(c, idp, email="anyone@example.com", sub="any")) is None


@pytest.mark.parametrize(
    "override,drop,code",
    [
        ({"aud": "someone-else"}, (), "INVALID_ID_TOKEN"),
        ({"iss": "https://evil.example.com"}, (), "INVALID_ID_TOKEN"),
        ({"exp": 1, "iat": 0}, (), "INVALID_ID_TOKEN"),
        ({"nonce": "replayed"}, (), "INVALID_ID_TOKEN"),
        ({}, ("sub",), "INVALID_ID_TOKEN"),
        ({"email_verified": False}, (), "EMAIL_NOT_VERIFIED"),
    ],
    ids=["audience", "issuer", "expired", "nonce", "no-sub", "unverified-email"],
)
def test_id_token_claims_are_verified(gclient, idp, override, drop, code):
    response = sign_in(gclient, idp, drop=drop, **override)
    assert auth_error(response) == code
    assert ACCESS_COOKIE not in response.headers.get("set-cookie", "")
    assert gclient.get("/v1/me").status_code == 401


def test_forged_signatures_and_algorithms_are_rejected(gclient, idp):
    idp.sign_with = OTHER  # same kid, wrong key
    assert auth_error(sign_in(gclient, idp)) == "INVALID_ID_TOKEN"
    idp.sign_with = OctKey.import_key(b"x" * 32)
    idp.header = {"alg": "HS256"}
    assert auth_error(sign_in(gclient, idp)) == "INVALID_ID_TOKEN"


def test_rotated_google_keys_are_refetched(gclient, idp):
    new_key = RSAKey.generate_key(2048, parameters={"kid": "k2", "alg": "RS256"}, private=True)
    assert auth_error(sign_in(gclient, idp)) is None  # caches keys {k1}
    gclient.cookies.clear()
    idp.published, idp.sign_with, idp.header = [new_key], new_key, {"kid": "k2"}
    assert auth_error(sign_in(gclient, idp, sub="google-sub-1")) is None
    assert idp.jwks_calls == 2


def test_state_is_single_use_browser_bound_and_short_lived(
    gclient, idp, clock, database, google_settings
):
    start = gclient.get("/v1/auth/google/start", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.challenge, idp.claims = query["code_challenge"][0], claims(idp, query["nonce"][0])
    params = {"code": "c", "state": query["state"][0]}
    # Another browser cannot complete this sign-in.
    with TestClient(gclient.app) as other:
        assert auth_error(
            other.get("/v1/auth/google/callback", params=params, follow_redirects=False)
        ) == ("LOGIN_STATE_INVALID")
    # ...and the attempt consumed the state, so the original browser must start again.
    assert auth_error(
        gclient.get("/v1/auth/google/callback", params=params, follow_redirects=False)
    ) == ("LOGIN_STATE_INVALID")
    start = gclient.get("/v1/auth/google/start", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    clock.advance(google_settings.login_state_seconds + 1)
    late = gclient.get(
        "/v1/auth/google/callback",
        params={"code": "c", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert auth_error(late) == "LOGIN_STATE_INVALID"
    denied = gclient.get(
        "/v1/auth/google/callback",
        params={"error": "access_denied", "state": "x"},
        follow_redirects=False,
    )
    assert auth_error(denied) == "GOOGLE_CANCELLED"


def test_unconfigured_google_sends_the_user_back_with_a_clear_code(client):
    assert client.get("/v1/auth/options").json()["google"] is False
    start = client.get("/v1/auth/google/start", follow_redirects=False)
    assert auth_error(start) == "GOOGLE_UNAVAILABLE"
