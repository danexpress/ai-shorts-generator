from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from conftest import rows
from social_helpers import callback, connect, drain, start, targets

from app.routers.media import media_signature
from app.social.adapters.base import ProviderError
from app.social.adapters.instagram import InstagramPublisher
from app.social.config import SocialSettings
from app.social.db_models import PublishJob, SocialAccount
from app.social.http import ProviderHTTP

VIDEO = {
    "size": 5_000_000,
    "duration": 30,
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "video_codec": "h264",
    "audio_codec": "aac",
}
POST = {
    "caption": "Hire for the company you will be.",
    "hashtags": ["#hiring"],
    "share_to_feed": True,
}


def settings(**overrides):
    defaults = {
        "public_url": "https://api.example",
        "clients": {"instagram": {"id": "ig-app", "secret": "ig-secret"}},
    }
    return SocialSettings(**{**defaults, **overrides})


class FakeMeta:
    def __init__(self):
        self.requests, self.status, self.quota_usage = [], "IN_PROGRESS", 3

    def handler(self, request):
        self.requests.append(request)
        path, query = request.url.path, parse_qs(request.url.query.decode())
        form = parse_qs(request.content.decode()) if request.content else {}
        if request.url.host == "api.instagram.com":
            assert form["client_secret"] == ["ig-secret"] and form["code"] == ["the-code"]
            return httpx.Response(
                200,
                json={
                    "access_token": "short",
                    "user_id": 17,
                    "permissions": [
                        "instagram_business_basic",
                        "instagram_business_content_publish",
                    ],
                },
            )
        if path == "/access_token":
            assert query["grant_type"] == ["ig_exchange_token"] and query["access_token"] == [
                "short"
            ]
            return httpx.Response(
                200, json={"access_token": "long", "token_type": "bearer", "expires_in": 5183944}
            )
        if path == "/refresh_access_token":
            assert query["grant_type"] == ["ig_refresh_token"]
            return httpx.Response(
                200, json={"access_token": "long-2", "token_type": "bearer", "expires_in": 5183944}
            )
        if path == "/v26.0/me":
            return httpx.Response(
                200,
                json={
                    "user_id": "1784",
                    "id": "app-scoped",
                    "username": "fred",
                    "name": "Fred",
                    "account_type": self.account_type,
                    "profile_picture_url": "https://cdn.example/a.jpg",
                },
            )
        if path == "/v26.0/1784/media":
            return httpx.Response(200, json={"id": "container-1"})
        if path == "/v26.0/container-1":
            return httpx.Response(200, json={"status_code": self.status, "id": "container-1"})
        if path == "/v26.0/1784/content_publishing_limit":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "quota_usage": self.quota_usage,
                            "config": {"quota_total": 100, "quota_duration": 86400},
                        }
                    ]
                },
            )
        if path == "/v26.0/1784/media_publish":
            return httpx.Response(200, json={"id": "media-9"})
        if path == "/v26.0/media-9":
            return httpx.Response(
                200, json={"permalink": "https://www.instagram.com/reel/abc/", "id": "media-9"}
            )
        return httpx.Response(404, json={"error": {"code": 100}})

    account_type = "Media_Creator"


@pytest.fixture
def meta():
    fake = FakeMeta()
    http = ProviderHTTP(httpx.MockTransport(fake.handler))
    yield fake, InstagramPublisher(settings(), http)
    http.close()


def test_login_uses_instagram_login_scopes_without_pkce(meta):
    _fake, adapter = meta
    url = urlparse(adapter.connect("state-1", "v" * 43))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert (url.netloc, url.path) == ("www.instagram.com", "/oauth/authorize")
    assert query["scope"] == "instagram_business_basic,instagram_business_content_publish"
    assert query["response_type"] == "code" and query["state"] == "state-1"
    assert query["redirect_uri"] == "https://api.example/v1/social-accounts/instagram/callback"
    assert "code_challenge" not in query


def test_code_exchange_returns_a_self_refreshing_long_lived_token(meta):
    _fake, adapter = meta
    tokens = adapter.exchange("the-code#_", "unused")  # Instagram appends #_ to the code
    assert (tokens.access, tokens.refresh, tokens.expires_in) == ("long", "long", 5183944)
    assert set(adapter.scopes) <= set(tokens.scopes)
    refreshed = adapter.refresh_token("long")
    assert (refreshed.access, refreshed.refresh) == ("long-2", "long-2")


def test_identity_uses_the_professional_account_id_and_rejects_personal_accounts(meta):
    fake, adapter = meta
    identity = adapter.identity("long")
    assert (identity.id, identity.username) == ("1784", "fred")  # user_id, not the app-scoped id
    fake.account_type = "Personal"
    with pytest.raises(ProviderError, match="PERMISSION_REQUIRED"):
        adapter.identity("long")


@pytest.mark.parametrize(
    "video,metadata,code",
    [
        ({**VIDEO, "duration": 2}, POST, "MEDIA_REJECTED"),
        ({**VIDEO, "duration": 901}, POST, "MEDIA_REJECTED"),
        ({**VIDEO, "size": 301 * 1024**2}, POST, "MEDIA_REJECTED"),
        ({**VIDEO, "fps": 20}, POST, "MEDIA_REJECTED"),
        ({**VIDEO, "video_codec": "vp9"}, POST, "MEDIA_REJECTED"),
        (VIDEO, {**POST, "caption": "x" * 2200}, "INVALID_METADATA"),
        (VIDEO, {**POST, "hashtags": [f"#t{i}" for i in range(31)]}, "INVALID_METADATA"),
        (VIDEO, {**POST, "caption": " ".join(f"@u{i}" for i in range(21))}, "INVALID_METADATA"),
    ],
)
def test_reels_specs_are_validated_up_front(meta, video, metadata, code):
    _fake, adapter = meta
    with pytest.raises(ProviderError, match=code):
        adapter.validate(video, metadata)


def test_reels_publish_container_poll_then_publish(meta):
    fake, adapter = meta
    adapter.validate(VIDEO, POST)
    cp = {"platform_user_id": "1784"}
    with pytest.raises(ProviderError, match="PUBLIC_MEDIA_URL_REQUIRED"):
        adapter.publish("long", None, VIDEO, POST, cp)
    step = adapter.publish(
        "long", None, VIDEO, POST, cp, media_url="https://api.example/v1/rendered/r?x=1"
    )
    created = parse_qs(fake.requests[-1].content.decode())
    assert created["media_type"] == ["REELS"] and created["share_to_feed"] == ["true"]
    assert created["video_url"] == ["https://api.example/v1/rendered/r?x=1"]
    assert created["caption"] == ["Hire for the company you will be.\n\n#hiring"]
    assert (step.status, step.checkpoint["container_id"]) == ("processing", "container-1")
    assert adapter.upload_complete(step.checkpoint)
    assert adapter.get_status("long", step.checkpoint).status == "processing"
    fake.status = "FINISHED"
    ready = adapter.get_status("long", step.checkpoint)
    assert ready.status == "publishing"  # the worker then calls create_post, once
    done = adapter.create_post("long", POST, ready.checkpoint)
    assert (done.status, done.post_id, done.post_url) == (
        "published",
        "media-9",
        "https://www.instagram.com/reel/abc/",
    )
    assert sum(r.url.path.endswith("/media_publish") for r in fake.requests) == 1


def test_rolling_post_limit_and_failed_containers(meta):
    fake, adapter = meta
    cp = {"platform_user_id": "1784", "container_id": "container-1"}
    fake.quota_usage = 100
    with pytest.raises(ProviderError, match="QUOTA_EXCEEDED"):
        adapter.create_post("long", POST, cp)
    assert not any(r.url.path.endswith("/media_publish") for r in fake.requests)
    for state in ("ERROR", "EXPIRED"):
        fake.status = state
        with pytest.raises(ProviderError, match="MEDIA_REJECTED"):
            adapter.get_status("long", cp)


def test_instagram_requires_https_and_shows_app_review_status():
    local = settings(
        public_url="http://127.0.0.1:8000",
        frontend_url="http://127.0.0.1:3000",
        allow_local_http=True,
    )
    assert local.valid_url("http://127.0.0.1:8000", "youtube")
    assert not local.valid_url("http://127.0.0.1:8000", "instagram")
    configured = settings(
        enabled={"instagram": True}, encryption_keys="k", frontend_url="https://app.example"
    )
    assert "app review pending" in configured.notice("instagram")
    assert replace(configured, instagram_approved=True).notice("instagram") is None


def test_worker_passes_a_short_lived_signed_video_link(
    client, headers, render, publishing, clock, app
):
    worker, adapters, calls = publishing
    assert (
        start(client, headers, render, targets(client, headers, ["instagram"])).status_code == 202
    )
    drain(worker, clock)
    link = adapters["instagram"].media_urls[0]
    parsed = urlparse(link)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert link.startswith(f"https://api.example/v1/rendered/{render}?")
    assert query["signature"] == media_signature(
        render, int(query["expires"]), app.state.settings.media_signing_key
    )
    assert int(query["expires"]) - clock() <= 3600
    assert calls["instagram"] == 1
    assert [j["status"] for j in rows(app.state.database, PublishJob)] == ["published"]


def test_media_links_need_a_public_https_backend(publishing, social):
    worker, _adapters, _calls = publishing
    service, _adapter = social
    # (With a plain-HTTP backend Instagram is disabled outright; this guards the worker too.)
    service.settings = replace(service.settings, public_url="http://127.0.0.1:8000")
    with pytest.raises(ProviderError, match="PUBLIC_MEDIA_URL_REQUIRED"):
        worker.media_url("rnd_1")
    assert not service.settings.available("instagram")


def test_long_lived_tokens_refresh_in_their_final_week(client, headers, social, database, clock):
    service, adapter = social
    state, binding = connect(client, headers, "instagram")
    callback(client, state, binding, "instagram")
    account = rows(database, SocialAccount)[0]
    with database.transaction() as db:
        db.get(SocialAccount, account["id"]).token_expires_at = int((clock() + 3 * 86_400) * 1000)
    adapter.refresh_ahead_seconds = 120  # YouTube-style: 3 days left is not "expiring"
    assert service.token(account["id"], account["user_id"], "instagram") == "private-access"
    adapter.refresh_ahead_seconds = 7 * 86_400  # Instagram: renew during the final week
    assert service.token(account["id"], account["user_id"], "instagram") == "rotated-access"
