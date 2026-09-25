from typing import Annotated

from fastapi import APIRouter, Header, Response

from ..auth import StoreDep, UserDep, login, token_digest
from ..models import LoginResponse, Ok, Session, SignInRequest

router = APIRouter(tags=["Authentication"])


@router.post("/v1/auth/google", response_model=LoginResponse, operation_id="signIn")
def sign_in(body: SignInRequest, store: StoreDep, response: Response):
    """Password login on the existing frontend route; this is not Google OAuth."""
    response.headers["Cache-Control"] = "no-store"
    return login(store, body.email, body.password.get_secret_value())


@router.post("/v1/auth/logout", response_model=Ok, operation_id="signOut")
def sign_out(store: StoreDep, authorization: Annotated[str | None, Header()] = None):
    """Revoke the presented token. Also succeeds without an active session."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and token:
        store.revoke_token(token_digest(token))
    return Ok()


@router.get("/v1/me", response_model=Session, operation_id="me")
def me(store: StoreDep, user: UserDep):
    return store.session(user)
