import hmac
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete

from .. import sessions
from ..auth import StoreDep, UserDep, google_user, login, token_digest
from ..db_models import LoginStateRecord, RefreshTokenRecord
from ..errors import ApiError
from ..google_auth import GoogleAuthError
from ..models import AuthOptions, LoginResponse, Ok, RefreshResult, Session, SignInRequest

router = APIRouter(tags=["Authentication"])
LOGIN_COOKIE = "shorts_login"  # binds a pending Google sign-in to this browser
LOGIN_COOKIE_PATH = "/v1/auth/google"


@router.get("/v1/auth/options", response_model=AuthOptions, operation_id="getAuthOptions")
def auth_options(request: Request):
    """Public: which sign-in methods the login screen should offer."""
    settings = request.app.state.settings
    return {
        "google": request.app.state.google.configured,
        "password": settings.allow_password_login,
    }


def _to_frontend(settings, error: str | None = None) -> RedirectResponse:
    query = f"?auth_error={error}" if error else ""
    response = RedirectResponse(f"{settings.frontend_public_url}/{query}", status_code=303)
    response.delete_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get(
    "/v1/auth/google/start",
    operation_id="startGoogleSignIn",
    status_code=303,
    response_class=RedirectResponse,
)
def google_start(request: Request, store: StoreDep):
    """Public: redirect to Google with state, nonce and a PKCE challenge."""
    google, settings = request.app.state.google, store.settings
    if not google.configured:
        return _to_frontend(settings, "GOOGLE_UNAVAILABLE")
    now = store.now()
    store.db.execute(delete(LoginStateRecord).where(LoginStateRecord.expiresAt <= now))
    state, nonce, browser = (secrets.token_urlsafe(32) for _ in range(3))
    verifier = secrets.token_urlsafe(64)
    store.db.add(
        LoginStateRecord(
            digest=token_digest(state),
            browserDigest=token_digest(browser),
            nonce=nonce,
            verifier=verifier,
            expiresAt=now + settings.login_state_seconds * 1000,
        )
    )
    response = RedirectResponse(google.authorization_url(state, nonce, verifier), status_code=303)
    response.set_cookie(
        LOGIN_COOKIE,
        browser,
        max_age=settings.login_state_seconds,
        httponly=True,
        secure=sessions.cookie_secure(settings),
        samesite="lax",
        path=LOGIN_COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get(
    "/v1/auth/google/callback",
    operation_id="finishGoogleSignIn",
    status_code=303,
    response_class=RedirectResponse,
)
def google_callback(
    request: Request,
    store: StoreDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Public: verify Google's response, find or create the user, start a session."""
    settings, google = store.settings, request.app.state.google
    # State is single-use: consume it before any provider exchange.
    record = store.db.get(LoginStateRecord, token_digest(state)) if state else None
    if record is not None:
        store.db.delete(record)
    if error:
        return _to_frontend(
            settings, "GOOGLE_CANCELLED" if error == "access_denied" else "GOOGLE_SIGNIN_FAILED"
        )
    browser = request.cookies.get(LOGIN_COOKIE, "")
    if (
        record is None
        or record.expiresAt <= store.now()
        or not browser
        or not hmac.compare_digest(token_digest(browser), record.browserDigest)
        or not code
    ):
        return _to_frontend(settings, "LOGIN_STATE_INVALID")
    try:
        claims = google.verify(google.exchange(code, record.verifier), record.nonce)
        user = google_user(store, claims)
    except GoogleAuthError as exc:
        return _to_frontend(settings, exc.code)
    except ApiError as exc:
        return _to_frontend(settings, exc.code)
    response = _to_frontend(settings)
    sessions.start(store, user, "google", response)
    return response


@router.post("/v1/auth/google", response_model=LoginResponse, operation_id="signIn")
def sign_in(body: SignInRequest, store: StoreDep, response: Response):
    """Email/password login (historical path; Google sign-in uses /v1/auth/google/start).

    Starts a cookie session for browsers and also returns a bearer token for API clients.
    """
    user, payload = login(store, body.email, body.password.get_secret_value())
    session = sessions.start(store, user, "password", response)
    return {**payload, "csrfToken": session.csrfToken}


@router.post("/v1/auth/refresh", response_model=RefreshResult, operation_id="refreshSession")
def refresh(request: Request, store: StoreDep, response: Response):
    """Rotate the session cookies using the refresh cookie."""
    sessions.check_origin(request, store.settings)
    result = sessions.rotate(store, request.cookies.get(sessions.REFRESH_COOKIE), response)
    if isinstance(result, str):
        # Returned (not raised) so a theft-triggered revocation is committed.
        busy = result == "REFRESH_IN_PROGRESS"
        failure = JSONResponse(
            status_code=409 if busy else 401,
            content={
                "error": {
                    "code": result,
                    "message": "Retry the request."
                    if busy
                    else "Your session ended. Sign in again.",
                }
            },
        )
        if not busy:
            sessions.clear_cookies(failure, store.settings)
        return failure
    return {"csrfToken": result.csrfToken, "expiresIn": store.settings.session_access_seconds}


@router.post("/v1/auth/logout", response_model=Ok, operation_id="signOut")
def sign_out(
    request: Request,
    store: StoreDep,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
):
    """Revoke the presented bearer token and/or this browser's session; clear cookies."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and token:
        store.revoke_token(token_digest(token))
    sessions.check_origin(request, store.settings)
    found = sessions.session_for_access(store, request.cookies.get(sessions.ACCESS_COOKIE))
    session_id = found[1].id if found else None
    refresh_token = request.cookies.get(sessions.REFRESH_COOKIE)
    if session_id is None and refresh_token:
        record = store.db.get(RefreshTokenRecord, token_digest(refresh_token))
        session_id = record.sessionId if record else None
    if session_id:
        sessions.revoke(store, session_id)
    sessions.clear_cookies(response, store.settings)
    return Ok()


@router.get("/v1/me", response_model=Session, operation_id="me")
def me(request: Request, store: StoreDep, user: UserDep):
    return store.session(user, getattr(request.state, "auth_session", None))
