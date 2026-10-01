"""OAuth lifecycle. Network calls never hold a database transaction open."""

import secrets
from urllib.parse import urlparse

from sqlalchemy import delete, select

from ..db_models import UserRecord
from ..errors import ApiError
from ..store import oid
from .adapters.base import ProviderError
from .config import PLATFORMS
from .db_models import OAuthState, SocialAccount, SocialEvent, SocialRateLimit, SocialRefreshLease
from .security import TokenCipher, digest

SAFE_ERRORS = {
    "RECONNECT_REQUIRED": "This connection has expired. Reconnect the account to continue.",
    "PERMISSION_REQUIRED": "The account has not granted the required permissions. Reconnect it or check your developer app access.",
    "PRIVATE_POSTING_ONLY": "This integration can currently publish private posts only. Check the account and visibility settings.",
    "PROVIDER_RATE_LIMITED": "The platform is busy. Try again later.",
    "PROVIDER_UNAVAILABLE": "The platform could not be reached. Try again later.",
    "PROVIDER_REJECTED": "The platform rejected this request. Check your settings before trying again.",
    "PROVIDER_RESPONSE_INVALID": "The platform returned an unexpected response. Try again later.",
    "MEDIA_REJECTED": "This video does not meet this platform’s requirements. Render a compatible Short and try again.",
    "PUBLISH_UNCERTAIN": "The platform may have received this post. Check your account before posting again; automatic retry is paused to prevent duplicates.",
    "ACCOUNT_DISCONNECTED": "This account was disconnected. Connect it again before publishing.",
    "ASSET_EXPIRED": "This rendered video is no longer available. Render the Short again.",
}


def safe_error(error):
    return SAFE_ERRORS.get(
        error.code, "Publishing could not finish. Check the account and try again."
    )


def event(db, user_id, name, now, platform=None, resource=None):
    db.add(
        SocialEvent(
            id=oid("evt"),
            user_id=user_id,
            event=name,
            platform=platform,
            resource_id=resource,
            created_at=now,
        )
    )


def rate_limit(db, user_id, action, now, limit=6):
    window = now // 60_000
    record = db.get(SocialRateLimit, (user_id, action, window))
    if record and record.count >= limit:
        raise ApiError(
            "SOCIAL_RATE_LIMITED", "Too many requests. Wait a minute and try again.", 429
        )
    if record:
        record.count += 1
    else:
        db.add(SocialRateLimit(user_id=user_id, action=action, window=window, count=1))
    db.execute(delete(SocialRateLimit).where(SocialRateLimit.window < window - 60))


def account_view(account):
    return {
        key: getattr(account, key)
        for key in ("id", "platform", "account_name", "username", "avatar_url", "status")
    }


class AccountService:
    def __init__(self, database, settings, clock, adapters):
        self.database, self.settings, self.clock, self.adapters = (
            database,
            settings,
            clock,
            adapters,
        )
        self.cipher = TokenCipher(settings.encryption_keys)

    def now(self):
        return int(self.clock() * 1000)

    def adapter(self, platform):
        if (
            platform not in PLATFORMS
            or not self.settings.available(platform)
            or platform not in self.adapters
        ):
            raise ApiError("SOCIAL_UNAVAILABLE", "This platform is not configured yet.", 503)
        self.cipher.cipher()  # Fail closed on an invalid encryption key.
        return self.adapters[platform]

    def list(self, user_id):
        with self.database.transaction() as db:
            accounts = [
                account_view(a)
                for a in db.scalars(select(SocialAccount).where(SocialAccount.user_id == user_id))
            ]
        return {
            "accounts": accounts,
            "platforms": [
                {
                    "platform": p,
                    "enabled": self.settings.available(p) and p in self.adapters,
                    "notice": self.settings.notice(p),
                    "private_only": (p == "youtube" and not self.settings.youtube_verified)
                    or (p == "tiktok" and not self.settings.tiktok_audited),
                }
                for p in PLATFORMS
            ],
            "copy_available": bool(
                self.settings.copy_url and self.settings.copy_key and self.settings.copy_model
            ),
        }

    def connect(self, user_id, platform):
        adapter = self.adapter(platform)
        state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.database.transaction() as db:
            rate_limit(db, user_id, "connect", self.now())
            db.execute(delete(OAuthState).where(OAuthState.expires_at <= self.now()))
            db.add(
                OAuthState(
                    digest=digest(state),
                    user_id=user_id,
                    platform=platform,
                    browser_digest=digest(binding),
                    verifier_encrypted=self.cipher.encrypt(verifier, user_id, platform),
                    expires_at=self.now() + 600_000,
                )
            )
        return adapter.connect(state, verifier), binding

    def callback(self, platform, state, binding, code, denied=False):
        adapter = self.adapter(platform)
        with self.database.transaction() as db:
            record = db.get(OAuthState, digest(state or ""))
            if (
                not record
                or record.platform != platform
                or record.expires_at <= self.now()
                or not secrets.compare_digest(record.browser_digest, digest(binding or ""))
            ):
                raise ApiError(
                    "OAUTH_STATE_INVALID",
                    "This connection request expired. Start again from Social Accounts.",
                    400,
                )
            user_id = record.user_id
            user = db.get(UserRecord, user_id)
            if not user or not user.active or not user.invited:
                raise ApiError("ACCOUNT_DISABLED", "Your account is unavailable.", 403)
            verifier = self.cipher.decrypt(record.verifier_encrypted, user_id, platform)
            record.browser_digest = (
                ""  # Consume browser binding before provider I/O; disconnect can still cancel.
            )
        if denied or not code:
            self.discard_state(state)
            raise ApiError(
                "OAUTH_DENIED", "The connection was not approved. You can try again.", 400
            )
        try:
            tokens = adapter.exchange(code, verifier)
            if (
                not tokens.access
                or tokens.expires_in <= 0
                or not set(adapter.scopes).issubset(tokens.scopes)
            ):
                raise ProviderError("PERMISSION_REQUIRED")
            identity = adapter.identity(tokens.access)
        except ProviderError as error:
            self.discard_state(state)
            raise ApiError(error.code, safe_error(error), 400) from None
        except (KeyError, ValueError, TypeError, IndexError):
            self.discard_state(state)
            raise ApiError(
                "PROVIDER_RESPONSE_INVALID",
                "The platform returned an invalid account response. Try connecting again.",
                502,
            ) from None
        if not identity.id or not identity.name:
            self.discard_state(state)
            raise ApiError(
                "PROVIDER_RESPONSE_INVALID", "The platform did not return an account.", 502
            )
        with self.database.transaction() as db:
            pending = db.get(OAuthState, digest(state))
            if not pending or pending.expires_at <= self.now():
                raise ApiError(
                    "OAUTH_STATE_INVALID",
                    "This connection was canceled or expired. Start again from Social Accounts.",
                    400,
                )
            db.delete(pending)
            user = db.get(UserRecord, user_id)
            if not user or not user.active or not user.invited:
                raise ApiError("ACCOUNT_DISABLED", "Your account is unavailable.", 403)
            account = db.scalar(
                select(SocialAccount).where(
                    SocialAccount.user_id == user_id, SocialAccount.platform == platform
                )
            )
            if not account:
                account = SocialAccount(
                    id=oid("soc"),
                    user_id=user_id,
                    platform=platform,
                    created_at=self.now(),
                    version=0,
                )
                db.add(account)
            old_refresh = (
                account.refresh_token_encrypted if account.platform_user_id == identity.id else None
            )
            account.platform_user_id, account.account_name, account.username = (
                identity.id,
                identity.name[:255],
                (identity.username or "")[:255] or None,
            )
            account.avatar_url = (
                identity.avatar
                if identity.avatar and urlparse(identity.avatar).scheme == "https"
                else None
            )
            account.access_token_encrypted = self.cipher.encrypt(tokens.access, user_id, platform)
            account.refresh_token_encrypted = (
                self.cipher.encrypt(tokens.refresh, user_id, platform)
                if tokens.refresh
                else old_refresh
            )
            account.token_expires_at, account.scopes = (
                self.now() + tokens.expires_in * 1000,
                tokens.scopes,
            )
            account.updated_at, account.status, account.version = (
                self.now(),
                "connected",
                account.version + 1,
            )
            event(db, user_id, "social_account_connected", self.now(), platform, account.id)

    def discard_state(self, state):
        with self.database.transaction() as db:
            db.execute(delete(OAuthState).where(OAuthState.digest == digest(state)))

    def token(self, account_id, user_id, platform):
        adapter = self.adapter(platform)
        owner = oid("refresh")
        with self.database.transaction() as db:
            account = db.get(SocialAccount, account_id)
            if not account or account.user_id != user_id or account.platform != platform:
                raise ProviderError("ACCOUNT_DISCONNECTED")
            if account.status != "connected":
                raise ProviderError("RECONNECT_REQUIRED")
            if not set(adapter.scopes).issubset(account.scopes):
                raise ProviderError("PERMISSION_REQUIRED")
            if account.token_expires_at > self.now() + 120_000:
                return self.cipher.decrypt(account.access_token_encrypted, user_id, platform)
            lease = db.get(SocialRefreshLease, account_id)
            if lease and lease.expires_at > self.now():
                raise ProviderError("PROVIDER_UNAVAILABLE", retryable=True)
            if not lease:
                lease = SocialRefreshLease(account_id=account_id)
                db.add(lease)
            lease.owner, lease.expires_at = owner, self.now() + 120_000
            refresh = (
                self.cipher.decrypt(account.refresh_token_encrypted, user_id, platform)
                if account.refresh_token_encrypted
                else None
            )
            version, platform_user_id = account.version, account.platform_user_id
        try:
            if not refresh:
                raise ProviderError("RECONNECT_REQUIRED")
            tokens = adapter.refresh_token(refresh)
            if (
                not tokens.access
                or tokens.expires_in <= 0
                or not set(adapter.scopes).issubset(tokens.scopes)
            ):
                raise ProviderError("RECONNECT_REQUIRED")
            with self.database.transaction() as db:
                account = db.get(SocialAccount, account_id)
                if (
                    not account
                    or account.user_id != user_id
                    or account.platform_user_id != platform_user_id
                ):
                    raise ProviderError("ACCOUNT_DISCONNECTED")
                if account.version != version:
                    return self.cipher.decrypt(account.access_token_encrypted, user_id, platform)
                account.access_token_encrypted = self.cipher.encrypt(
                    tokens.access, user_id, platform
                )
                if tokens.refresh:
                    account.refresh_token_encrypted = self.cipher.encrypt(
                        tokens.refresh, user_id, platform
                    )
                account.token_expires_at, account.updated_at = (
                    self.now() + tokens.expires_in * 1000,
                    self.now(),
                )
                account.scopes = tokens.scopes
                account.version += 1
                return tokens.access
        except ProviderError as error:
            if error.code in {"RECONNECT_REQUIRED", "PERMISSION_REQUIRED"}:
                with self.database.transaction() as db:
                    account = db.get(SocialAccount, account_id)
                    if account and account.version == version:
                        account.status, account.updated_at = "reconnect_required", self.now()
            raise
        finally:
            with self.database.transaction() as db:
                lease = db.get(SocialRefreshLease, account_id)
                if lease and lease.owner == owner:
                    db.delete(lease)

    def disconnect(self, user_id, platform):
        with self.database.transaction() as db:
            account = db.scalar(
                select(SocialAccount).where(
                    SocialAccount.user_id == user_id, SocialAccount.platform == platform
                )
            )
            db.execute(
                delete(OAuthState).where(
                    OAuthState.user_id == user_id, OAuthState.platform == platform
                )
            )
            if not account:
                return
            try:
                token = self.cipher.decrypt(account.access_token_encrypted, user_id, platform)
            except ApiError:
                token = None
            db.delete(account)
            event(db, user_id, "social_account_disconnected", self.now(), platform)
        try:
            if token and platform in self.adapters:
                self.adapters[platform].disconnect(token)
        except ProviderError:
            # Local token deletion always succeeds, even if provider revocation is down.
            pass
