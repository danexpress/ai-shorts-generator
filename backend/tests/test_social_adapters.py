from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.social.adapters.base import ProviderError
from app.social.adapters.youtube import YouTubePublisher
from app.social.config import SocialSettings
from app.social.http import ProviderHTTP


def settings():
    return SocialSettings(
        clients={
            p: {"id": "client-id", "secret": "client-secret"} for p in ["youtube", "tiktok", "x"]
        }
    )


def test_youtube_oauth_requests_offline_and_pkce():
    adapter = YouTubePublisher(settings(), None)
    url = adapter.connect("state", "v" * 43)
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"]
    assert query["state"] == ["state"]
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == set(adapter.scopes)


def test_youtube_resumes_then_waits_for_provider_confirmation(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/videos") and request.method == "POST":
            assert b"private" in request.content and b"#career" in request.content
            return httpx.Response(
                200, headers={"Location": "https://www.googleapis.com/resumable/session"}
            )
        if request.url.path == "/resumable/session":
            if request.headers.get("content-range") == "bytes */5":
                return httpx.Response(308)
            assert request.headers["content-range"] == "bytes 0-4/5"
            return httpx.Response(200, json={"id": "video-1"})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "video-1",
                        "status": {"uploadStatus": "processed", "privacyStatus": "private"},
                    }
                ]
            },
        )

    http = ProviderHTTP(httpx.MockTransport(handler))
    try:
        adapter = YouTubePublisher(settings(), http)
        info = {"size": 5, "duration": 30, "width": 1080, "height": 1920}
        metadata = {
            "title": "Career advice",
            "description": "A lesson",
            "hashtags": ["#career"],
            "visibility": "private",
        }
        step = adapter.publish("token", path, info, metadata, {})
        assert step.status == "uploading"
        step = adapter.publish("token", path, info, metadata, step.checkpoint)
        assert step.status == "processing"
        step = adapter.get_publish_status("token", step.checkpoint)
        assert step.status == "published" and step.post_url.endswith("video-1")
        assert sum(r.method == "POST" and r.url.path.endswith("/videos") for r in calls) == 1
    finally:
        http.close()


def test_youtube_resumable_status_recovers_uncertain_completed_upload(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"id": "already-uploaded"})

    http = ProviderHTTP(httpx.MockTransport(handler))
    try:
        step = YouTubePublisher(settings(), http).publish(
            "token",
            tmp_path / "unused",
            {"size": 5},
            {},
            {"upload_url": "https://www.googleapis.com/resumable/session"},
        )
        assert step.checkpoint["video_id"] == "already-uploaded"
    finally:
        http.close()


def test_youtube_private_restrictions_are_explicit():
    adapter = YouTubePublisher(settings(), None)
    video = {"size": 100, "duration": 30, "width": 1080, "height": 1920}
    with pytest.raises(ProviderError, match="PRIVATE_POSTING_ONLY"):
        adapter.validate_video(video, {"visibility": "public"})
    YouTubePublisher(replace(settings(), youtube_verified=True), None).validate_video(
        video, {"visibility": "public"}
    )


def test_provider_transport_blocks_untrusted_upload_urls_and_redacts_errors():
    http = ProviderHTTP(
        httpx.MockTransport(
            lambda r: httpx.Response(500, json={"error": {"message": "private-token-and-caption"}})
        )
    )
    try:
        with pytest.raises(ProviderError, match="PROVIDER_RESPONSE_INVALID"):
            http.request("PUT", "https://localhost/private", hosts=("googleapis.com",))
        with pytest.raises(ProviderError) as result:
            http.request("POST", "https://www.googleapis.com/upload", hosts=("googleapis.com",))
        assert result.value.retryable and result.value.uncertain
        assert "private-token" not in str(result.value)
    finally:
        http.close()


def test_tiktok_creator_options_are_not_invented_and_private_mode_is_explicit():
    from app.social.adapters.tiktok import TikTokPublisher

    http = ProviderHTTP(
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "data": {
                        "creator_nickname": "Fred",
                        "creator_username": "fred",
                        "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                        "comment_disabled": True,
                        "duet_disabled": False,
                        "stitch_disabled": False,
                        "max_video_post_duration_sec": 60,
                    },
                    "error": {"code": "ok"},
                },
            )
        )
    )
    try:
        adapter = TikTokPublisher(settings(), http)
        creator = adapter.creator_info("token")
        assert creator["privacy_options"] == ["SELF_ONLY"]
        video = {"size": 10, "duration": 30, "width": 1080, "height": 1920, "fps": 30}
        metadata = {"privacy": "SELF_ONLY", "consent": True, "allow_comment": False}
        adapter.validate_video(video, metadata, creator)
        with pytest.raises(ProviderError):
            adapter.validate_video(video, {**metadata, "allow_comment": True}, creator)
        with pytest.raises(ProviderError):
            adapter.validate_video({**video, "duration": 90}, metadata, creator)
    finally:
        http.close()


def test_tiktok_waits_for_confirmation_and_handles_private_posts_without_public_url():
    from app.social.adapters.tiktok import TikTokPublisher

    statuses = iter(["PROCESSING_UPLOAD", "PUBLISH_COMPLETE"])
    http = ProviderHTTP(
        httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "data": {"status": next(statuses), "publicaly_available_post_id": []},
                    "error": {"code": "ok"},
                },
            )
        )
    )
    try:
        adapter = TikTokPublisher(settings(), http)
        assert adapter.get_publish_status("token", {"publish_id": "p1"}).status == "processing"
        step = adapter.get_publish_status("token", {"publish_id": "p1"})
        assert step.status == "published" and step.post_url is None and step.post_id is None
    finally:
        http.close()


def test_tiktok_chunk_plan_merges_final_remainder(tmp_path):
    from app.social.adapters.tiktok import TikTokPublisher

    total = 70 * 1024 * 1024 + 3
    path = tmp_path / "video.mp4"
    with path.open("wb") as file:
        file.truncate(total)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/creator_info/query/"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "creator_nickname": "Fred",
                        "privacy_level_options": ["SELF_ONLY"],
                        "comment_disabled": True,
                        "duet_disabled": True,
                        "stitch_disabled": True,
                        "max_video_post_duration_sec": 60,
                    },
                    "error": {"code": "ok"},
                },
            )
        if request.url.path.endswith("/video/init/"):
            import json

            source = json.loads(request.content)["source_info"]
            assert source["total_chunk_count"] == 2
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "p1",
                        "upload_url": "https://open-upload.tiktokapis.com/video/upload",
                    },
                    "error": {"code": "ok"},
                },
            )
        if request.url.path.endswith("/status/fetch/"):
            return httpx.Response(
                200,
                json={
                    "data": {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 32 * 1024 * 1024},
                    "error": {"code": "ok"},
                },
            )
        assert len(request.content) == total - 32 * 1024 * 1024
        assert request.headers["content-range"] == f"bytes {32 * 1024 * 1024}-{total - 1}/{total}"
        return httpx.Response(201)

    http = ProviderHTTP(httpx.MockTransport(handler))
    try:
        adapter = TikTokPublisher(settings(), http)
        info = {"size": total, "duration": 30, "width": 1080, "height": 1920, "fps": 30}
        metadata = {
            "privacy": "SELF_ONLY",
            "consent": True,
            "caption": "Lesson",
            "hashtags": [],
            "allow_comment": False,
            "allow_duet": False,
            "allow_stitch": False,
            "brand_content": False,
            "brand_organic": False,
            "is_aigc": False,
        }
        step = adapter.publish("token", path, info, metadata, {})
        assert step.status == "uploading"
        assert (
            adapter.publish("token", path, info, metadata, step.checkpoint).status == "processing"
        )
    finally:
        http.close()


def test_x_chunked_upload_polls_before_creating_post(tmp_path):
    from app.social.adapters.x import XPublisher

    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/initialize"):
            return httpx.Response(200, json={"data": {"id": "123"}})
        if request.url.path.endswith("/append"):
            import base64
            import json

            body = json.loads(request.content)
            assert base64.b64decode(body["media"]) == b"video" and body["segment_index"] == 0
            return httpx.Response(200, json={"data": {}})
        if request.url.path.endswith("/finalize"):
            return httpx.Response(
                200, json={"data": {"processing_info": {"state": "pending", "check_after_secs": 5}}}
            )
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"data": {"processing_info": {"state": "succeeded"}}})
        if request.url.path.endswith("/tweets"):
            return httpx.Response(201, json={"data": {"id": "456"}})
        raise AssertionError(request.url)

    http = ProviderHTTP(httpx.MockTransport(handler))
    try:
        adapter = XPublisher(settings(), http)
        info = {"size": 5}
        step = adapter.publish("token", path, info, {}, {})
        assert step.status == "uploading"
        step = adapter.publish("token", path, info, {}, step.checkpoint)
        assert step.status == "uploading"
        step = adapter.publish("token", path, info, {}, step.checkpoint)
        assert step.status == "processing"
        assert not any(r.url.path.endswith("/tweets") for r in requests)
        step = adapter.get_publish_status("token", step.checkpoint)
        assert step.status == "publishing"
        step = adapter.create_post("token", {"text": "A useful lesson"}, step.checkpoint)
        assert step.post_url == "https://x.com/i/status/456"
    finally:
        http.close()


def test_x_oauth_includes_media_scope_offline_and_pkce():
    from app.social.adapters.x import XPublisher

    query = parse_qs(urlparse(XPublisher(settings(), None).connect("state", "v" * 43)).query)
    assert "media.write" in query["scope"][0] and "offline.access" in query["scope"][0]
    assert query["code_challenge_method"] == ["S256"]
