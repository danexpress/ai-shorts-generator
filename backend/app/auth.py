"""Authentication: Google sign-in, password login, cookie sessions and bearer tokens.

Browsers use cookie sessions (see sessions.py). Bearer tokens from password login
remain for API clients and tests. Every opaque token is stored hashed.
"""

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select

from .db_models import UserRecord
from .errors import ApiError
from .store import Store, get_store

password_hasher = PasswordHash.recommended()
# Verify a real hash even for an unknown email, avoiding a fast account-existence path.
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False, scheme_name="bearerAuth")
StoreDep = Annotated[Store, Depends(get_store, scope="function")]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_access(user: UserRecord):
    if not user.invited:
        raise ApiError("NOT_INVITED", "This account is not invited to the private beta.", 403)
    if not user.active:
        raise ApiError("ACCOUNT_DISABLED", "This account is disabled.", 403)


def google_user(store: Store, claims: dict) -> UserRecord:
    """Find the user by Google `sub`; email only links a pre-existing invite once."""
    sub, email = claims["sub"], claims["email"].strip().lower()
    name = (claims.get("name") or email.split("@")[0])[:255]
    picture = (
        claims.get("picture") if str(claims.get("picture", "")).startswith("https://") else None
    )
    user = store.db.scalar(select(UserRecord).where(UserRecord.googleSub == sub))
    if user is None:
        existing = store.user_by_email(email)
        if existing is not None:
            if existing.googleSub is not None:
                raise ApiError(
                    "ACCOUNT_CONFLICT", "This email belongs to a different Google account.", 409
                )
            user, user.googleSub = existing, sub
        elif store.settings.open_signup or email in store.settings.beta_invite_emails:
            from .store import oid

            user = UserRecord(
                id=oid("usr"),
                email=email,
                displayName=name,
                role="creator",
                invited=True,
                active=True,
                limitMinutes=60,
                unlimitedUsage=False,
                # Google users have no password; this hash matches nothing.
                passwordHash=hash_password(secrets.token_urlsafe(32)),
                googleSub=sub,
            )
            store.db.add(user)
        else:
            raise ApiError("NOT_INVITED", "This account is not invited to the private beta.", 403)
    check_access(user)
    user.avatarUrl = picture
    if not user.displayName:
        user.displayName = name
    store.db.flush()
    return user


def login(store: Store, email: str, password: str) -> tuple[UserRecord, dict]:
    if not store.settings.allow_password_login:
        raise ApiError("PASSWORD_LOGIN_DISABLED", "Sign in with Google.", 403)
    user = store.user_by_email(email)
    valid = password_hasher.verify(password, user.passwordHash if user else DUMMY_HASH)
    if not user or not valid:
        raise ApiError("UNAUTHENTICATED", "Invalid email or password.", 401)
    check_access(user)
    token = secrets.token_urlsafe(32)
    store.add_token(token_digest(token), user.id)
    return user, dict(
        **store.session(user),
        access_token=token,
        token_type="bearer",
        expires_in=store.settings.token_ttl_seconds,
    )


def current_user(request: Request, store: StoreDep, credentials: Credentials) -> UserRecord:
    from . import sessions

    if credentials:
        token = store.token(token_digest(credentials.credentials))
        if not token or token.expiresAt <= store.now():
            raise ApiError("UNAUTHENTICATED", "A valid bearer token is required.", 401)
    else:
        found = sessions.session_for_access(store, request.cookies.get(sessions.ACCESS_COOKIE))
        if not found:
            raise ApiError("UNAUTHENTICATED", "A valid bearer token is required.", 401)
        token, session = found
        sessions.check_csrf(request, session, store.settings)
        request.state.auth_session = session
    user = store.user(token.userId)
    if not user:
        raise ApiError("UNAUTHENTICATED", "Your session ended. Sign in again.", 401)
    if not user.active:
        raise ApiError("ACCOUNT_DISABLED", "This account is disabled.", 403)
    if not user.invited:
        raise ApiError("NOT_INVITED", "Your beta invitation was revoked.", 403)
    return user


UserDep = Annotated[UserRecord, Depends(current_user)]
