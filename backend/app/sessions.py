"""Browser sessions in httpOnly cookies with rotating, single-use refresh tokens.

A session has a short-lived access token (cookie `shorts_session`) and a refresh
token (cookie `shorts_refresh`, sent only to /v1/auth). Every refresh issues a new
pair. Presenting an already-used refresh token outside a short grace window is
treated as theft and revokes the whole session. Tokens are stored as SHA-256
digests only. Unsafe requests authenticated by cookie must carry the session's
CSRF token in `X-CSRF-Token` and come from an allowed Origin.
"""

import hmac
import secrets
from urllib.parse import urlparse

from fastapi import Request, Response
from sqlalchemy import delete, select, update

from .db_models import AuthSessionRecord, RefreshTokenRecord, TokenRecord, UserRecord
from .errors import ApiError

ACCESS_COOKIE = "shorts_session"
REFRESH_COOKIE = "shorts_refresh"
REFRESH_PATH = "/v1/auth"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Two tabs refreshing at once both present the same token; that is not theft.
REUSE_GRACE_MS = 10_000


def digest(token: str) -> str:
    from .auth import token_digest

    return token_digest(token)


def cookie_secure(settings) -> bool:
    """Secure everywhere except a plain-HTTP loopback backend in development."""
    parsed = urlparse(settings.backend_public_url)
    return not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})


def _issue(store, session: AuthSessionRecord) -> tuple[str, str]:
    now = store.now()
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store.db.add(
        TokenRecord(
            digest=digest(access),
            userId=session.userId,
            sessionId=session.id,
            expiresAt=now + store.settings.session_access_seconds * 1000,
        )
    )
    store.db.add(
        RefreshTokenRecord(
            digest=digest(refresh), sessionId=session.id, expiresAt=session.expiresAt, usedAt=None
        )
    )
    return access, refresh


def start(store, user: UserRecord, method: str, response: Response) -> AuthSessionRecord:
    from .store import oid

    now = store.now()
    session = AuthSessionRecord(
        id=oid("ses")[:40],
        userId=user.id,
        csrfToken=secrets.token_urlsafe(32),
        method=method,
        createdAt=now,
        expiresAt=now + store.settings.session_refresh_seconds * 1000,
        revokedAt=None,
    )
    store.db.add(session)
    store.db.flush()
    set_cookies(response, store.settings, *_issue(store, session))
    return session


def rotate(store, refresh_token: str | None, response: Response) -> AuthSessionRecord | str:
    """Rotate the token pair. Returns the session, or an error code.

    Errors are returned rather than raised so the caller can commit a revocation:
    raising would roll back the request transaction.
    """
    now = store.now()
    record = store.db.get(RefreshTokenRecord, digest(refresh_token)) if refresh_token else None
    session = store.db.get(AuthSessionRecord, record.sessionId) if record else None
    if not record or not session or session.revokedAt or session.expiresAt <= now:
        return "UNAUTHENTICATED"
    if record.usedAt is not None:
        if now - record.usedAt <= REUSE_GRACE_MS:
            return "REFRESH_IN_PROGRESS"
        revoke(store, session.id)  # replayed refresh token: assume it was stolen
        return "UNAUTHENTICATED"
    record.usedAt = now
    user = store.user(session.userId)
    if not user or not user.active or not user.invited:
        revoke(store, session.id)
        return "UNAUTHENTICATED"
    set_cookies(response, store.settings, *_issue(store, session))
    return session


def revoke(store, session_id: str):
    store.db.execute(
        update(AuthSessionRecord)
        .where(AuthSessionRecord.id == session_id, AuthSessionRecord.revokedAt.is_(None))
        .values(revokedAt=store.now())
    )
    store.db.execute(delete(TokenRecord).where(TokenRecord.sessionId == session_id))
    store.db.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.sessionId == session_id))


def revoke_all(store, user_id: str):
    for session_id in store.db.scalars(
        select(AuthSessionRecord.id).where(AuthSessionRecord.userId == user_id)
    ):
        revoke(store, session_id)


def session_for_access(store, access_token: str | None):
    """(TokenRecord, AuthSessionRecord) for a live cookie access token, else None."""
    token = store.token(digest(access_token)) if access_token else None
    if not token or token.expiresAt <= store.now() or not token.sessionId:
        return None
    session = store.db.get(AuthSessionRecord, token.sessionId)
    if not session or session.revokedAt or session.expiresAt <= store.now():
        return None
    return token, session


def authenticate(db, request: Request, credentials, settings, now: int) -> TokenRecord:
    """The live token for this request: bearer header first, else the session cookie.

    Cookie-authenticated unsafe requests must pass the CSRF and Origin checks. Shared
    by the main API and the social endpoints (which use their own short transactions).
    """
    if credentials:
        token = db.get(TokenRecord, digest(credentials.credentials))
        if not token or token.expiresAt <= now:
            raise ApiError("UNAUTHENTICATED", "A valid bearer token is required.", 401)
        return token
    access = request.cookies.get(ACCESS_COOKIE)
    token = db.get(TokenRecord, digest(access)) if access else None
    session = db.get(AuthSessionRecord, token.sessionId) if token and token.sessionId else None
    if (
        not token
        or token.expiresAt <= now
        or not session
        or session.revokedAt
        or session.expiresAt <= now
    ):
        raise ApiError("UNAUTHENTICATED", "A valid bearer token is required.", 401)
    check_csrf(request, session, settings)
    request.state.auth_session = session
    return token


def check_origin(request: Request, settings):
    origin = request.headers.get("origin")
    allowed = set(settings.cors_origins) | {settings.backend_public_url}
    if origin and origin.rstrip("/") not in allowed:
        raise ApiError("CSRF_FAILED", "This request came from an unexpected site.", 403)


def check_csrf(request: Request, session: AuthSessionRecord, settings):
    if request.method in SAFE_METHODS:
        return
    check_origin(request, settings)
    presented = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(presented.encode(), session.csrfToken.encode()):
        raise ApiError("CSRF_FAILED", "Refresh the page and try again.", 403)


def set_cookies(response: Response, settings, access: str, refresh: str):
    secure = cookie_secure(settings)
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.session_access_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.session_refresh_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=REFRESH_PATH,
    )
    response.headers["Cache-Control"] = "no-store"


def clear_cookies(response: Response, settings):
    secure = cookie_secure(settings)
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, secure=secure, httponly=True, samesite="lax"
    )
