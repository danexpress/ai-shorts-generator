"""Additive portable tables; no changes to existing user or render columns."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db_models import Base


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_social_user_platform"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    platform_user_id: Mapped[str] = mapped_column(String(128))
    account_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[int] = mapped_column(BigInteger)
    scopes: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="connected")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class OAuthState(Base):
    __tablename__ = "social_oauth_states"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    browser_digest: Mapped[str] = mapped_column(String(64))
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class RenderContext(Base):
    __tablename__ = "render_social_context"
    render_id: Mapped[str] = mapped_column(
        ForeignKey("renders.id", ondelete="CASCADE"), primary_key=True
    )
    context: Mapped[dict] = mapped_column(JSON)


class SocialCopy(Base):
    __tablename__ = "social_copy"
    render_id: Mapped[str] = mapped_column(
        ForeignKey("renders.id", ondelete="CASCADE"), primary_key=True
    )
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[int] = mapped_column(BigInteger)


class PublishBatch(Base):
    __tablename__ = "publish_batches"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_publish_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # Retain history after the rendered file expires. Ownership is also on the batch.
    render_id: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger)


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        UniqueConstraint(
            "render_id", "platform", "platform_user_id", "user_id", name="uq_publish_render_target"
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    publish_batch_id: Mapped[str] = mapped_column(ForeignKey("publish_batches.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    render_id: Mapped[str] = mapped_column(String(40), index=True)
    platform: Mapped[str] = mapped_column(String(16))
    platform_user_id: Mapped[str] = mapped_column(String(128))
    social_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    post_metadata: Mapped[dict] = mapped_column("post_metadata", JSON)
    checkpoint_encrypted: Mapped[str | None] = mapped_column(Text)
    platform_post_id: Mapped[str | None] = mapped_column(String(128))
    platform_post_url: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message_safe: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[int | None] = mapped_column(BigInteger)
    next_attempt_at: Mapped[int] = mapped_column(BigInteger, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(40))
    lease_until: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class SocialEvent(Base):
    __tablename__ = "social_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    event: Mapped[str] = mapped_column(String(60))
    platform: Mapped[str | None] = mapped_column(String(16))
    resource_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)


class SocialRateLimit(Base):
    __tablename__ = "social_rate_limits"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    action: Mapped[str] = mapped_column(String(30), primary_key=True)
    window: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class SocialRefreshLease(Base):
    __tablename__ = "social_refresh_leases"
    account_id: Mapped[str] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    owner: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[int] = mapped_column(BigInteger)
