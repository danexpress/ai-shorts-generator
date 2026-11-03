from datetime import UTC, datetime

from conftest import assert_error, login, record
from social_helpers import drain, start, targets

from app.db_models import RenderRecord

HOUR, DAY = 3600, 86_400


def at(clock, seconds):
    return datetime.fromtimestamp(clock() + seconds, UTC).isoformat()


def schedule(client, headers, render, selected, when, key="scheduled-once"):
    return client.post(
        f"/v1/renders/{render}/publish",
        headers={**headers, "Idempotency-Key": key},
        json={"targets": selected, "approved": True, "scheduled_at": when},
    )


def batch(client, headers, batch_id):
    return client.get(f"/v1/publish-batches/{batch_id}", headers=headers).json()


def test_scheduled_posts_wait_for_their_time_then_publish(
    client, headers, render, publishing, clock
):
    worker, _adapters, calls = publishing
    selected = targets(client, headers, ["youtube", "x"])
    response = schedule(client, headers, render, selected, at(clock, 2 * HOUR))
    assert response.status_code == 202, response.text
    created = response.json()
    due = int((clock() + 2 * HOUR) * 1000)
    assert created["scheduled_at"] == due
    assert {(j["status"], j["scheduled_at"]) for j in created["jobs"]} == {("scheduled", due)}

    drain(worker, clock)  # about five minutes pass: too early
    assert not calls
    assert {j["status"] for j in batch(client, headers, created["id"])["jobs"]} == {"scheduled"}

    clock.advance(2 * HOUR)
    headers = login(client)  # bearer tokens last an hour
    drain(worker, clock)
    jobs = batch(client, headers, created["id"])["jobs"]
    assert all(j["status"] == "published" and j["platform_post_url"] for j in jobs)
    assert calls == {"youtube": 1, "x": 1}


def test_schedule_times_are_validated(client, headers, render, publishing, clock):
    selected = targets(client, headers, ["youtube"])
    cases = [
        (at(clock, 30), 422, "SCHEDULE_IN_PAST"),
        (at(clock, -HOUR), 422, "SCHEDULE_IN_PAST"),
        (at(clock, 31 * DAY), 422, "SCHEDULE_TOO_FAR"),
        ("2026-12-01T10:00:00", 422, "INVALID_INPUT"),  # no timezone: ambiguous
    ]
    for index, (when, status, code) in enumerate(cases):
        assert_error(
            schedule(client, headers, render, selected, when, key=f"k{index}"), status, code
        )


def test_scheduling_keeps_the_render_until_after_it_posts(
    client, headers, render, publishing, clock, database
):
    worker, _adapters, _calls = publishing
    expires = record(database, RenderRecord, render)["expiresAt"]
    selected = targets(client, headers, ["tiktok"])
    when = clock() + 10 * DAY  # past the normal 7-day render retention
    response = schedule(client, headers, render, selected, at(clock, 10 * DAY))
    assert response.status_code == 202, response.text
    kept = record(database, RenderRecord, render)["expiresAt"]
    assert kept > expires and kept >= int(when * 1000) + DAY * 1000
    clock.advance(10 * DAY)
    headers = login(client)
    client.get("/v1/me", headers=login(client))  # requests run retention cleanup
    drain(worker, clock)
    assert batch(client, headers, response.json()["id"])["jobs"][0]["status"] == "published"


def test_canceling_removes_unstarted_posts_and_frees_the_short(
    client, headers, render, publishing, clock
):
    worker, _adapters, calls = publishing
    selected = targets(client, headers, ["youtube"])
    created = schedule(client, headers, render, selected, at(clock, HOUR)).json()
    canceled = client.post(f"/v1/publish-batches/{created['id']}/cancel", headers=headers)
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["jobs"] == []
    assert client.get(f"/v1/renders/{render}/publishing-history", headers=headers).json() == []
    clock.advance(2 * HOUR)
    headers = login(client)
    drain(worker, clock)
    assert not calls  # never posted
    # The same Short can be published to the same account again.
    again = start(client, headers, render, selected, key="post-now-after-cancel")
    assert again.status_code == 202, again.text
    assert_error(
        client.post(f"/v1/publish-batches/{created['id']}/cancel", headers=headers),
        409,
        "NOT_SCHEDULED",
    )


def test_rescheduling_moves_posts_until_publishing_starts(
    client, headers, render, publishing, clock
):
    worker, _adapters, calls = publishing
    selected = targets(client, headers, ["youtube"])
    created = schedule(client, headers, render, selected, at(clock, 2 * HOUR)).json()
    moved = client.patch(
        f"/v1/publish-batches/{created['id']}",
        headers=headers,
        json={"scheduled_at": at(clock, 5 * HOUR)},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["scheduled_at"] == int((clock() + 5 * HOUR) * 1000)
    assert_error(
        client.patch(
            f"/v1/publish-batches/{created['id']}",
            headers=headers,
            json={"scheduled_at": at(clock, 10)},
        ),
        422,
        "SCHEDULE_IN_PAST",
    )
    clock.advance(3 * HOUR)
    headers = login(client)
    drain(worker, clock)
    assert not calls  # the old time passed without posting
    clock.advance(2 * HOUR)
    headers = login(client)
    drain(worker, clock)
    assert calls == {"youtube": 1}
    # Once publishing has started, the post can no longer be moved or canceled.
    url = f"/v1/publish-batches/{created['id']}"
    assert_error(
        client.patch(url, headers=headers, json={"scheduled_at": at(clock, HOUR)}),
        409,
        "NOT_SCHEDULED",
    )
    assert_error(client.post(url + "/cancel", headers=headers), 409, "NOT_SCHEDULED")


def test_the_uncertainty_window_starts_at_the_scheduled_time(
    client, headers, render, publishing, clock
):
    worker, _adapters, _calls = publishing
    selected = targets(client, headers, ["youtube"])
    created = schedule(client, headers, render, selected, at(clock, 25 * HOUR)).json()
    clock.advance(25 * HOUR)  # more than 24 h after the request was made
    headers = login(client)
    drain(worker, clock)
    job = batch(client, headers, created["id"])["jobs"][0]
    assert job["status"] == "published", job
    assert job["error_code"] is None


def test_scheduled_requests_are_idempotent_and_private(client, headers, render, publishing, clock):
    selected = targets(client, headers, ["youtube"])
    when = at(clock, HOUR)
    first = schedule(client, headers, render, selected, when).json()
    assert schedule(client, headers, render, selected, when).json()["id"] == first["id"]
    assert_error(
        schedule(client, headers, render, selected, at(clock, 2 * HOUR)),
        409,
        "IDEMPOTENCY_CONFLICT",
    )
    stranger = login(client, "jon@example.com")
    url = f"/v1/publish-batches/{first['id']}"
    assert_error(client.post(url + "/cancel", headers=stranger), 404, "NOT_FOUND")
    assert_error(
        client.patch(url, headers=stranger, json={"scheduled_at": at(clock, 2 * HOUR)}),
        404,
        "NOT_FOUND",
    )
