"""User/platform-bound encryption and short, shared-database authentication checks."""

import hashlib
import json
from typing import Annotated
from urllib.parse import parse_qs

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import Depends, Request

from ..auth import Credentials, token_digest
from ..db_models import TokenRecord, UserRecord
from ..errors import ApiError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class TokenCipher:
    def __init__(self, keys):
        self.keys = keys

    def cipher(self):
        try:
            return MultiFernet(
                [Fernet(key.strip().encode()) for key in self.keys.split(",") if key.strip()]
            )
        except (ValueError, TypeError):
            raise ApiError(
                "SOCIAL_UNAVAILABLE", "Social publishing needs server configuration.", 503
            ) from None

    def encrypt(self, value, user_id, platform):
        return (
            self.cipher()
            .encrypt(json.dumps({"owner": user_id, "platform": platform, "value": value}).encode())
            .decode()
        )

    def decrypt(self, value, user_id, platform):
        try:
            data = json.loads(self.cipher().decrypt(value.encode()))
            if data["owner"] != user_id or data["platform"] != platform:
                raise ValueError
            return data["value"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise ApiError(
                "RECONNECT_REQUIRED", "This connection needs to be reconnected.", 409
            ) from None


def social_user(request: Request, credentials: Credentials):
    with request.app.state.database.transaction() as session:
        token = (
            session.get(TokenRecord, token_digest(credentials.credentials)) if credentials else None
        )
        user = session.get(UserRecord, token.userId) if token else None
        if not token or token.expiresAt <= int(request.app.state.clock() * 1000) or not user:
            raise ApiError("UNAUTHENTICATED", "Sign in to continue.", 401)
        if not user.active or not user.invited:
            raise ApiError("ACCOUNT_DISABLED", "Your account is unavailable.", 403)
        return user.id


SocialUser = Annotated[str, Depends(social_user)]


class RedactOAuthQueries:
    """Keep authorization codes/state out of Uvicorn access logs, including errors."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("path", "").startswith("/v1/social-accounts/")
            and scope["path"].endswith("/callback")
        ):
            raw = scope.get("query_string", b"")
            scope["query_string"] = b""
            try:
                params = parse_qs(raw.decode(), max_num_fields=12)
                scope["social_callback_params"] = {
                    k: v[0] for k, v in params.items() if len(v) == 1
                }
            except (UnicodeDecodeError, ValueError):
                scope["social_callback_params"] = {}
        await self.app(scope, receive, send)
