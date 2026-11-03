"""User/platform-bound encryption and short, shared-database authentication checks."""

import base64
import hashlib
import json
import time
from typing import Annotated
from urllib.parse import parse_qs

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import Depends, Request

from ..auth import Credentials
from ..db_models import UserRecord
from ..errors import ApiError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


UNAVAILABLE = ("SOCIAL_UNAVAILABLE", "Social publishing needs server configuration.", 503)
KMS_PREFIX = "kms1:"
KMS_CONTEXT = {"purpose": "ai-shorts-social-tokens"}


class TokenCipher:
    """Fernet encryption of platform tokens and upload checkpoints, bound to owner+platform.

    Keys come from SOCIAL_TOKEN_ENCRYPTION_KEYS (first encrypts, all decrypt). With
    TOKEN_KMS_KEY_ID set, new values use envelope encryption instead: a data key from
    AWS KMS encrypts the value and is stored wrapped by KMS alongside it
    ("kms1:<wrapped key>.<fernet token>"). Data keys are reused briefly and cached to
    limit KMS calls. Existing Fernet values keep decrypting, so no migration is needed.
    """

    def __init__(
        self,
        keys,
        kms_key_id="",
        kms_client=None,
        clock=time.monotonic,
        kms_region="",
        reuse_seconds=300,
        reuse_count=10_000,
    ):
        self.keys, self.kms_key_id, self.kms_region = keys, kms_key_id, kms_region
        self._kms, self.clock = kms_client, clock
        self.reuse_seconds, self.reuse_count = reuse_seconds, reuse_count
        self._data_key = None  # (fernet, wrapped, created, uses)
        self._unwrapped = {}  # wrapped key -> Fernet, bounded

    def cipher(self):
        try:
            return MultiFernet(
                [Fernet(key.strip().encode()) for key in self.keys.split(",") if key.strip()]
            )
        except (ValueError, TypeError):
            raise ApiError(*UNAVAILABLE) from None

    def check(self):
        """Fail closed when no usable key is configured."""
        if not self.kms_key_id:
            self.cipher()

    def kms(self):
        if self._kms is None:
            try:
                import boto3
            except ImportError:
                raise ApiError(*UNAVAILABLE) from None  # `uv sync --extra kms`
            self._kms = boto3.client(
                "kms", **({"region_name": self.kms_region} if self.kms_region else {})
            )
        return self._kms

    def _kms_call(self, method, **kwargs):
        try:
            return getattr(self.kms(), method)(**kwargs)
        except ApiError:
            raise
        except Exception:  # noqa: BLE001 - botocore errors: fail closed, never store plaintext
            raise ApiError(*UNAVAILABLE) from None

    def _current_data_key(self):
        now = self.clock()
        key = self._data_key
        if key is None or now - key[2] > self.reuse_seconds or key[3] >= self.reuse_count:
            response = self._kms_call(
                "generate_data_key",
                KeyId=self.kms_key_id,
                KeySpec="AES_256",
                EncryptionContext=KMS_CONTEXT,
            )
            fernet = Fernet(base64.urlsafe_b64encode(response["Plaintext"]))
            key = (fernet, base64.urlsafe_b64encode(response["CiphertextBlob"]).decode(), now, 0)
        self._data_key = (key[0], key[1], key[2], key[3] + 1)
        return key[0], key[1]

    def _fernet_for(self, wrapped):
        if wrapped not in self._unwrapped:
            request = {
                "CiphertextBlob": base64.urlsafe_b64decode(wrapped),
                "EncryptionContext": KMS_CONTEXT,
            }
            if self.kms_key_id:
                request["KeyId"] = self.kms_key_id  # only accept data keys from this KMS key
            response = self._kms_call("decrypt", **request)
            if len(self._unwrapped) >= 256:
                self._unwrapped.pop(next(iter(self._unwrapped)))
            self._unwrapped[wrapped] = Fernet(base64.urlsafe_b64encode(response["Plaintext"]))
        return self._unwrapped[wrapped]

    def encrypt(self, value, user_id, platform):
        payload = json.dumps({"owner": user_id, "platform": platform, "value": value}).encode()
        if self.kms_key_id:
            fernet, wrapped = self._current_data_key()
            return f"{KMS_PREFIX}{wrapped}.{fernet.encrypt(payload).decode()}"
        return self.cipher().encrypt(payload).decode()

    def decrypt(self, value, user_id, platform):
        try:
            if value.startswith(KMS_PREFIX):
                wrapped, _, token = value[len(KMS_PREFIX) :].partition(".")
                raw = self._fernet_for(wrapped).decrypt(token.encode())
            else:
                raw = self.cipher().decrypt(value.encode())
            data = json.loads(raw)
            if data["owner"] != user_id or data["platform"] != platform:
                raise ValueError
            return data["value"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise ApiError(
                "RECONNECT_REQUIRED", "This connection needs to be reconnected.", 409
            ) from None


def social_user(request: Request, credentials: Credentials):
    from ..sessions import authenticate

    with request.app.state.database.transaction() as session:
        now = int(request.app.state.clock() * 1000)
        token = authenticate(session, request, credentials, request.app.state.settings, now)
        user = session.get(UserRecord, token.userId)
        if not user:
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
