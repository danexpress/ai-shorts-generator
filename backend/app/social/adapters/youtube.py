"""YouTube Data API: resumable uploads survive worker restart without re-uploading."""

import re

from .base import Identity, ProviderError, PublishStep, SocialPublisher, Tokens

TITLE_LIMIT, DESCRIPTION_BYTES = 100, 5000


def with_shorts_tag(title: str, description: str, hashtags: list[str]) -> tuple[str, str]:
    """Title and description with #Shorts added where missing and it fits.

    The tag always goes in the description's hashtag line (unless it would exceed
    YouTube's 5,000-byte limit) and in the title when it fits in 100 characters.
    """
    tags = list(hashtags)
    present = lambda text: "#shorts" in text.lower()
    if not any(present(t) for t in [title, description, *tags]):
        tags.append("#Shorts")
    full = description + ("\n\n" + " ".join(tags) if tags else "")
    if len(full.encode("utf-8")) > DESCRIPTION_BYTES:
        full = description + ("\n\n" + " ".join(hashtags) if hashtags else "")
    if not present(title) and len(title) + len(" #Shorts") <= TITLE_LIMIT:
        title = title + " #Shorts"
    return title, full


class YouTubePublisher(SocialPublisher):
    platform = "youtube"
    supports_thumbnail = True
    quota_timezone = "America/Los_Angeles"  # YouTube quotas reset at midnight Pacific

    def quota_limits(self):
        return {
            "youtube:uploads": self.settings.youtube_daily_uploads,
            "youtube:units": self.settings.youtube_daily_units,
        }

    def quota_costs(self, action, checkpoint):
        if action == "publish":
            # Only starting the resumable session is a videos.insert call; chunks are free.
            return {} if checkpoint.get("upload_url") else {"youtube:uploads": 1}
        if action == "status":
            return {"youtube:units": 1}  # videos.list
        if action == "thumbnail":
            return {"youtube:units": 50}  # thumbnails.set
        return {}

    scopes = (
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    )
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    hosts = ("googleapis.com",)

    def authorization_options(self):
        return {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}

    def token_request(self, **fields):
        data = self.http.json(
            "POST",
            self.token_url,
            hosts=self.hosts,
            data={"client_id": self.client["id"], "client_secret": self.client["secret"], **fields},
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

    def identity(self, token):
        data = self.http.json(
            "GET",
            "https://www.googleapis.com/youtube/v3/channels",
            hosts=self.hosts,
            headers={"Authorization": "Bearer " + token},
            params={"part": "snippet", "mine": "true"},
        )
        if not data.get("items"):
            raise ProviderError("PERMISSION_REQUIRED")
        channel = data["items"][0]
        snippet = channel["snippet"]
        return Identity(
            channel["id"],
            snippet["title"],
            snippet.get("customUrl"),
            snippet.get("thumbnails", {}).get("default", {}).get("url"),
        )

    def validate(self, video, metadata, creator=None):
        if (
            video["size"] > 256 * 1024**3
            or video["duration"] > 180
            or video["width"] / video["height"] > 1.01
        ):
            raise ProviderError("MEDIA_REJECTED")
        if (
            metadata.get("visibility", "private") != "private"
            and not self.settings.youtube_verified
        ):
            raise ProviderError("PRIVATE_POSTING_ONLY")

    def publish(self, token, video, info, metadata, checkpoint, media_url=None):
        cp = dict(checkpoint)
        headers = {"Authorization": "Bearer " + token}
        if not cp.get("upload_url"):
            title, description = with_shorts_tag(
                metadata["title"], metadata["description"], metadata.get("hashtags") or []
            )
            response = self.http.request(
                "POST",
                "https://www.googleapis.com/upload/youtube/v3/videos",
                hosts=self.hosts,
                headers={
                    **headers,
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(info["size"]),
                },
                params={"uploadType": "resumable", "part": "snippet,status"},
                json={
                    "snippet": {
                        "title": title,
                        "description": description,
                        "categoryId": "22",
                    },
                    "status": {
                        "privacyStatus": metadata["visibility"],
                        "selfDeclaredMadeForKids": metadata.get("made_for_kids", False),
                        "containsSyntheticMedia": metadata.get("contains_synthetic_media", False),
                    },
                },
            )
            url = response.headers.get("Location")
            if not url:
                raise ProviderError("PROVIDER_RESPONSE_INVALID")
            cp["upload_url"] = url
            return PublishStep("uploading", cp, delay=0)
        # Status query reconciles an ambiguous last PUT rather than inserting again.
        response = self.http.request(
            "PUT",
            cp["upload_url"],
            hosts=self.hosts,
            headers={**headers, "Content-Length": "0", "Content-Range": f"bytes */{info['size']}"},
            content=b"",
            expected=(200, 201, 308),
        )
        if response.status_code in (200, 201):
            cp["video_id"] = response.json()["id"]
            return PublishStep("processing", cp)
        matched = re.search(r"bytes=0-(\d+)", response.headers.get("Range", ""))
        offset = int(matched[1]) + 1 if matched else 0
        with video.open("rb") as file:
            file.seek(offset)
            chunk = file.read(8 * 1024 * 1024)
        if not chunk:
            return PublishStep("processing", cp)
        response = self.http.request(
            "PUT",
            cp["upload_url"],
            hosts=self.hosts,
            headers={
                **headers,
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{info['size']}",
            },
            content=chunk,
            expected=(200, 201, 308),
        )
        if response.status_code in (200, 201):
            cp["video_id"] = response.json()["id"]
            return PublishStep("processing", cp)
        return PublishStep("uploading", cp, delay=0)

    def upload_complete(self, checkpoint):
        return bool(checkpoint.get("video_id"))

    def get_status(self, token, checkpoint):
        if not checkpoint.get("video_id"):
            return PublishStep("uploading", checkpoint, delay=0)
        data = self.http.json(
            "GET",
            "https://www.googleapis.com/youtube/v3/videos",
            hosts=self.hosts,
            headers={"Authorization": "Bearer " + token},
            params={"part": "status,processingDetails", "id": checkpoint["video_id"]},
        )
        if not data.get("items"):
            return PublishStep("processing", checkpoint, delay=10)
        item = data["items"][0]
        state = item.get("processingDetails", {}).get("processingStatus")
        uploaded = item.get("status", {}).get("uploadStatus")
        if state in {"failed", "terminated"} or uploaded in {"failed", "rejected", "deleted"}:
            raise ProviderError("MEDIA_REJECTED")
        if state == "succeeded" or uploaded == "processed":
            visibility = item.get("status", {}).get("privacyStatus")
            return PublishStep(
                "published",
                checkpoint,
                post_id=item["id"],
                post_url="https://www.youtube.com/watch?v=" + item["id"],
                notice="Uploaded as a private YouTube video." if visibility == "private" else None,
            )
        return PublishStep("processing", checkpoint, delay=10)

    def set_thumbnail(self, token, video_id, thumbnail):
        # Called only with a server-owned image, never a browser-supplied URL.
        self.http.request(
            "POST",
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            hosts=self.hosts,
            headers={"Authorization": "Bearer " + token, "Content-Type": "image/jpeg"},
            params={"videoId": video_id, "uploadType": "media"},
            content=thumbnail,
        )

    def disconnect(self, token):
        self.http.request(
            "POST", "https://oauth2.googleapis.com/revoke", hosts=self.hosts, data={"token": token}
        )
