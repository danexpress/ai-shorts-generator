from collections import Counter
from dataclasses import replace
from typing import ClassVar

import pytest
from cryptography.fernet import Fernet
from test_social_publishing import INFO, drain, start, targets

from app.social.accounts import AccountService
from app.social.adapters import BUILTIN, load_adapter, publishers
from app.social.adapters.base import Identity, PublishStep, SocialPublisher, Tokens
from app.social.adapters.tiktok import TikTokPublisher
from app.social.adapters.x import XPublisher
from app.social.adapters.youtube import YouTubePublisher
from app.social.config import PLATFORMS, SocialSettings
from app.social.worker import PublishWorker

UNIFIED = "test_social_registry:UnifiedPublisher"


class UnifiedPublisher(SocialPublisher):
    """Stand-in for a unified posting API: one class, many platforms, own credentials."""

    requires_client_credentials = False
    needs_creator_info = True  # exercises the admission hook on a non-TikTok class
    calls: ClassVar[Counter] = Counter()

    def connect(self, state, verifier):
        return f"https://unified.example/link?platform={self.platform}&state={state}"

    def exchange(self, code, verifier):
        return Tokens(f"unified-{self.platform}", None, 3600, ["post"])

    def refresh_token(self, refresh):
        return Tokens(f"unified-{self.platform}", None, 3600, ["post"])

    def identity(self, token):
        return Identity(f"{self.platform}-profile", "Fred via Unified", "@fred")

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
        pass

    def publish(self, token, video, info, metadata, checkpoint):
        UnifiedPublisher.calls[self.platform] += 1
        return PublishStep("processing", {**checkpoint, "job": self.platform}, delay=0)

    def get_status(self, token, checkpoint):
        return PublishStep(
            "published",
            checkpoint,
            post_id=f"u-{self.platform}",
            post_url=f"https://unified.example/{self.platform}/post",
        )

    def disconnect(self, token):
        pass


def social_settings(**overrides):
    return SocialSettings(
        public_url="https://api.example",
        frontend_url="https://studio.example",
        encryption_keys=Fernet.generate_key().decode(),
        enabled={p: True for p in PLATFORMS},
        clients={p: {"id": "", "secret": ""} for p in PLATFORMS},
        **overrides,
    )


def test_builtin_adapters_serve_their_platforms():
    adapters = publishers(social_settings(), None)
    assert set(adapters) == set(PLATFORMS) == set(BUILTIN)
    assert isinstance(adapters["youtube"], YouTubePublisher)
    assert isinstance(adapters["tiktok"], TikTokPublisher)
    assert isinstance(adapters["x"], XPublisher)
    assert all(adapter.platform == platform for platform, adapter in adapters.items())


def test_one_class_can_replace_several_platforms_from_the_environment(monkeypatch):
    monkeypatch.setenv("SOCIAL_ADAPTER_TIKTOK", UNIFIED)
    monkeypatch.setenv("SOCIAL_ADAPTER_X", f"  {UNIFIED}  ")
    settings = SocialSettings()
    assert settings.adapter_overrides == {"tiktok": UNIFIED, "x": UNIFIED}
    adapters = publishers(settings, None)
    assert isinstance(adapters["youtube"], YouTubePublisher)  # untouched
    assert type(adapters["tiktok"]) is type(adapters["x"]) is UnifiedPublisher
    assert (adapters["tiktok"].platform, adapters["x"].platform) == ("tiktok", "x")


@pytest.mark.parametrize(
    "spec,error",
    [
        ("app.social.adapters.missing:Thing", RuntimeError),
        ("app.social.adapters.base:NoSuchClass", RuntimeError),
        ("no-colon-here", RuntimeError),
        ("app.social.adapters.base:Tokens", TypeError),  # not a SocialPublisher
    ],
)
def test_misconfigured_adapters_fail_at_startup(spec, error):
    with pytest.raises(error, match=spec.split(":")[0][:10]):
        load_adapter(spec)


def test_credentials_are_only_required_by_adapters_that_need_them():
    settings = social_settings()
    builtin, unified = (
        YouTubePublisher(settings, None),
        UnifiedPublisher(settings, None, platform="youtube"),
    )
    assert not settings.available("youtube", builtin)  # no client id/secret
    assert settings.available("youtube", unified)
    assert not replace(settings, encryption_keys="").available("youtube", unified)


def test_unified_adapter_runs_the_unchanged_connect_and_publish_flow(
    client, headers, render, app, clock, monkeypatch
):
    UnifiedPublisher.calls.clear()
    settings = social_settings(adapter_overrides={"tiktok": UNIFIED, "x": UNIFIED})
    accounts = AccountService(
        app.state.database, settings, app.state.clock, publishers(settings, None)
    )
    app.state.social_accounts = accounts
    app.state.social_publishing.accounts = accounts
    monkeypatch.setattr("app.social.publishing.inspect_video", lambda *args: INFO)
    monkeypatch.setattr("app.social.worker.inspect_video", lambda *args: INFO)

    listed = {
        p["platform"]: p["enabled"]
        for p in client.get("/v1/social-accounts", headers=headers).json()["platforms"]
    }
    assert listed == {
        "youtube": False,
        "tiktok": True,
        "x": True,
    }  # youtube lacks its own client keys
    connected = client.get("/v1/social-accounts/x/connect", headers=headers).json()
    assert connected["authorization_url"].startswith("https://unified.example/link?platform=x")

    selected = targets(client, headers, ["tiktok", "x"])
    batch = start(client, headers, render, selected)
    assert batch.status_code == 202, batch.text
    drain(PublishWorker(app.state.database, app.state.settings, accounts, clock), clock)
    jobs = client.get("/v1/publish-batches/" + batch.json()["id"], headers=headers).json()["jobs"]
    assert {j["platform"]: j["platform_post_url"] for j in jobs} == {
        "tiktok": "https://unified.example/tiktok/post",
        "x": "https://unified.example/x/post",
    }
    assert UnifiedPublisher.calls == Counter({"tiktok": 1, "x": 1})
