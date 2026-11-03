"""Instagram API with Instagram Login: Reels for Business and Creator accounts.

Flow (graph.instagram.com, v26.0): create a REELS container from a public video URL,
poll its status_code until FINISHED, then media_publish, the only call that
creates a post. Instagram downloads the video itself, so the worker supplies a
short-lived signed URL (`media_url`) and never logs it.
"""

import re
from urllib.parse import urlencode

from .base import Identity, ProviderError, PublishStep, SocialPublisher, Tokens

GRAPH = "https://graph.instagram.com/v26.0"
MAX_BYTES = 300 * 1024**2
CAPTION_LIMIT, HASHTAG_LIMIT, MENTION_LIMIT = 2200, 30, 20


def caption_text(metadata) -> str:
    tags = " ".join(metadata.get("hashtags") or [])
    return metadata["caption"] + (("\n\n" + tags) if tags else "")


class InstagramPublisher(SocialPublisher):
    platform = "instagram"
    scopes = ("instagram_business_basic", "instagram_business_content_publish")
    authorize_url = "https://www.instagram.com/oauth/authorize"
    token_url = "https://api.instagram.com/oauth/access_token"
    hosts = ("instagram.com",)
    needs_media_url = True
    # Long-lived tokens last 60 days and refresh themselves; renew in the final week.
    refresh_ahead_seconds = 7 * 86_400

    def connect(self, state, verifier):
        # Instagram Login does not use PKCE; the single-use, browser-bound state does.
        return (
            self.authorize_url
            + "?"
            + urlencode(
                {
                    "client_id": self.client["id"],
                    "redirect_uri": self.settings.callback(self.platform),
                    "response_type": "code",
                    "scope": ",".join(self.scopes),
                    "state": state,
                }
            )
        )

    def exchange(self, code, verifier):
        short = self.http.json(
            "POST",
            self.token_url,
            hosts=self.hosts,
            data={
                "client_id": self.client["id"],
                "client_secret": self.client["secret"],
                "grant_type": "authorization_code",
                "redirect_uri": self.settings.callback(self.platform),
                "code": code.removesuffix("#_"),
            },
        )
        granted = short.get("permissions") or []
        if isinstance(granted, str):
            granted = [p.strip() for p in granted.split(",") if p.strip()]
        long = self.http.json(
            "GET",
            "https://graph.instagram.com/access_token",
            hosts=self.hosts,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.client["secret"],
                "access_token": short["access_token"],
            },
        )
        # The long-lived token is also what refreshes it.
        return Tokens(long["access_token"], long["access_token"], int(long["expires_in"]), granted)

    def refresh_token(self, refresh):
        data = self.http.json(
            "GET",
            "https://graph.instagram.com/refresh_access_token",
            hosts=self.hosts,
            params={"grant_type": "ig_refresh_token", "access_token": refresh},
        )
        return Tokens(
            data["access_token"], data["access_token"], int(data["expires_in"]), list(self.scopes)
        )

    def call(self, token, method, path, **kwargs):
        params = {**kwargs.pop("params", {}), "access_token": token}
        return self.http.json(method, f"{GRAPH}/{path}", hosts=self.hosts, params=params, **kwargs)

    def identity(self, token):
        data = self.call(
            token,
            "GET",
            "me",
            params={"fields": "user_id,username,name,account_type,profile_picture_url"},
        )
        if data.get("account_type") not in {"Business", "Media_Creator"}:
            # Publishing requires a professional account.
            raise ProviderError("PERMISSION_REQUIRED")
        # user_id is the professional account ID the publishing endpoints expect.
        return Identity(
            str(data["user_id"]),
            data.get("name") or data["username"],
            data.get("username"),
            data.get("profile_picture_url"),
        )

    def validate(self, video, metadata, creator=None):
        if (
            video["size"] > MAX_BYTES
            or not 3 <= video["duration"] <= 900
            or video["width"] > 1920
            or not 23 <= video.get("fps", 30) <= 60
            or video.get("video_codec") not in {"h264", "hevc"}
            or video.get("audio_codec") not in {None, "aac"}
        ):
            raise ProviderError("MEDIA_REJECTED")
        if metadata:
            text = caption_text(metadata)
            if (
                len(text) > CAPTION_LIMIT
                or len(metadata.get("hashtags") or []) > HASHTAG_LIMIT
                or len(re.findall(r"(?<!\w)@\w+", text)) > MENTION_LIMIT
            ):
                raise ProviderError("INVALID_METADATA")

    def publish(self, token, video, info, metadata, checkpoint, media_url=None):
        cp = dict(checkpoint)
        if not media_url:
            raise ProviderError("PUBLIC_MEDIA_URL_REQUIRED")
        # Containers are not posts: creating one again after an ambiguous failure is safe.
        data = self.call(
            token,
            "POST",
            f"{cp['platform_user_id']}/media",
            data={
                "media_type": "REELS",
                "video_url": media_url,
                "caption": caption_text(metadata),
                "share_to_feed": "true" if metadata.get("share_to_feed", True) else "false",
            },
        )
        cp["container_id"] = data["id"]
        return PublishStep("processing", cp, delay=30)

    def upload_complete(self, checkpoint):
        return bool(checkpoint.get("container_id"))

    def get_status(self, token, checkpoint):
        data = self.call(token, "GET", checkpoint["container_id"], params={"fields": "status_code"})
        state = data.get("status_code")
        if state == "FINISHED":
            return PublishStep("publishing", checkpoint, delay=0)  # worker calls create_post
        if state == "PUBLISHED":
            return PublishStep("published", checkpoint)  # finished earlier; no media id to link
        if state in {"ERROR", "EXPIRED"}:
            raise ProviderError("MEDIA_REJECTED")
        return PublishStep("processing", checkpoint, delay=30)

    def create_post(self, token, metadata, checkpoint):
        ig_user = checkpoint["platform_user_id"]
        usage = self.call(
            token,
            "GET",
            f"{ig_user}/content_publishing_limit",
            params={"fields": "quota_usage,config"},
        ).get("data", [{}])[0]
        total = (usage.get("config") or {}).get("quota_total")
        if total and usage.get("quota_usage", 0) >= total:
            raise ProviderError("QUOTA_EXCEEDED")  # rolling 24-hour post limit; waits
        media = self.call(
            token,
            "POST",
            f"{ig_user}/media_publish",
            data={"creation_id": checkpoint["container_id"]},
        )
        permalink = self.call(token, "GET", media["id"], params={"fields": "permalink"}).get(
            "permalink"
        )
        return PublishStep("published", checkpoint, post_id=media["id"], post_url=permalink)

    def disconnect(self, token):
        # Instagram Login has no token revocation endpoint; creators remove the app in
        # Instagram's settings. Stored tokens are deleted locally regardless.
        raise ProviderError("UNSUPPORTED")
