"""Explicit public schemas: OAuth secrets/checkpoints are never serialized."""

from typing import Literal

from pydantic import BaseModel

SocialPlatform = Literal["youtube", "tiktok", "x"]


class SocialAccountView(BaseModel):
    id: str
    platform: SocialPlatform
    account_name: str
    username: str | None
    avatar_url: str | None
    status: Literal["connected", "reconnect_required"]


class SocialCapability(BaseModel):
    platform: SocialPlatform
    enabled: bool
    notice: str | None
    private_only: bool = False


class SocialAccountsView(BaseModel):
    accounts: list[SocialAccountView]
    platforms: list[SocialCapability]
    copy_available: bool


class SocialConnectView(BaseModel):
    authorization_url: str


class SocialDisconnected(BaseModel):
    ok: bool = True
