import json
from datetime import UTC, datetime

import httpx
import pytest
from conftest import login, record, rows
from social_helpers import drain, start, targets

from app.db_models import RenderRecord
from app.social.adapters.base import ProviderError
from app.social.adapters.youtube import YouTubePublisher, with_shorts_tag
from app.social.config import SocialSettings
from app.social.db_models import ProviderQuotaUsage, PublishJob
from app.social.http import ProviderHTTP
from app.social.worker import QUOTA_NOTICE


def extra_render(client, headers, database, render):
    """Another render of the same clip: a separate Short that can be posted again."""
    first = record(database, RenderRecord, render)
    response = client.post(
        f"/v1/projects/{first['projectId']}/render",
        headers=headers,
        json={"clipId": first["clipId"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def youtube_jobs(database):
    return sorted(
        (j for j in rows(database, PublishJob) if j["platform"] == "youtube"),
        key=lambda j: j["created_at"],
    )


def next_midnight_ms(clock):
    now = datetime.fromtimestamp(clock(), UTC)
    return int(datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() * 1000) + 86_400_000


def test_uploads_wait_for_the_daily_reset_instead_of_failing(
    client, headers, render, publishing, clock, database
):
    worker, adapters, calls = publishing
    youtube = adapters["youtube"]
    youtube.limits = {"youtube:uploads": 2}
    youtube.costs = {"publish": {"youtube:uploads": 1}}
    renders = [
        render,
        extra_render(client, headers, database, render),
        extra_render(client, headers, database, render),
    ]
    selected = targets(client, headers, ["youtube"])
    for index, short in enumerate(renders):
        assert start(client, headers, short, selected, key=f"k{index}").status_code == 202

    drain(worker, clock)
    jobs = youtube_jobs(database)
    assert [j["status"] for j in jobs] == ["published", "published", "queued"]
    held = jobs[2]
    assert held["error_message_safe"] == QUOTA_NOTICE and held["error_code"] is None
    assert held["attempt_count"] == 0  # waiting does not use up retries
    assert held["next_attempt_at"] >= next_midnight_ms(clock)
    assert calls["youtube"] == 2
    assert rows(database, ProviderQuotaUsage)[0]["units"] == 2

    clock.advance(next_midnight_ms(clock) / 1000 - clock() + 120)  # just after the reset
    login(client)
    drain(worker, clock)
    assert youtube_jobs(database)[2]["status"] == "published"
    assert calls["youtube"] == 3


def test_platform_quota_errors_mark_the_bucket_full_and_hold_the_job(
    client, headers, render, publishing, clock, database
):
    worker, adapters, _calls = publishing
    youtube = adapters["youtube"]
    youtube.limits = {"youtube:uploads": 100}
    youtube.costs = {"publish": {"youtube:uploads": 1}}
    youtube.failures = [ProviderError("QUOTA_EXCEEDED")]  # shared project used it up elsewhere
    assert start(client, headers, render, targets(client, headers, ["youtube"])).status_code == 202
    drain(worker, clock)
    job = youtube_jobs(database)[0]
    assert (job["status"], job["error_message_safe"], job["attempt_count"]) == (
        "queued",
        QUOTA_NOTICE,
        0,
    )
    assert rows(database, ProviderQuotaUsage)[0]["units"] == 100
    clock.advance(next_midnight_ms(clock) / 1000 - clock() + 120)
    drain(worker, clock)
    assert youtube_jobs(database)[0]["status"] == "published"


def test_a_full_quota_skips_the_optional_cover_but_keeps_the_video(
    client, headers, render, publishing, clock, database
):
    worker, adapters, _calls = publishing
    youtube = adapters["youtube"]
    youtube.limits = {"youtube:units": 10}
    youtube.costs = {"thumbnail": {"youtube:units": 50}}
    youtube.set_thumbnail = lambda *args: pytest.fail("cover must be skipped")
    selected = targets(client, headers, ["youtube"])
    selected[0]["youtube"]["use_thumbnail"] = True
    assert start(client, headers, render, selected).status_code == 202
    drain(worker, clock)
    job = youtube_jobs(database)[0]
    assert job["status"] == "published"
    assert "cover could not be applied" in job["error_message_safe"]


def test_youtube_quota_resets_at_midnight_pacific(app, clock, publishing):
    worker, _adapters, _calls = publishing
    adapter = YouTubePublisher(SocialSettings(), None)
    # 07:30 UTC on 3 Nov 2026 is 23:30 on 2 Nov in Los Angeles (PST, UTC-8).
    clock.value = datetime(2026, 11, 3, 7, 30, tzinfo=UTC).timestamp()
    day, reset = worker.quota_day(adapter)
    assert day == "2026-11-02"
    assert reset == int(datetime(2026, 11, 3, 8, 0, tzinfo=UTC).timestamp() * 1000)


def test_youtube_declares_its_official_quota_costs(monkeypatch):
    monkeypatch.setenv("YOUTUBE_DAILY_UPLOADS", "250")
    adapter = YouTubePublisher(SocialSettings(), None)
    assert adapter.quota_limits() == {"youtube:uploads": 250, "youtube:units": 10_000}
    assert adapter.quota_costs("publish", {}) == {"youtube:uploads": 1}  # videos.insert
    assert adapter.quota_costs("publish", {"upload_url": "https://x"}) == {}  # chunks are free
    assert adapter.quota_costs("status", {}) == {"youtube:units": 1}  # videos.list
    assert adapter.quota_costs("thumbnail", {}) == {"youtube:units": 50}  # thumbnails.set


@pytest.mark.parametrize(
    "reason,code,retryable",
    [
        ("quotaExceeded", "QUOTA_EXCEEDED", False),
        ("uploadLimitExceeded", "QUOTA_EXCEEDED", False),
        ("rateLimitExceeded", "PROVIDER_RATE_LIMITED", True),
        ("forbidden", "PERMISSION_REQUIRED", False),
    ],
)
def test_youtube_quota_errors_are_recognised(reason, code, retryable):
    body = {"error": {"code": 403, "errors": [{"reason": reason}]}}
    http = ProviderHTTP(httpx.MockTransport(lambda request: httpx.Response(403, json=body)))
    try:
        with pytest.raises(ProviderError) as raised:
            http.request(
                "POST",
                "https://www.googleapis.com/upload/youtube/v3/videos",
                hosts=["googleapis.com"],
            )
        assert (raised.value.code, raised.value.retryable) == (code, retryable)
    finally:
        http.close()


def test_shorts_tag_is_added_where_missing_and_it_fits():
    assert with_shorts_tag("Hire well", "A lesson", ["#career"]) == (
        "Hire well #Shorts",
        "A lesson\n\n#career #Shorts",
    )
    assert with_shorts_tag("Hire well #shorts", "A lesson", []) == ("Hire well #shorts", "A lesson")
    # Already in the hashtags: not repeated there, but the title still gets it.
    assert with_shorts_tag("Hire well", "A lesson", ["#Shorts"]) == (
        "Hire well #Shorts",
        "A lesson\n\n#Shorts",
    )
    long_title = "T" * 95
    assert with_shorts_tag(long_title, "d", [])[0] == long_title  # would exceed 100 characters
    full = "x" * 4996
    title, description = with_shorts_tag("Hire", full, [])
    assert description == full and title == "Hire #Shorts"  # description at its byte limit


def test_youtube_upload_request_carries_the_shorts_tag(tmp_path):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"Location": "https://www.googleapis.com/resumable/session"}
        )

    http = ProviderHTTP(httpx.MockTransport(handler))
    try:
        adapter = YouTubePublisher(
            SocialSettings(clients={"youtube": {"id": "i", "secret": "s"}}), http
        )
        metadata = {
            "title": "Hire well",
            "description": "A lesson",
            "hashtags": ["#career"],
            "visibility": "private",
        }
        adapter.publish("token", tmp_path / "v.mp4", {"size": 5}, metadata, {})
        snippet = sent[0]["snippet"]
        assert snippet["title"] == "Hire well #Shorts"
        assert snippet["description"].endswith("#career #Shorts")
    finally:
        http.close()
