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
