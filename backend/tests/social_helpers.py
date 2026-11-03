from collections import Counter
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import ready
from cryptography.fernet import Fernet

from app.social.accounts import AccountService
from app.social.adapters.base import Identity, ProviderError, PublishStep, Tokens
from app.social.config import PLATFORMS, SocialSettings
from app.social.worker import PublishWorker


class FakeAccountAdapter:
    scopes = ("publish", "profile")

    def connect(self, state, verifier):
        return "https://provider.example/authorize?state=" + state

    def exchange(self, code, verifier):
        assert code == "approved"
        return Tokens("private-access", "private-refresh", 3600, list(self.scopes))

    def identity(self, token):
        return Identity("creator-1", "Fred Creates", "@fred", "https://provider.example/avatar.png")

    def refresh_token(self, refresh):
        assert refresh == "private-refresh"
        return Tokens("rotated-access", "rotated-refresh", 3600, list(self.scopes))

    def disconnect(self, token):
        pass


@pytest.fixture
def social(client, app):
    settings = SocialSettings(
        public_url="https://api.example",
        frontend_url="https://studio.example",
        encryption_keys=Fernet.generate_key().decode(),
        enabled={p: True for p in PLATFORMS},
        clients={p: {"id": "client", "secret": "secret"} for p in PLATFORMS},
    )
    adapter = FakeAccountAdapter()
    service = AccountService(
        app.state.database, settings, app.state.clock, {p: adapter for p in PLATFORMS}
    )
    app.state.social_accounts = service
    return service, adapter


def connect(client, headers, platform="youtube"):
    result = client.get(f"/v1/social-accounts/{platform}/connect", headers=headers)
    assert result.status_code == 200, result.text
    state = parse_qs(urlparse(result.json()["authorization_url"]).query)["state"][0]
    return state, result.cookies[f"social_oauth_{platform}"]


def callback(client, state, binding, platform="youtube", **params):
    return client.get(
        f"/v1/social-accounts/{platform}/callback",
        params={"state": state, "code": "approved", **params},
        headers={"Cookie": f"social_oauth_{platform}={binding}"},
    )


@pytest.fixture
def render(client, headers, clock, monkeypatch):
    from app.routers import renders

    def fake(source, destination, *args, **options):
        destination.write_bytes(b"video")

    monkeypatch.setattr(renders, "render_short", fake)
    project = ready(client, headers, clock)
    clip = client.get(f"/v1/projects/{project}/clips", headers=headers).json()["clips"][0]
    result = client.post(
        f"/v1/projects/{project}/render", headers=headers, json={"clipId": clip["id"]}
    )
    assert result.status_code == 200, result.text
    return result.json()["id"]


# ---------- publishing: fake adapters, a worker fixture and request helpers ----------

INFO = {
    "size": 5,
    "duration": 30,
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "video_codec": "h264",
    "audio_codec": "aac",
    "format": "mp4",
}


class FakePublisher(FakeAccountAdapter):
    def __init__(self, platform, calls):
        self.platform, self.calls, self.failures = platform, calls, []
        self.reject = False
        # The same interface hooks the real adapters declare for these platforms.
        self.needs_creator_info = platform == "tiktok"
        self.supports_thumbnail = platform == "youtube"

    def upload_complete(self, checkpoint):
        return False

    # Quota hooks (untracked by default; quota tests set limits and costs).
    quota_timezone = "UTC"

    def quota_limits(self):
        return getattr(self, "limits", {})

    def quota_costs(self, action, checkpoint):
        return getattr(self, "costs", {}).get(action, {})

    def creator_info(self, token):
        return {
            "account_name": "Fred",
            "username": "fred",
            "privacy_options": ["SELF_ONLY"],
            "comment_disabled": False,
            "duet_disabled": False,
            "stitch_disabled": False,
            "max_duration": 60,
        }

    def validate(self, video, metadata, creator=None):
        if self.reject:
            raise ProviderError("MEDIA_REJECTED")

    def publish(self, token, path, info, metadata, cp):
        self.calls[self.platform] += 1
        if self.failures:
            raise self.failures.pop(0)
        return PublishStep("processing", {**cp, "uploaded": True}, delay=0)

    def get_status(self, token, cp):
        return PublishStep(
            "published",
            cp,
            post_id=self.platform + "-post",
            post_url="https://platform.example/" + self.platform,
        )


@pytest.fixture
def publishing(app, social, settings, clock, monkeypatch):
    accounts = social[0]
    calls = Counter()
    adapters = {p: FakePublisher(p, calls) for p in ["youtube", "tiktok", "x"]}
    accounts.adapters = adapters
    app.state.social_publishing.accounts = accounts
    monkeypatch.setattr("app.social.publishing.inspect_video", lambda *args: INFO)
    monkeypatch.setattr("app.social.worker.inspect_video", lambda *args: INFO)
    return PublishWorker(app.state.database, settings, accounts, clock), adapters, calls


def targets(client, headers, platforms):
    result = []
    for platform in platforms:
        state, binding = connect(client, headers, platform)
        callback(client, state, binding, platform)
        account = next(
            a
            for a in client.get("/v1/social-accounts", headers=headers).json()["accounts"]
            if a["platform"] == platform
        )
        from test_social_copy import COPY

        metadata = dict(COPY[platform])
        if platform == "youtube":
            metadata["visibility"] = "private"
        if platform == "tiktok":
            metadata.update(privacy="SELF_ONLY", consent=True)
        result.append(
            {"platform": platform, "social_account_id": account["id"], platform: metadata}
        )
    return result


def start(client, headers, render, selected, key="publish-once"):
    return client.post(
        f"/v1/renders/{render}/publish",
        headers={**headers, "Idempotency-Key": key},
        json={"targets": selected, "approved": True},
    )


def drain(worker, clock, count=20):
    for _ in range(count):
        if not worker.run_once():
            clock.advance(16)
