from collections import Counter

import pytest
from conftest import assert_error, login, rows
from social_helpers import FakeAccountAdapter, callback, connect
from test_social_copy import COPY

from app.social.adapters.base import ProviderError, PublishStep
from app.social.db_models import PublishJob, SocialAccount, SocialEvent
from app.social.worker import PublishWorker

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

    def validate_video(self, video, metadata, creator=None):
        if self.reject:
            raise ProviderError("MEDIA_REJECTED")

    def publish(self, token, path, info, metadata, cp):
        self.calls[self.platform] += 1
        if self.failures:
            raise self.failures.pop(0)
        return PublishStep("processing", {**cp, "uploaded": True}, delay=0)

    def get_publish_status(self, token, cp):
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


@pytest.mark.parametrize(
    "platforms", [["youtube"], ["youtube", "tiktok"], ["youtube", "tiktok", "x"]]
)
def test_publishes_each_selected_platform_independently(
    client, headers, render, publishing, clock, platforms
):
    worker, _adapters, calls = publishing
    selected = targets(client, headers, platforms)
    result = start(client, headers, render, selected)
    assert result.status_code == 202, result.text
    batch = result.json()
    assert len(batch["jobs"]) == len(platforms)
    assert {j["status"] for j in batch["jobs"]} == {"queued"}
    assert not calls  # No upload happens in the HTTP request.
    drain(worker, clock)
    result = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()
    assert all(j["status"] == "published" and j["platform_post_url"] for j in result["jobs"])
    assert calls == Counter({p: 1 for p in platforms})


def test_partial_failure_retry_only_failed_and_duplicate_prevention(
    client, headers, render, publishing, clock
):
    worker, adapters, calls = publishing
    adapters["x"].reject = True
    selected = targets(client, headers, ["youtube", "tiktok", "x"])
    first = start(client, headers, render, selected)
    assert first.status_code == 202, first.text
    assert start(client, headers, render, selected).json()["id"] == first.json()["id"]
    drain(worker, clock)
    result = client.get("/v1/publish-batches/" + first.json()["id"], headers=headers).json()
    assert {j["platform"]: j["status"] for j in result["jobs"]} == {
        "youtube": "published",
        "tiktok": "published",
        "x": "failed",
    }
    x = next(j for j in result["jobs"] if j["platform"] == "x")
    assert_error(
        client.post("/v1/publish-jobs/" + x["id"] + "/retry", headers=headers), 409, "INVALID_STATE"
    )
    assert_error(
        start(client, headers, render, selected, "different-key"), 409, "ALREADY_PUBLISHED"
    )
    changed = [{**selected[0], "youtube": {**selected[0]["youtube"], "title": "Changed"}}]
    assert_error(start(client, headers, render, changed), 409, "IDEMPOTENCY_CONFLICT")
    assert calls == Counter({"youtube": 1, "tiktok": 1})


def test_transient_retry_has_backoff_and_never_repeats_successful_platforms(
    client, headers, render, publishing, clock
):
    worker, adapters, calls = publishing
    adapters["x"].failures = [ProviderError("PROVIDER_UNAVAILABLE", True)]
    batch = start(client, headers, render, targets(client, headers, ["youtube", "x"])).json()
    for _ in range(5):
        worker.run_once()
    # Wait for exponential backoff, not HTTP response or fake status percentages.
    clock.advance(16)
    drain(worker, clock)
    result = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()
    assert all(j["status"] == "published" for j in result["jobs"])
    assert calls["youtube"] == 1 and calls["x"] == 2
    assert next(j for j in result["jobs"] if j["platform"] == "x")["attempt_count"] == 2


def test_manual_retry_failed_job_only(client, headers, render, publishing, clock, database):
    worker, _adapters, calls = publishing
    batch = start(client, headers, render, targets(client, headers, ["youtube", "x"])).json()
    x = next(j for j in batch["jobs"] if j["platform"] == "x")
    with database.transaction() as db:
        job = db.get(PublishJob, x["id"])
        job.status = "failed"
        job.retryable = True
        job.error_code = "PROVIDER_UNAVAILABLE"
    for _ in range(3):
        worker.run_once()
    assert client.post("/v1/publish-jobs/" + x["id"] + "/retry", headers=headers).status_code == 200
    drain(worker, clock)
    assert calls == Counter({"youtube": 1, "x": 1})
    assert any(e["event"] == "retry_initiated" for e in rows(database, SocialEvent))


def test_expired_social_token_refresh_and_disconnect_failure(
    client, headers, render, publishing, clock, database
):
    worker, _adapters, _calls = publishing
    selected = targets(client, headers, ["youtube"])
    with database.transaction() as db:
        account = db.get(SocialAccount, selected[0]["social_account_id"])
        account.token_expires_at = int(clock() * 1000)
    batch = start(client, headers, render, selected).json()
    drain(worker, clock)
    assert (
        client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0][
            "status"
        ]
        == "published"
    )
    saved = rows(database, SocialAccount)[0]
    assert (
        worker.accounts.cipher.decrypt(saved["access_token_encrypted"], saved["user_id"], "youtube")
        == "rotated-access"
    )


def test_render_and_account_ownership_are_enforced(client, headers, render, publishing):
    selected = targets(client, headers, ["youtube"])
    other = login(client, "jon@example.com")
    assert_error(start(client, other, render, selected), 404, "NOT_FOUND")
    foreign = dict(selected[0])
    foreign["social_account_id"] = "foreign"
    assert_error(start(client, headers, render, [foreign]), 404, "NOT_FOUND")


def test_rate_limit_and_explicit_approval(client, headers, render, publishing, database, clock):
    from app.social.db_models import SocialRateLimit

    selected = targets(client, headers, ["youtube"])
    assert_error(
        client.post(
            f"/v1/renders/{render}/publish",
            headers={**headers, "Idempotency-Key": "key"},
            json={"targets": selected, "approved": False},
        ),
        422,
        "INVALID_INPUT",
    )
    user_id = client.get("/v1/me", headers=headers).json()["user"]["id"]
    with database.transaction() as db:
        db.add(
            SocialRateLimit(
                user_id=user_id, action="publish", window=int(clock() * 1000) // 60_000, count=6
            )
        )
    assert_error(start(client, headers, render, selected), 429, "SOCIAL_RATE_LIMITED")


def test_worker_lease_prevents_two_workers_from_uploading_one_job(
    client, headers, render, publishing, database, settings, clock
):
    worker, _adapters, _calls = publishing
    start(client, headers, render, targets(client, headers, ["youtube"]))
    assert worker.claim()
    other = PublishWorker(database, settings, worker.accounts, clock)
    assert other.claim() is None


def test_history_survives_render_expiration(client, headers, render, publishing, database, clock):
    from app.db_models import RenderRecord

    worker, _adapters, _calls = publishing
    batch = start(client, headers, render, targets(client, headers, ["youtube"])).json()
    drain(worker, clock)
    with database.transaction() as db:
        db.get(RenderRecord, render).expiresAt = int(clock() * 1000) - 1
    client.get("/v1/projects", headers=headers)  # Existing retention cleanup deletes the render.
    history = client.get(f"/v1/renders/{render}/publishing-history", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()[0]["id"] == batch["id"]
    assert history.json()[0]["jobs"][0]["status"] == "published"


def test_ambiguous_x_post_is_never_automatically_retried(
    client, headers, render, publishing, database, clock
):
    worker, adapters, _calls = publishing
    selected = targets(client, headers, ["x"])
    batch = start(client, headers, render, selected).json()
    job_id = batch["jobs"][0]["id"]
    with database.transaction() as db:
        job = db.get(PublishJob, job_id)
        job.status = "publishing"
        job.checkpoint_encrypted = worker.accounts.cipher.encrypt(
            {"media_id": "123", "finalized": True}, job.user_id, "x"
        )
    count = []

    def ambiguous(*args):
        count.append(1)
        raise ProviderError("PROVIDER_UNAVAILABLE", True, True)

    adapters["x"].create_post = ambiguous
    worker.run_once()
    clock.advance(60)
    worker.run_once()
    job = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0]
    assert (
        len(count) == 1 and job["status"] == "failed" and job["error_code"] == "PUBLISH_UNCERTAIN"
    )
    assert_error(
        client.post("/v1/publish-jobs/" + job_id + "/retry", headers=headers), 409, "INVALID_STATE"
    )


def test_retry_attempts_are_capped_and_worker_restart_retains_job(
    client, headers, render, publishing, database, settings, clock
):
    worker, adapters, calls = publishing
    adapters["youtube"].failures = [ProviderError("PROVIDER_UNAVAILABLE", True) for _ in range(10)]
    batch = start(client, headers, render, targets(client, headers, ["youtube"])).json()
    restarted = PublishWorker(database, settings, worker.accounts, clock)
    for _ in range(5):
        restarted.run_once()
        clock.advance(130)
    job = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0]
    assert job["status"] == "failed" and job["attempt_count"] == 4 and calls["youtube"] == 4
    assert not job["retryable"]


def test_disconnect_reconnect_same_account_allows_failed_job_retry(
    client, headers, render, publishing, clock
):
    worker, _adapters, calls = publishing
    batch = start(client, headers, render, targets(client, headers, ["youtube"])).json()
    client.delete("/v1/social-accounts/youtube", headers=headers)
    worker.run_once()
    job = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0]
    assert job["error_code"] == "ACCOUNT_DISCONNECTED" and not job["retryable"]
    clock.advance(1)
    state, binding = connect(client, headers, "youtube")
    callback(client, state, binding, "youtube")
    job = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0]
    assert job["retryable"]
    assert (
        client.post("/v1/publish-jobs/" + job["id"] + "/retry", headers=headers).status_code == 200
    )
    worker.run_once()
    worker.run_once()
    assert calls["youtube"] == 1
    assert (
        client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0][
            "status"
        ]
        == "published"
    )


def test_deleted_project_cancels_social_jobs_and_purges_private_drafts(
    client, headers, render, publishing, database
):
    from app.db_models import RenderRecord
    from app.social.db_models import PublishBatch

    worker, _adapters, calls = publishing
    batch = start(client, headers, render, targets(client, headers, ["youtube"])).json()
    with database.transaction() as db:
        project_id = db.get(RenderRecord, render).projectId
    assert client.delete("/v1/projects/" + project_id, headers=headers).status_code == 200
    assert not worker.run_once() and not calls
    assert_error(
        client.get("/v1/publish-batches/" + batch["id"], headers=headers), 404, "NOT_FOUND"
    )
    assert len(rows(database, PublishBatch)) == 1
    job = rows(database, PublishJob)[0]
    assert (
        job["status"] == "failed" and job["post_metadata"] == {} and not job["checkpoint_encrypted"]
    )


def test_optional_youtube_cover_failure_does_not_repeat_video(
    client, headers, render, publishing, monkeypatch
):
    worker, adapters, calls = publishing
    selected = targets(client, headers, ["youtube"])
    selected[0]["youtube"]["use_thumbnail"] = True
    batch = start(client, headers, render, selected).json()
    monkeypatch.setattr("app.social.worker.cover_image", lambda *args: b"jpeg")

    def reject_cover(*args):
        raise ProviderError("PERMISSION_REQUIRED")

    adapters["youtube"].set_thumbnail = reject_cover
    worker.run_once()
    worker.run_once()
    job = client.get("/v1/publish-batches/" + batch["id"], headers=headers).json()["jobs"][0]
    assert job["status"] == "published" and "cover" in job["error_message_safe"]
    assert calls["youtube"] == 1 and not worker.run_once()
