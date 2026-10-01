"""TikTok Direct Post FILE_UPLOAD with creator-driven privacy and processing status."""

from urllib.parse import urlencode

from .base import Identity, ProviderError, PublishStep, SocialPublisher, Tokens


class TikTokPublisher(SocialPublisher):
    platform = "tiktok"
    scopes = ("user.info.basic", "video.publish")
    token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    hosts = ("tiktokapis.com",)

    def connect(self, state, verifier):
        # TikTok confidential web Login Kit uses state; PKCE is for its public clients.
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
            {
                "client_key": self.client["id"],
                "scope": ",".join(self.scopes),
                "response_type": "code",
                "redirect_uri": self.settings.callback(self.platform),
                "state": state,
            }
        )

    def token_request(self, **fields):
        data = self.http.json(
            "POST",
            self.token_url,
            hosts=self.hosts,
            data={
                "client_key": self.client["id"],
                "client_secret": self.client["secret"],
                **fields,
            },
        )
        if data.get("error"):
            raise ProviderError("RECONNECT_REQUIRED")
        return Tokens(
            data["access_token"],
            data.get("refresh_token"),
            int(data["expires_in"]),
            data.get("scope", "").split(","),
        )

    def exchange(self, code, verifier):
        return self.token_request(
            grant_type="authorization_code",
            code=code,
            redirect_uri=self.settings.callback(self.platform),
        )

    def refresh_token(self, refresh):
        return self.token_request(grant_type="refresh_token", refresh_token=refresh)

    def call(self, token, endpoint, body=None, **kwargs):
        data = self.http.json(
            "POST" if body is not None else "GET",
            "https://open.tiktokapis.com/v2/" + endpoint,
            hosts=self.hosts,
            headers={"Authorization": "Bearer " + token},
            **({"json": body} if body is not None else {}),
            **kwargs,
        )
        code = data.get("error", {}).get("code", "ok")
        if code != "ok":
            mapping = {
                "access_token_invalid": "RECONNECT_REQUIRED",
                "scope_not_authorized": "PERMISSION_REQUIRED",
                "rate_limit_exceeded": "PROVIDER_RATE_LIMITED",
                "unaudited_client_can_only_post_to_private_accounts": "PRIVATE_POSTING_ONLY",
            }
            raise ProviderError(
                mapping.get(code, "PROVIDER_REJECTED"),
                retryable=code in {"internal_error", "rate_limit_exceeded"},
            )
        return data.get("data", {})

    def identity(self, token):
        user = self.call(token, "user/info/", params={"fields": "open_id,display_name,avatar_url"})[
            "user"
        ]
        return Identity(user["open_id"], user["display_name"], None, user.get("avatar_url"))

    def creator_info(self, token):
        data = self.call(token, "post/publish/creator_info/query/", {})
        options = data.get("privacy_level_options", [])
        if not self.settings.tiktok_audited:
            options = [option for option in options if option == "SELF_ONLY"]
        return {
            "account_name": data["creator_nickname"],
            "username": data.get("creator_username"),
            "avatar_url": data.get("creator_avatar_url"),
            "privacy_options": options,
            "comment_disabled": bool(data.get("comment_disabled", True)),
            "duet_disabled": bool(data.get("duet_disabled", True)),
            "stitch_disabled": bool(data.get("stitch_disabled", True)),
            "max_duration": data["max_video_post_duration_sec"],
        }

    def validate_video(self, video, metadata, creator=None):
        if (
            not creator
            or video["size"] > 4 * 1024**3
            or video["duration"] > min(600, creator["max_duration"])
            or not 23 <= video["fps"] <= 60
            or not all(360 <= video[d] <= 4096 for d in ["width", "height"])
        ):
            raise ProviderError("MEDIA_REJECTED")
        if metadata.get("privacy") not in creator["privacy_options"]:
            raise ProviderError(
                "PRIVATE_POSTING_ONLY" if not self.settings.tiktok_audited else "PROVIDER_REJECTED"
            )
        for field in ["comment", "duet", "stitch"]:
            if metadata.get("allow_" + field) and creator[field + "_disabled"]:
                raise ProviderError("PROVIDER_REJECTED")
        if not metadata.get("consent") or (
            metadata.get("brand_content") and metadata.get("privacy") == "SELF_ONLY"
        ):
            raise ProviderError("PROVIDER_REJECTED")

    def publish(self, token, video, info, metadata, checkpoint):
        cp = dict(checkpoint)
        if not cp.get("publish_id"):
            # Re-query creator restrictions immediately before initializing the post.
            creator = self.creator_info(token)
            self.validate_video(info, metadata, creator)
            cp["username"] = creator.get("username")
            chunk_size = min(info["size"], 32 * 1024 * 1024)
            chunks = max(1, info["size"] // chunk_size)
            caption = metadata["caption"] + (
                "\n" + " ".join(metadata["hashtags"]) if metadata.get("hashtags") else ""
            )
            data = self.call(
                token,
                "post/publish/video/init/",
                {
                    "post_info": {
                        "title": caption,
                        "privacy_level": metadata["privacy"],
                        "disable_comment": not metadata["allow_comment"],
                        "disable_duet": not metadata["allow_duet"],
                        "disable_stitch": not metadata["allow_stitch"],
                        "brand_content_toggle": metadata["brand_content"],
                        "brand_organic_toggle": metadata["brand_organic"],
                        "is_aigc": metadata["is_aigc"],
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": info["size"],
                        "chunk_size": chunk_size,
                        "total_chunk_count": chunks,
                    },
                },
            )
            cp.update(
                publish_id=data["publish_id"],
                upload_url=data["upload_url"],
                chunk_size=chunk_size,
                chunk_count=chunks,
            )
            return PublishStep("uploading", cp)
        data = self.call(token, "post/publish/status/fetch/", {"publish_id": cp["publish_id"]})
        if data["status"] != "PROCESSING_UPLOAD":
            return self.status_step(data, cp)
        offset = int(data.get("uploaded_bytes", 0))
        if offset >= info["size"]:
            return PublishStep("processing", cp, delay=10)
        remaining = info["size"] - offset
        length = remaining if remaining < 2 * cp["chunk_size"] else cp["chunk_size"]
        with video.open("rb") as file:
            file.seek(offset)
            chunk = file.read(length)
        self.http.request(
            "PUT",
            cp["upload_url"],
            hosts=self.hosts,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{info['size']}",
            },
            content=chunk,
            expected=(201, 206),
        )
        if offset + len(chunk) == info["size"]:
            cp["transfer_complete"] = True
        return PublishStep(
            "processing" if offset + len(chunk) == info["size"] else "uploading",
            cp,
            delay=10 if offset + len(chunk) == info["size"] else 3,
        )

    def status_step(self, data, cp):
        if data["status"] == "FAILED":
            raise ProviderError("MEDIA_REJECTED")
        if data["status"] == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id", [])
            post_id = str(ids[0]) if ids else None
            return PublishStep(
                "published",
                cp,
                post_id=post_id,
                post_url="https://www.tiktok.com/@" + cp["username"] + "/video/" + post_id
                if post_id and cp.get("username")
                else None,
                notice="TikTok confirmed the post. Private posts do not have a public link."
                if not post_id
                else None,
            )
        return PublishStep("processing", cp, delay=10)

    def get_publish_status(self, token, checkpoint):
        return self.status_step(
            self.call(
                token, "post/publish/status/fetch/", {"publish_id": checkpoint["publish_id"]}
            ),
            checkpoint,
        )

    def disconnect(self, token):
        self.http.request(
            "POST",
            "https://open.tiktokapis.com/v2/oauth/revoke/",
            hosts=self.hosts,
            data={
                "client_key": self.client["id"],
                "client_secret": self.client["secret"],
                "token": token,
            },
        )
