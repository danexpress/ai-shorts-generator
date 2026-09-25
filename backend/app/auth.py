"""Argon2id passwords and revocable opaque bearer tokens (hashed at rest)."""

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

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


def login(store: Store, email: str, password: str) -> dict:
    user = store.user_by_email(email)
    valid = password_hasher.verify(password, user.passwordHash if user else DUMMY_HASH)
    if not user or not valid:
        raise ApiError("UNAUTHENTICATED", "Invalid email or password.", 401)
    if not user.invited:
        raise ApiError("NOT_INVITED", "This account is not invited to the private beta.", 403)
    if not user.active:
        raise ApiError("ACCOUNT_DISABLED", "This account is disabled.", 403)
    token = secrets.token_urlsafe(32)
    store.add_token(token_digest(token), user.id)
    return dict(
        **store.session(user),
        access_token=token,
        token_type="bearer",
        expires_in=store.settings.token_ttl_seconds,
    )


def current_user(store: StoreDep, credentials: Credentials) -> UserRecord:
    token = store.token(token_digest(credentials.credentials)) if credentials else None
    if not token or token.expiresAt <= store.now():
        raise ApiError("UNAUTHENTICATED", "A valid bearer token is required.", 401)
    user = store.user(token.userId)
    if not user:
        raise ApiError("UNAUTHENTICATED", "Your session ended. Sign in again.", 401)
    if not user.active:
        raise ApiError("ACCOUNT_DISABLED", "This account is disabled.", 403)
    if not user.invited:
        raise ApiError("NOT_INVITED", "Your beta invitation was revoked.", 403)
    return user


UserDep = Annotated[UserRecord, Depends(current_user)]
