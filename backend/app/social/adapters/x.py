"""X v2 chunked uploads and user-context posts; no post until processing succeeds."""

import base64

from .base import Identity, ProviderError, PublishStep, SocialPublisher, Tokens


class XPublisher(SocialPublisher):
    platform = "x"
    scopes = ("users.read", "tweet.read", "tweet.write", "media.write", "offline.access")
    authorize_url = "https://x.com/i/oauth2/authorize"
    token_url = "https://api.x.com/2/oauth2/token"
    hosts = ("api.x.com",)

    def token_request(self, **fields):
        data = self.http.json(
            "POST",
            self.token_url,
            hosts=self.hosts,
            auth=(self.client["id"], self.client["secret"]),
            data=fields,
        )
        return Tokens(
            data["access_token"],
            data.get("refresh_token"),
            int(data["expires_in"]),
            data.get("scope", " ".join(self.scopes)).split(),
        )

    def exchange(self, code, verifier):
        return self.token_request(
            grant_type="authorization_code",
            code=code,
            code_verifier=verifier,
            redirect_uri=self.settings.callback(self.platform),
        )

    def refresh_token(self, refresh):
        return self.token_request(grant_type="refresh_token", refresh_token=refresh)

    def call(self, token, method, endpoint, **kwargs):
        data = self.http.json(
            method,
            "https://api.x.com/2/" + endpoint,
            hosts=self.hosts,
            headers={"Authorization": "Bearer " + token},
            **kwargs,
        )
        if data.get("errors"):
            raise ProviderError("PROVIDER_REJECTED")
        return data["data"]

    def identity(self, token):
        data = self.call(token, "GET", "users/me", params={"user.fields": "profile_image_url"})
        return Identity(data["id"], data["name"], data["username"], data.get("profile_image_url"))

    def validate_video(self, video, metadata, creator=None):
        # Current v2 limits are account-dependent; conservative standard post caps.
        if (
            video["size"] > 8 * 1024**3
            or not 0.5 <= video["duration"] <= 1200
            or video["fps"] > 60
            or not 1 / 3 <= video["width"] / video["height"] <= 3
            or min(video["width"], video["height"]) < 32
            or max(video["width"], video["height"]) > 1920
        ):
            raise ProviderError("MEDIA_REJECTED")

    def publish(self, token, video, info, metadata, checkpoint):
        cp = dict(checkpoint)
        if not cp.get("media_id"):
            data = self.call(
                token,
                "POST",
                "media/upload/initialize",
                json={
                    "media_type": "video/mp4",
                    "media_category": "tweet_video",
                    "total_bytes": info["size"],
                },
            )
            cp.update(media_id=data["id"], offset=0, segment=0)
            return PublishStep("uploading", cp, delay=0)
        if cp["offset"] < info["size"]:
            with video.open("rb") as file:
                file.seek(cp["offset"])
                chunk = file.read(4 * 1024 * 1024)
            # Same segment_index is replayed after a lost acknowledgement.
            self.call(
                token,
                "POST",
                f"media/upload/{cp['media_id']}/append",
                json={"segment_index": cp["segment"], "media": base64.b64encode(chunk).decode()},
            )
            cp.update(offset=cp["offset"] + len(chunk), segment=cp["segment"] + 1)
            return PublishStep("uploading", cp, delay=0)
        if not cp.get("finalized"):
            data = self.call(token, "POST", f"media/upload/{cp['media_id']}/finalize")
            cp["finalized"] = True
            return self.processing(data, cp)
        return self.get_publish_status(token, cp)

    def processing(self, data, cp):
        processing = data.get("processing_info", {})
        if processing.get("state") == "failed":
            raise ProviderError("MEDIA_REJECTED")
        if not processing or processing.get("state") == "succeeded":
            return PublishStep("publishing", cp, delay=0)
        return PublishStep(
            "processing", cp, delay=max(1, int(processing.get("check_after_secs", 5)))
        )

    def get_publish_status(self, token, checkpoint):
        data = self.call(token, "GET", "media/upload", params={"media_id": checkpoint["media_id"]})
        return self.processing(data, dict(checkpoint))

    def create_post(self, token, metadata, checkpoint):
        # Worker persists post_request_started BEFORE this non-idempotent request.
        # Ambiguous errors must never automatically repeat POST /tweets.
        data = self.call(
            token,
            "POST",
            "tweets",
            json={"text": metadata["text"], "media": {"media_ids": [checkpoint["media_id"]]}},
        )
        return PublishStep(
            "published",
            checkpoint,
            post_id=data["id"],
            post_url="https://x.com/i/status/" + data["id"],
        )

    def disconnect(self, token):
        self.http.request(
            "POST",
            "https://api.x.com/2/oauth2/revoke",
            hosts=self.hosts,
            auth=(self.client["id"], self.client["secret"]),
            data={"token": token, "token_type_hint": "access_token"},
        )
