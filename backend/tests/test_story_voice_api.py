from dataclasses import replace

import pytest
from conftest import assert_error, rows
from test_contract import check
from test_story_voice import CountingDemo

from app.db_models import StoryRecord
from app.story.tts import TTSFailed, TTSUnavailable
from app.story.tts.elevenlabs import ElevenLabsProvider
from app.story.worker import StoryWorker, story_dir

TOPIC = {"topic": "a lighthouse keeper who finds a message in a bottle", "genre": "mystery"}


@pytest.fixture
def voices(app, settings, database, clock):
    """Demo script writer and voice, plus an unconfigured ElevenLabs."""
    story_settings = replace(
        settings, story_model="demo", story_tts_provider="demo", whisper_align=""
    )
    demo = CountingDemo()
    providers = {"demo": demo, "elevenlabs": ElevenLabsProvider(replace(story_settings))}
    service = app.state.story
    service.settings, service.providers = story_settings, providers
    worker = StoryWorker(database, story_settings, clock, providers=providers)
    return demo, worker


def story(client, headers, seconds=30):
    response = client.post("/v1/stories", headers=headers, json={**TOPIC, "targetSeconds": seconds})
    assert response.status_code == 200, response.text
    return response.json()


def ask_voice(client, headers, item, **body):
    payload = {"revision": item["revision"], "provider": "demo", "voice": "low", "speed": 1.0}
    return client.post(f"/v1/stories/{item['id']}/voice", headers=headers, json={**payload, **body})


def get(client, headers, item):
    return client.get(f"/v1/stories/{item['id']}", headers=headers).json()


def test_voice_is_generated_in_the_background_and_can_be_played(
    client, headers, voices, clock, app
):
    _demo, worker = voices
    listed = check(client.get("/v1/story-voices", headers=headers), "/v1/story-voices", "get")
    providers = {p["id"]: p for p in listed["providers"]}
    assert listed["defaultProvider"] == "demo" and providers["demo"]["available"]
    assert providers["elevenlabs"]["available"] is False and providers["elevenlabs"]["reason"]
    app.state.story.settings = replace(app.state.story.settings, story_tts_provider="kokoro")
    hidden = client.get("/v1/story-voices", headers=headers).json()
    assert "demo" not in [p["id"] for p in hidden["providers"]]  # offline tones stay hidden
    app.state.story.settings = replace(app.state.story.settings, story_tts_provider="demo")

    item = story(client, headers)
    queued = check(ask_voice(client, headers, item), "/v1/stories/{id}/voice", "post")
    assert queued["status"] == "voicing" and queued["voiceJob"]["progress"] == 0
    assert queued["voiceJob"]["request"] == {"provider": "demo", "voice": "low", "speed": 1.0}
    assert queued["voice"] is None and queued["revision"] == item["revision"] + 1

    assert worker.run_once() and not worker.run_once()
    done = check(
        client.get(f"/v1/stories/{item['id']}", headers=headers), "/v1/stories/{id}", "get"
    )
    voice = done["voice"]
    assert done["status"] == "voiced" and voice["stale"] is False and voice["tooLong"] is False
    assert len(voice["words"]) == done["wordCount"] and voice["timingSources"] == ["provider"]
    assert [s["id"] for s in voice["scenes"]] == [s["id"] for s in done["script"]["scenes"]]
    assert voice["loudness"]["outputLufs"] == pytest.approx(-14, abs=0.6)
    assert done["voiceJob"] == {"request": None, "progress": None, "error": None}

    link = check(
        client.post(f"/v1/stories/{item['id']}/voice/playback", headers=headers),
        "/v1/stories/{id}/voice/playback",
        "post",
    )
    played = client.get(link["url"])
    assert played.status_code == 200 and played.headers["content-type"] == "audio/wav"
    assert played.content[:4] == b"RIFF"
    ranged = client.get(link["url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    assert_error(
        client.get(link["url"].replace("signature=", "signature=0")), 401, "UNAUTHENTICATED"
    )
    clock.advance(31 * 60)
    assert_error(client.get(link["url"]), 401, "UNAUTHENTICATED")


def test_script_changes_mark_the_voice_stale_and_are_blocked_while_voicing(client, headers, voices):
    _demo, worker = voices
    item = story(client, headers)
    queued = ask_voice(client, headers, item).json()
    script = {**queued["script"], "title": "Changed while voicing"}
    busy = client.put(
        f"/v1/stories/{item['id']}/script",
        headers=headers,
        json={"revision": queued["revision"], "script": script},
    )
    assert_error(busy, 409, "STORY_BUSY")
    assert_error(ask_voice(client, headers, queued), 409, "STORY_BUSY")
    worker.run_once()
    done = get(client, headers, item)
    retitled = client.put(
        f"/v1/stories/{item['id']}/script",
        headers=headers,
        json={"revision": done["revision"], "script": {**done["script"], "title": "New title"}},
    ).json()
    assert retitled["voice"]["stale"] is False  # only the spoken words matter
    scenes = [dict(s) for s in retitled["script"]["scenes"]]
    scenes[0]["narration"] += " One more line."
    edited = client.put(
        f"/v1/stories/{item['id']}/script",
        headers=headers,
        json={"revision": retitled["revision"], "script": {**retitled["script"], "scenes": scenes}},
    ).json()
    assert edited["voice"]["stale"] is True


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"provider": "nope"}, 422, "INVALID_INPUT"),
        ({"voice": "nope"}, 422, "INVALID_INPUT"),
        ({"speed": 2.5}, 422, "INVALID_INPUT"),
        ({"provider": "elevenlabs", "voice": "21m00Tcm4TlvDq8ikWAM"}, 409, "VOICE_UNAVAILABLE"),
        ({"revision": 0}, 409, "STALE_REVISION"),
    ],
)
def test_invalid_voice_requests(client, headers, voices, body, status, code):
    item = story(client, headers)
    assert_error(ask_voice(client, headers, item, **body), status, code)
    assert get(client, headers, item)["status"] == "draft"


def test_speed_is_limited_to_the_providers_range(client, headers, voices, app):
    demo = app.state.story.providers["demo"]
    demo.speed_range, demo.default_speed = (0.8, 1.2), 0.9
    item = story(client, headers)
    response = ask_voice(client, headers, item, speed=1.5)
    assert_error(response, 422, "INVALID_INPUT")
    assert "between 0.8x and 1.2x" in response.json()["error"]["message"]
    queued = ask_voice(client, headers, item, speed=None).json()
    assert queued["voiceJob"]["request"]["speed"] == 0.9  # the provider's default


def test_unavailable_providers_are_retried_then_fail(client, headers, voices, clock):
    demo, worker = voices
    calls = []

    def down(*args):
        calls.append(1)
        raise TTSUnavailable("ElevenLabs is busy (HTTP 503); retry later.")

    demo.synthesize = down
    item = story(client, headers)
    ask_voice(client, headers, item)
    assert worker.run_once()
    assert get(client, headers, item)["status"] == "voicing"
    assert not worker.run_once()  # waiting out the backoff
    clock.advance(31)
    worker.run_once()
    clock.advance(61)
    worker.run_once()
    failed = get(client, headers, item)
    assert len(calls) == 3 and failed["status"] == "voice_failed"
    assert failed["voiceJob"]["error"] == {
        "code": "VOICE_UNAVAILABLE",
        "message": "ElevenLabs is busy (HTTP 503); retry later.",
    }


def test_rejected_text_fails_at_once_and_can_be_retried(client, headers, voices):
    demo, worker = voices
    original = demo.synthesize
    demo.synthesize = lambda *args: (_ for _ in ()).throw(TTSFailed("Voice rejected the text."))
    item = story(client, headers)
    ask_voice(client, headers, item)
    worker.run_once()
    failed = get(client, headers, item)
    assert failed["status"] == "voice_failed"
    assert failed["voiceJob"]["error"]["message"] == "Voice rejected the text."
    demo.synthesize = original
    ask_voice(client, headers, failed)
    worker.run_once()
    assert get(client, headers, item)["status"] == "voiced"


def test_a_new_voice_replaces_the_old_file(client, headers, voices, settings):
    _demo, worker = voices
    item = story(client, headers)
    ask_voice(client, headers, item)
    worker.run_once()
    first = get(client, headers, item)
    ask_voice(client, headers, first, voice="high", speed=1.2)
    worker.run_once()
    second = get(client, headers, item)
    assert second["voice"]["voice"] == "high" and second["voice"]["speed"] == 1.2
    assert second["voice"]["durationSec"] < first["voice"]["durationSec"]
    assert len(list(story_dir(settings.media_dir, item["id"]).glob("*.wav"))) == 1


def test_a_voiceover_over_60_seconds_asks_for_a_shorter_script(client, headers, voices):
    _demo, worker = voices
    item = story(client, headers, seconds=60)
    ask_voice(client, headers, item, speed=0.6)
    worker.run_once()
    done = get(client, headers, item)
    assert done["voice"]["tooLong"] is True
    assert any("Shorten the script" in w for w in done["warnings"])


def test_deleting_a_story_while_voicing_discards_the_result(
    client, headers, voices, settings, database
):
    demo, worker = voices
    item = story(client, headers)
    ask_voice(client, headers, item)
    original = demo.synthesize

    def delete_midway(*args):
        result = original(*args)
        client.delete(f"/v1/stories/{item['id']}", headers=headers)
        return result

    demo.synthesize = delete_midway
    assert worker.run_once()
    assert rows(database, StoryRecord) == []
    assert not story_dir(settings.media_dir, item["id"]).exists() or not list(
        story_dir(settings.media_dir, item["id"]).glob("*.wav")
    )


def test_deleting_the_story_or_account_removes_voice_files(client, headers, voices, settings):
    _demo, worker = voices
    first, second = story(client, headers), story(client, headers, seconds=45)
    for item in (first, second):
        ask_voice(client, headers, item)
        worker.run_once()
    assert story_dir(settings.media_dir, first["id"]).is_dir()
    client.delete(f"/v1/stories/{first['id']}", headers=headers)
    assert not story_dir(settings.media_dir, first["id"]).exists()
    response = client.request(
        "DELETE", "/v1/me", headers=headers, json={"confirmEmail": "maya@example.com"}
    )
    assert response.status_code == 200, response.text
    assert not story_dir(settings.media_dir, second["id"]).exists()
