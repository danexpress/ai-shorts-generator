"""Google Sign-In (OpenID Connect authorization-code flow with PKCE).

Only `openid email profile` is requested here. YouTube upload access is a
separate, later consent when the creator connects YouTube (incremental auth).
The ID token is verified server-side: RS256 signature against Google's published
keys, issuer, audience (our client ID), expiry and the nonce we generated.
"""

import base64
import hashlib
import logging
import re
import time
from urllib.parse import urlencode

import httpx
from joserfc import jws, jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
SCOPES = "openid email profile"


class GoogleAuthError(Exception):
    """Sign-in failed; `code` is safe to show (never provider text or tokens)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


class GoogleOIDC:
    def __init__(self, settings, transport=None, clock=time.time):
        self.settings, self.clock = settings, clock
        self.http = httpx.Client(timeout=10, follow_redirects=False, transport=transport)
        self._keys: KeySet | None = None
        self._keys_expire = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.settings.google_client_id and self.settings.google_client_secret)

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.backend_public_url}/v1/auth/google/callback"

    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                {
                    "client_id": self.settings.google_client_id,
                    "redirect_uri": self.redirect_uri,
                    "response_type": "code",
                    "scope": SCOPES,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge(verifier),
                    "code_challenge_method": "S256",
                    "prompt": "select_account",
                }
            )
        )

    def exchange(self, code: str, verifier: str) -> str:
        """Authorization code -> ID token (only the ID token is used)."""
        try:
            response = self.http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleAuthError("GOOGLE_UNAVAILABLE") from exc
        if response.status_code != 200:
            logger.warning("Google token exchange failed with HTTP %d", response.status_code)
            raise GoogleAuthError("GOOGLE_SIGNIN_FAILED")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise GoogleAuthError("GOOGLE_SIGNIN_FAILED")
        return id_token

    def keys(self, refresh: bool = False) -> KeySet:
        if self._keys is None or refresh or self.clock() >= self._keys_expire:
            try:
                response = self.http.get(JWKS_URL)
                response.raise_for_status()
                self._keys = KeySet.import_key_set(response.json())
            except (httpx.HTTPError, ValueError, JoseError) as exc:
                raise GoogleAuthError("GOOGLE_UNAVAILABLE") from exc
            match = re.search(r"max-age=(\d+)", response.headers.get("cache-control", ""))
            self._keys_expire = self.clock() + min(int(match.group(1)) if match else 3600, 86_400)
        return self._keys

    def verify(self, id_token: str, nonce: str) -> dict:
        try:
            header = jws.extract_compact(id_token.encode()).protected
        except (JoseError, ValueError) as exc:
            raise GoogleAuthError("INVALID_ID_TOKEN") from exc
        keys = self.keys()
        if header.get("kid") not in {key.kid for key in keys.keys}:
            keys = self.keys(refresh=True)  # Google rotated its signing keys
        try:
            token = jwt.decode(id_token, keys, algorithms=["RS256"])
            jwt.JWTClaimsRegistry(
                now=int(self.clock()),
                leeway=60,
                iss={"essential": True, "values": ISSUERS},
                aud={"essential": True, "value": self.settings.google_client_id},
                sub={"essential": True},
                exp={"essential": True},
                nonce={"essential": True, "value": nonce},
            ).validate(token.claims)
        except (JoseError, ValueError) as exc:
            logger.info("Rejected Google ID token: %s", type(exc).__name__)
            raise GoogleAuthError("INVALID_ID_TOKEN") from exc
        claims = token.claims
        if claims.get("email_verified") is not True or not claims.get("email"):
            raise GoogleAuthError("EMAIL_NOT_VERIFIED")
        return claims

    def close(self):
        self.http.close()
