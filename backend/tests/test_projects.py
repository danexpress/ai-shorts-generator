from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import SOURCE, assert_error, login, ready, record, rows, uploaded
from sqlalchemy import select

from app.db_models import (
    AnalysisRunRecord,
    LedgerRecord,
    TranscriptRecord,
    UserRecord,
)


@pytest.mark.parametrize(
    "overrides,status,code",
    [
        ({"fileName": "bad.avi"}, 415, "UNSUPPORTED_MEDIA"),
        ({"mimeType": "application/octet-stream"}, 415, "UNSUPPORTED_MEDIA"),
        ({"sizeBytes": 4294967297}, 413, "SOURCE_TOO_LARGE"),
        ({"durationSec": 29}, 422, "SOURCE_TOO_SHORT"),
        ({"durationSec": 3601}, 422, "SOURCE_TOO_LONG"),
        ({"durationSec": 0}, 422, "MEDIA_PROBE_FAILED"),
        ({"hasVideo": False}, 422, "MEDIA_PROBE_FAILED"),
        ({"hasAudio": False}, 422, "MEDIA_PROBE_FAILED"),
        ({"width": 8000}, 415, "UNSUPPORTED_MEDIA"),
        ({"fps": 121}, 415, "UNSUPPORTED_MEDIA"),
        ({"width": -1}, 422, "INVALID_INPUT"),
    ],
)
def test_media_validation_is_atomic(client, headers, overrides, status, code):
    before = client.get("/v1/projects", headers=headers).json()
    assert_error(
        client.post("/v1/uploads", headers=headers, json={**SOURCE, **overrides}), status, code
    )
    assert client.get("/v1/projects", headers=headers).json() == before


def test_upload_must_complete_and_can_expire(client, headers, clock):
    up = client.post("/v1/uploads", headers=headers, json=SOURCE).json()
    assert_error(
        client.post(
            f"/v1/projects/{up['projectId']}/process", headers=headers, json={"goal": "viral"}
        ),
        409,
        "INVALID_STATE",
    )
    clock.advance(901)
    assert_error(
        client.post(f"/v1/uploads/{up['uploadId']}/complete", headers=headers), 409, "INVALID_STATE"
    )


def test_foreign_upload(client, headers):
    up = client.post("/v1/uploads", headers=headers, json=SOURCE).json()
    assert_error(
        client.post(
            f"/v1/uploads/{up['uploadId']}/complete", headers=login(client, "jon@example.com")
        ),
        404,
        "NOT_FOUND",
    )


@pytest.mark.parametrize("email", ["jon@example.com", "ops@example.com"])
def test_every_project_operation_checks_ownership(client, headers, clock, email):
    pid = ready(client, headers, clock)
    stranger = login(client, email)
    for method, suffix, body in [
        ("GET", "", None),
        ("DELETE", "", None),
        ("GET", "/status", None),
        ("GET", "/transcript", None),
        ("PATCH", "/transcript", {"segments": []}),
        ("GET", "/downloads", None),
        ("GET", "/clips", None),
        ("POST", "/process", {"goal": "viral"}),
        ("POST", "/retry", {}),
        ("POST", "/cancel", None),
        ("POST", "/analysis-runs", {"goal": "viral"}),
    ]:
        assert_error(
            client.request(method, f"/v1/projects/{pid}{suffix}", headers=stranger, json=body),
            404,
            "NOT_FOUND",
        )


def test_processing_lifecycle_and_usage(client, headers, clock):
    pid = uploaded(client, headers)
    base = f"/v1/projects/{pid}"
    assert client.get(base + "/clips", headers=headers).json() == {
        "runId": None,
        "goal": None,
        "stale": False,
        "clips": [],
    }
    assert_error(client.get(base + "/transcript", headers=headers), 409, "INVALID_STATE")
    started = client.post(base + "/process", headers=headers, json={"goal": "educational"}).json()
    assert started["status"] == "transcribing" and started["canCancel"]
    assert client.get("/v1/me", headers=headers).json()["activeJob"]["id"] == started["jobId"]
    clock.advance(2)
    assert client.get(base + "/status", headers=headers).json()["progress"] == 0.3
    clock.advance(3)
    assert client.get(base + "/status", headers=headers).json()["stage"] == "analyzing"
    clock.advance(3)
    status = client.get(base + "/status", headers=headers).json()
    assert status["status"] == "ready" and status["progress"] == 1
    assert not status["canCancel"]
    clips = client.get(base + "/clips", headers=headers).json()["clips"]
    assert len(clips) == 3
    assert all(15 <= clip["duration"] <= 90 for clip in clips)
    usage = client.get("/v1/usage", headers=headers).json()
    assert usage["usedMinutes"] == 24
    assert len([e for e in usage["entries"] if e["projectId"] == pid]) == 1


def test_idempotency_replay_and_conflict(client, headers, clock):
    pid = uploaded(client, headers)
    url = f"/v1/projects/{pid}/process"
    idem = {**headers, "Idempotency-Key": "first-attempt"}
    first = client.post(url, headers=idem, json={"goal": "viral"}).json()
    assert client.post(url, headers=idem, json={"goal": "viral"}).json() == first
    assert_error(client.post(url, headers=idem, json={"goal": "educational"}), 422, "INVALID_INPUT")
    clock.advance(8)
    # Starting a later analysis must not change the original idempotency replay's job ID.
    client.post(f"/v1/projects/{pid}/analysis-runs", headers=headers, json={"goal": "educational"})
    replay = client.post(url, headers=idem, json={"goal": "viral"}).json()
    assert replay["jobId"] == first["jobId"]
    assert (
        len(
            [
                e
                for e in client.get("/v1/usage", headers=headers).json()["entries"]
                if e["projectId"] == pid
            ]
        )
        == 1
    )


def test_parallel_duplicate_and_per_creator_concurrency(client, headers, database, app):
    pid = uploaded(client, headers)
    other = uploaded(client, headers)
    url = f"/v1/projects/{pid}/process"

    def start(_):
        return client.post(
            url, headers={**headers, "Idempotency-Key": "parallel"}, json={"goal": "viral"}
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(start, range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["jobId"] for r in responses}) == 1
    assert_error(
        client.post(f"/v1/projects/{other}/process", headers=headers, json={"goal": "viral"}),
        409,
        "ACTIVE_JOB_EXISTS",
    )
    jon = login(client, "jon@example.com")
    jon_project = uploaded(client, jon)
    assert (
        client.post(
            f"/v1/projects/{jon_project}/process", headers=jon, json={"goal": "viral"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("stage", ["transcribing", "analyzing"])
def test_retry_failure_reuses_work_and_counts_once(client, headers, clock, database, app, stage):
    pid = uploaded(client, headers)
    app.state.fail_next.add(stage)
    client.post(f"/v1/projects/{pid}/process", headers=headers, json={"goal": "educational"})
    clock.advance(8)
    state = client.get(f"/v1/projects/{pid}/status", headers=headers).json()
    assert state["status"] == "failed" and state["canRetry"]
    transcript = record(database, TranscriptRecord, pid)
    before = len([e for e in rows(database, LedgerRecord) if e["projectId"] == pid])
    assert before == (1 if stage == "analyzing" else 0)
    first = client.post(
        f"/v1/projects/{pid}/retry", headers={**headers, "Idempotency-Key": "retry"}, json={}
    )
    assert first.status_code == 200
    assert (
        client.post(
            f"/v1/projects/{pid}/retry", headers={**headers, "Idempotency-Key": "retry"}
        ).json()
        == first.json()
    )
    clock.advance(8)
    assert client.get(f"/v1/projects/{pid}/status", headers=headers).json()["status"] == "ready"
    assert len([e for e in rows(database, LedgerRecord) if e["projectId"] == pid]) == 1
    if transcript:
        assert record(database, TranscriptRecord, pid) == transcript


def test_cancel_only_charges_processed_seconds(client, headers, clock, database, app):
    pid = uploaded(client, headers)
    client.post(f"/v1/projects/{pid}/process", headers=headers, json={"goal": "viral"})
    clock.advance(2)
    response = client.post(f"/v1/projects/{pid}/cancel", headers=headers)
    assert response.json()["status"] == "canceled"
    clock.advance(10)
    client.get("/v1/me", headers=headers)
    entries = [e for e in rows(database, LedgerRecord) if e["projectId"] == pid]
    assert len(entries) == 1 and entries[0]["processedSeconds"] == 150
    assert_error(client.post(f"/v1/projects/{pid}/cancel", headers=headers), 409, "INVALID_STATE")


def test_quota_checked_before_upload_and_processing(client, headers, database, app):
    pid = uploaded(client, headers)
    with database.transaction() as session:
        user = session.scalar(select(UserRecord).where(UserRecord.email == "maya@example.com"))
        user.limitMinutes = 19
    assert_error(
        client.post("/v1/uploads", headers=headers, json=SOURCE), 402, "MONTHLY_LIMIT_REACHED"
    )
    assert_error(
        client.post(f"/v1/projects/{pid}/process", headers=headers, json={"goal": "viral"}),
        402,
        "MONTHLY_LIMIT_REACHED",
    )


def test_delete_revokes_access_and_purges_content(client, headers, clock, database, app):
    pid = ready(client, headers, clock)
    client.post(f"/v1/projects/{pid}/analysis-runs", headers=headers, json={"goal": "viral"})
    assert client.delete(f"/v1/projects/{pid}", headers=headers).json() == {
        "ok": True,
        "deletionQueued": True,
    }
    for suffix in ["", "/status", "/transcript", "/downloads", "/clips"]:
        assert_error(client.get(f"/v1/projects/{pid}{suffix}", headers=headers), 404, "NOT_FOUND")
    assert record(database, TranscriptRecord, pid) is None
    assert not any(r["projectId"] == pid for r in rows(database, AnalysisRunRecord))
    assert any(e["projectId"] == pid for e in rows(database, LedgerRecord))
    assert not any(p["id"] == pid for p in client.get("/v1/projects", headers=headers).json())


def test_expiry_removes_content_but_keeps_history(client, headers, clock, database, app):
    pid = ready(client, headers, clock)
    clock.advance(31 * 86400)
    headers = login(client)
    assert client.get(f"/v1/projects/{pid}", headers=headers).json()["status"] == "expired"
    for suffix in ["/transcript", "/downloads", "/clips"]:
        assert_error(
            client.get(f"/v1/projects/{pid}{suffix}", headers=headers), 410, "ASSET_EXPIRED"
        )
    assert record(database, TranscriptRecord, pid) is None
    assert client.get("/v1/usage", headers=headers).json()["usedMinutes"] == 0
    clock.advance(60 * 86400)
    assert_error(client.get(f"/v1/projects/{pid}", headers=login(client)), 404, "NOT_FOUND")
