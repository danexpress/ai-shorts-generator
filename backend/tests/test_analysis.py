from dataclasses import replace
from urllib.parse import unquote

import pytest
from conftest import assert_error, ready, record

from app.db_models import (
    TranscriptRecord,
)


def test_edits_preserve_timing_and_invalidate_analysis(client, headers, clock):
    pid = ready(client, headers, clock)
    url = f"/v1/projects/{pid}/transcript"
    transcript = client.get(url, headers=headers).json()
    first, second = transcript["segments"][:2]
    original_timings = [(w["s"], w["e"]) for w in first["words"]]
    same_count = first["text"].replace("Most", "Many", 1)
    edited = client.patch(
        url,
        headers=headers,
        json={
            "segments": [
                {"id": first["id"], "text": same_count},
                {"id": second["id"], "text": "A shorter complete thought."},
            ]
        },
    ).json()
    assert edited["revision"] == 2 and edited["userCorrected"]
    assert [(w["s"], w["e"]) for w in edited["segments"][0]["words"]] == original_timings
    assert edited["segments"][1]["timing"] == "segment"
    assert edited["segments"][1]["words"] == [
        {"w": "A shorter complete thought.", "s": second["start"], "e": second["end"]}
    ]
    assert client.get(f"/v1/projects/{pid}/clips", headers=headers).json()["stale"]
    assert client.patch(url, headers=headers, json={"segments": []}).json()["revision"] == 2


def test_edit_batch_is_atomic(client, headers, clock):
    pid = ready(client, headers, clock)
    url = f"/v1/projects/{pid}/transcript"
    original = client.get(url, headers=headers).json()
    assert_error(
        client.patch(
            url,
            headers=headers,
            json={
                "segments": [
                    {"id": original["segments"][0]["id"], "text": "Valid replacement"},
                    {"id": "missing", "text": "Will fail"},
                ]
            },
        ),
        422,
        "INVALID_INPUT",
    )
    assert client.get(url, headers=headers).json() == original
    assert_error(
        client.patch(
            url,
            headers=headers,
            json={
                "segments": [
                    {"id": original["segments"][0]["id"], "text": "   "},
                ]
            },
        ),
        422,
        "INVALID_INPUT",
    )


def test_regeneration_uses_edits_and_guidance_without_charging(
    client, headers, clock, database, app
):
    pid = ready(client, headers, clock)
    base = f"/v1/projects/{pid}"
    old = client.get(base + "/clips", headers=headers).json()
    revision = record(database, TranscriptRecord, pid)["revision"]
    used = client.get("/v1/usage", headers=headers).json()
    client.post(
        base + "/analysis-runs",
        headers={**headers, "Idempotency-Key": "regen"},
        json={
            "goal": "entertaining",
            "instruction": "Find clips about pricing",
            "avoid": ["best friend"],
        },
    )
    assert_error(
        client.patch(base + "/transcript", headers=headers, json={"segments": []}),
        409,
        "ACTIVE_JOB_EXISTS",
    )
    assert_error(client.post(base + "/cancel", headers=headers), 409, "INVALID_STATE")
    clock.advance(4)
    new = client.get(base + "/clips", headers=headers).json()
    assert new["runId"] != old["runId"] and new["goal"] == "entertaining"
    assert not any("best friend" in c["hook"].lower() for c in new["clips"])
    assert new["clips"][0]["tags"] == ["pricing", "growth"]
    assert client.get("/v1/usage", headers=headers).json() == used
    assert record(database, TranscriptRecord, pid)["revision"] == revision


def test_failed_regeneration_preserves_previous_clips(client, headers, clock, database, app):
    pid = ready(client, headers, clock)
    old = client.get(f"/v1/projects/{pid}/clips", headers=headers).json()
    app.state.fail_next.add("analyzing")
    client.post(f"/v1/projects/{pid}/analysis-runs", headers=headers, json={"goal": "viral"})
    clock.advance(4)
    status = client.get(f"/v1/projects/{pid}/status", headers=headers).json()
    assert status["status"] == "ready" and status["regenError"] == "ANALYSIS_FAILED"
    assert client.get(f"/v1/projects/{pid}/clips", headers=headers).json() == old


@pytest.mark.parametrize(
    "body",
    [
        {"goal": "funny"},
        {"goal": "viral", "instruction": "a" * 201},
        {"goal": "viral", "avoid": ["a"] * 11},
        {"goal": "viral", "avoid": ["a" * 41]},
    ],
)
def test_guidance_validation(client, headers, clock, body):
    pid = ready(client, headers, clock)
    assert_error(
        client.post(f"/v1/projects/{pid}/analysis-runs", headers=headers, json=body),
        422,
        "INVALID_INPUT",
    )


def test_downloads_contain_corrected_unicode_and_real_byte_lengths(client, headers, clock):
    pid = ready(client, headers, clock)
    client.patch(
        f"/v1/projects/{pid}/transcript",
        headers=headers,
        json={"segments": [{"id": "seg_0", "text": "Café — welcome!"}]},
    )
    files = client.get(f"/v1/projects/{pid}/downloads", headers=headers).json()
    assert {f["kind"] for f in files} == {"txt", "json"}
    for item in files:
        content = unquote(item["url"].split(",", 1)[1])
        assert "Café — welcome!" in content
        assert len(content.encode("utf-8")) == item["bytes"]


def test_youtube_flag_and_rights(client, headers, database, app, monkeypatch, settings):
    from app.routers import projects

    def fake_download(url, destination_dir, ffmpeg_binary, before_download):
        before_download(180)
        work = destination_dir / "fixture"
        work.mkdir(parents=True)
        path = work / "abcdefghijk.mp4"
        path.write_bytes(b"downloaded youtube source")
        return {
            "path": path,
            "fileName": "A useful video.mp4",
            "title": "A useful video",
            "durationSec": 180,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "sizeBytes": path.stat().st_size,
            "mimeType": "video/mp4",
        }

    monkeypatch.setattr(projects, "download_youtube", fake_download)
    body = {"url": "https://youtu.be/abcdefghijk", "rightsConfirmed": True}
    assert_error(
        client.post("/v1/projects/youtube", headers=headers, json=body), 503, "IMPORT_UNAVAILABLE"
    )
    app.state.settings = replace(app.state.settings, youtube_import=True)
    assert_error(
        client.post("/v1/projects/youtube", headers=headers, json={"url": body["url"]}),
        422,
        "RIGHTS_NOT_CONFIRMED",
    )
    response = client.post("/v1/projects/youtube", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["sourceType"] == "youtube_url"
    project_id = response.json()["id"]
    source_path = settings.media_dir / "sources" / f"{project_id}.mp4"
    assert source_path.read_bytes() == b"downloaded youtube source"
    playback = client.post(f"/v1/projects/{project_id}/playback", headers=headers).json()
    assert client.get(playback["url"]).status_code == 200
    assert (
        client.post(
            f"/v1/projects/{project_id}/process", headers=headers, json={"goal": "viral"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/watch?v=abcdefghijk",
        "http://youtu.be/abcdefghijk",
        "https://youtube.com.evil.com/watch?v=abcdefghijk",
        "file:///etc/passwd",
        "https://user:pass@youtu.be/abcdefghijk",
        "https://youtu.be:8080/abcdefghijk",
        "https://youtu.be/abc/def",
        "https://youtu.be:invalid/abcdefghijk",
    ],
)
def test_youtube_url_allowlist(client, headers, database, app, url):
    app.state.settings = replace(app.state.settings, youtube_import=True)
    assert_error(
        client.post(
            "/v1/projects/youtube", headers=headers, json={"url": url, "rightsConfirmed": True}
        ),
        422,
        "UNSUPPORTED_URL",
    )
