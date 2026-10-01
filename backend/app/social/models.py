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


from pydantic import ConfigDict, Field, field_validator, model_validator


class SocialInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def clean(cls, value):
        if isinstance(value, str) and any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Control characters are not supported")
        return value


class YouTubeCopy(SocialInput):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=4800)
    hashtags: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def description_bytes(self):
        if len((self.description + "\n\n" + " ".join(self.hashtags)).encode("utf-8")) > 5000:
            raise ValueError("YouTube description and hashtags are too long")
        return self


class TikTokCopy(SocialInput):
    caption: str = Field(min_length=1, max_length=2200)
    hashtags: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def caption_length(self):
        if len((self.caption + " " + " ".join(self.hashtags)).encode("utf-16-le")) // 2 > 2200:
            raise ValueError("TikTok caption is too long")
        return self


class XCopy(SocialInput):
    text: str = Field(min_length=1, max_length=280)

    @field_validator("text")
    @classmethod
    def weighted_length(cls, value):
        # Conservative twitter-text weight bound; compound emoji/URLs may use less.
        weight = sum(
            1
            if ord(c) <= 0x10FF
            or 0x2000 <= ord(c) <= 0x200D
            or 0x2010 <= ord(c) <= 0x201F
            or 0x2032 <= ord(c) <= 0x2037
            else 2
            for c in value
        )
        if weight > 280:
            raise ValueError("X text is too long")
        return value


class HashtagRules:
    @staticmethod
    def validate(content):
        import re

        tags = content.get("hashtags", [])
        if any(not re.fullmatch(r"#[\w]{1,50}", tag) for tag in tags) or len(
            {t.lower() for t in tags}
        ) != len(tags):
            raise ValueError("Invalid or duplicate hashtags")
        if any("<" in v or ">" in v for v in content.values() if isinstance(v, str)):
            raise ValueError("Markup is not supported")
        return content


class SocialCopyBundle(SocialInput):
    youtube: YouTubeCopy
    tiktok: TikTokCopy
    x: XCopy


class SocialCopyView(BaseModel):
    content: dict[SocialPlatform, YouTubeCopy | TikTokCopy | XCopy] = Field(alias="copy")
    updated_at: int | None = None
    source_transcript: str | None = None
    transcript_verified: bool = False


class SocialGenerateRequest(SocialInput):
    transcript_confirmed: bool = False
    platforms: list[SocialPlatform] = Field(
        default_factory=lambda: ["youtube", "tiktok", "x"], min_length=1, max_length=3
    )

    @field_validator("platforms")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate platform")
        return value


class YouTubePost(YouTubeCopy):
    use_thumbnail: bool = False
    visibility: Literal["public", "unlisted", "private"] = "private"
    made_for_kids: bool = False
    contains_synthetic_media: bool = False


class TikTokPost(TikTokCopy):
    privacy: Literal[
        "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"
    ]
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    brand_content: bool = False
    brand_organic: bool = False
    is_aigc: bool = False
    consent: bool = False

    @model_validator(mode="after")
    def approval(self):
        if not self.consent or (self.brand_content and self.privacy == "SELF_ONLY"):
            raise ValueError("Consent and compatible branded-content visibility are required")
        return self


class SocialPublishTarget(SocialInput):
    platform: SocialPlatform
    social_account_id: str = Field(min_length=1, max_length=40)
    youtube: YouTubePost | None = None
    tiktok: TikTokPost | None = None
    x: XCopy | None = None

    @model_validator(mode="after")
    def selected_metadata(self):
        if getattr(self, self.platform) is None or any(
            getattr(self, p) is not None for p in ["youtube", "tiktok", "x"] if p != self.platform
        ):
            raise ValueError("Provide only the selected platform metadata")
        HashtagRules.validate(getattr(self, self.platform).model_dump())
        return self


class SocialPublishRequest(SocialInput):
    targets: list[SocialPublishTarget] = Field(min_length=1, max_length=3)
    approved: bool

    @model_validator(mode="after")
    def approval(self):
        if not self.approved or len({t.platform for t in self.targets}) != len(self.targets):
            raise ValueError("Explicit approval and unique platforms are required")
        return self


class SocialPublishJobView(BaseModel):
    id: str
    platform: SocialPlatform
    social_account_id: str | None
    account_name: str | None = None
    status: Literal["queued", "uploading", "processing", "publishing", "published", "failed"]
    attempt_count: int
    platform_post_id: str | None
    platform_post_url: str | None
    error_code: str | None
    error_message_safe: str | None
    retryable: bool
    published_at: int | None
    created_at: int
    updated_at: int


class SocialPublishBatchView(BaseModel):
    id: str
    render_id: str
    created_at: int
    jobs: list[SocialPublishJobView]


class SocialCreatorInfo(BaseModel):
    account_name: str
    username: str | None
    avatar_url: str | None
    privacy_options: list[str]
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_duration: int


class SocialCopySave(SocialInput):
    content: dict[SocialPlatform, dict] = Field(alias="copy")

    @field_validator("content")
    @classmethod
    def typed_copy(cls, content):
        models = {"youtube": YouTubeCopy, "tiktok": TikTokCopy, "x": XCopy}
        if not content:
            raise ValueError("Provide edited copy")
        return {
            p: HashtagRules.validate(models[p].model_validate(c).model_dump())
            for p, c in content.items()
        }
