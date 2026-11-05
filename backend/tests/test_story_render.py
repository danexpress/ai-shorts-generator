import re
import subprocess
from dataclasses import replace

import pytest
from conftest import assert_error, rows, uploaded
from test_contract import check
from test_highlights import FakeLLM
from test_story_script import sized
from test_story_voice import CountingDemo

from app.captions.service import exports
from app.db_models import ProjectRecord, RenderRecord, StoryRecord
from app.llm import Usage
from app.renderer import ffmpeg_path
from app.social.db_models import RenderContext
from app.story import audio, visuals
from app.story.music import bed_gain, music_filter
from app.story.render import OUTRO, StoryRenderPlan, build_command, render_story, story_duration
from app.story.titlecard import TITLE_SECONDS, story_captions, with_title_card
from app.story.tts.elevenlabs import ElevenLabsProvider
from app.story.worker import StoryWorker, story_dir

FFMPEG = ffmpeg_path(None, need_subtitles=True)
TOPIC = {"topic": "a lighthouse keeper who finds a message in a bottle", "genre": "mystery"}


def probe(path):
    command = [FFMPEG, "-hide_banner", "-i", str(path)]
    info = subprocess.run(command, capture_output=True, text=True, check=False)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info.stderr)
    h, m, s = duration.groups()
    return int(h) * 3600 + int(m) * 60 + float(s), info.stderr


def tone_file(path, seconds, frequency=330):
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi"]
    command += ["-i", f"sine=f={frequency}:d={seconds}", str(path)]
    subprocess.run(command, check=True)
    return path


def words(*texts, step=0.5):
    return [
        {"text": t, "start": i * step, "end": i * step + step * 0.8, "scene": 1}
        for i, t in enumerate(texts)
    ]


# ---------- timing, title card and captions ----------


def test_duration_adds_an_outro_but_stays_within_60_seconds():
    assert story_duration(30) == pytest.approx(30 + OUTRO)
    assert story_duration(59.6) == 60
    assert story_duration(61) == pytest.approx(61 + OUTRO)  # already over: nothing is cut


def test_captions_reuse_the_karaoke_module_and_keep_spoken_fillers():
    spoken = words("Hmm.", "The", "bottle", "was", "still", "warm.")
    ass, snapshot, preset = story_captions(spoken, 3.0, "creator-pop")
    assert preset.id == "creator-pop" and snapshot["clipStart"] == 0
    chunks = snapshot["chunks"]
    assert [w["text"] for c in chunks for w in c["words"]] == [w["text"] for w in spoken]
    assert all(1 <= len(c["words"]) <= 3 for c in chunks)
    assert "Style: Caption,Montserrat Black" in ass and "Dialogue: 0," in ass
    assert set(exports(snapshot)) == {"srt", "ass"}


def title_events(ass: str) -> tuple[str, str]:
    box, text = [line for line in ass.splitlines() if ",Title," in line]
    return box, text


def test_title_card_is_large_text_on_one_box_for_the_first_two_seconds():
    ass, _snapshot, preset = story_captions(words("One", "two"), 2.0, "clean-bold")
    carded = with_title_card(ass, "The {Second} Bottle", preset)
    style = next(line for line in carded.splitlines() if line.startswith("Style: Title,"))
    fields = style.split(",")
    assert fields[1] == preset.font_name and int(fields[2]) >= 108
    box, text = title_events(carded)
    assert box.startswith("Dialogue: 1,0:00:00.00,0:00:02.00,Title") and "\\p1" in box
    assert re.search(r"}m 0 0 l (\d+) 0 \1 (\d+) 0 \2$", box)  # one rectangle for all lines
    assert text.startswith("Dialogue: 2,0:00:00.00,0:00:02.00,Title")
    assert "\\fad(150,300)" in text and "\\fad(150,300)" in box
    assert text.split("}", 1)[1].replace("\\N", " ") == "The (Second) Bottle"
    assert carded.index("Style: Title") < carded.index("Style: Caption")
    assert with_title_card(ass, "   ", preset) == ass


def test_long_titles_wrap_and_shrink_to_fit_three_lines():
    ass, _snapshot, preset = story_captions(words("x"), 1.0, "creator-pop")
    title = "The Night the Lighthouse Keeper Answered a Message From His Own Future Self"
    _box, text = title_events(with_title_card(ass, title, preset))
    body = text.split("}", 1)[1]
    assert body.count("\\N") + 1 == 3 and body == body.upper()  # uppercase preset
    assert body.endswith("…")  # too long even at the smallest size: cut, never overflowing


# ---------- music and backgrounds ----------


def test_music_sits_18_db_under_the_voice_and_swells_after_it(tmp_path):
    track = tone_file(tmp_path / "bed.wav", 5)
    loudness = float(audio.measure(FFMPEG, track)["input_i"])
    assert loudness + bed_gain(FFMPEG, track) == pytest.approx(-32, abs=0.01)
    chain = music_filter(2, -10.5, 40.0, 40.8)
    assert chain.startswith("[2:a:0]") and chain.endswith("[music]")
    assert "volume=-10.50dB" in chain and "if(lt(t,40.000),1" in chain
    assert "afade=t=out:st=39.800:d=1.0" in chain and "atrim=duration=40.800" in chain


def test_loop_background_crops_loops_trims_and_darkens(tmp_path):
    source = visuals.LoopBackground(tmp_path / "clip.mp4", start=2)
    assert source.inputs()[:4] == ["-stream_loop", "-1", "-ss", "2.000"]
    chain = source.filter(0, 31.5, [])
    assert "force_original_aspect_ratio=increase,crop=1080:1920" in chain
    assert "fps=30,trim=duration=31.500" in chain and visuals.DARKEN in chain
    assert chain.endswith("[bg]")
    assert visuals.DARKEN not in visuals.LoopBackground(tmp_path / "x", darken=False).filter(
        0, 1, []
    )


def test_bundled_loops_are_generated_once(settings, monkeypatch):
    runs = []
    real_run = subprocess.run
    monkeypatch.setattr(
        visuals.subprocess, "run", lambda *a, **k: runs.append(1) or real_run(*a, **k)
    )
    first = visuals.bundled_file(settings, FFMPEG, visuals.BUNDLED["ember"])
    assert visuals.bundled_file(settings, FFMPEG, visuals.BUNDLED["ember"]) == first
    assert len(runs) == 1  # the second request is a cache hit
    duration, info = probe(first)
    assert duration == pytest.approx(2 * visuals.HALF_LOOP, abs=0.1) and "1080x1920" in info


def test_folder_backgrounds_cannot_escape_their_directory(settings, tmp_path):
    folder = tmp_path / "loops"
    folder.mkdir()
    (folder / "rain.mp4").write_bytes(b"video")
    (folder / "notes.txt").write_text("not a video")
    custom = replace(settings, story_backgrounds_dir=str(folder))
    assert [p.name for p in visuals.folder_files(folder, visuals.VIDEO_EXTENSIONS)] == ["rain.mp4"]
    found = visuals.resolve(None, custom, "u", {"kind": "folder", "name": "rain.mp4"}, FFMPEG)
    assert found.path == folder / "rain.mp4"
    for name in ("../secret.mp4", "notes.txt", "missing.mp4"):
        with pytest.raises(Exception, match="no longer available"):
            visuals.resolve(None, custom, "u", {"kind": "folder", "name": name}, FFMPEG)


# ---------- the single FFmpeg pass ----------


def test_one_command_mixes_background_voice_music_and_captions(tmp_path):
    plan = StoryRenderPlan(
        visuals.LoopBackground(tmp_path / "bg.mp4"),
        tmp_path / "voice.wav",
        20.0,
        [],
        [],
        "Title",
        "clean-bold",
        music=tone_file(tmp_path / "m.wav", 2),
    )
    command, duration = build_command(FFMPEG, plan, tmp_path / "out.mp4", "ass=captions.ass")
    assert duration == pytest.approx(20.8)
    assert command.count("-i") == 3 and command.count("-stream_loop") == 2
    graph = command[command.index("-filter_complex") + 1]
    assert "[bg]ass=captions.ass[v]" in graph and "apad=whole_dur=20.800[voice]" in graph
    assert "[voice][music]amix=inputs=2:duration=first:normalize=0" in graph
    for flag in (["-r", "30"], ["-c:v", "libx264"], ["-pix_fmt", "yuv420p"], ["-c:a", "aac"]):
        position = command.index(flag[0])
        assert command[position + 1] == flag[1]
    assert command[command.index("-t") + 1] == "20.800" and "+faststart" in command


def test_render_produces_a_1080x1920_30fps_h264_aac_short(settings, tmp_path):
    voice = tmp_path / "voice.wav"
    CountingDemo().synthesize("One two three four five six seven", "low", 1.0, voice, {})
    pcm = audio.decode(FFMPEG, voice)
    normalized = tmp_path / "voice48.wav"
    audio.write_wav(normalized, pcm)
    seconds = audio.seconds(pcm)
    plan = StoryRenderPlan(
        visuals.LoopBackground(visuals.bundled_file(settings, FFMPEG, visuals.BUNDLED["dusk"])),
        normalized,
        seconds,
        words("One", "two", "three", "four", "five", "six", "seven", step=0.4),
        [{"id": 1, "start": 0, "end": seconds}],
        "The Second Bottle",
        "clean-bold",
        music=tone_file(tmp_path / "bed.wav", 1, 220),  # shorter than the video: loops
    )
    result = render_story(FFMPEG, plan, tmp_path / "story.mp4")
    duration, info = probe(tmp_path / "story.mp4")
    assert duration == pytest.approx(seconds + OUTRO, abs=0.1)
    assert result["durationSec"] == pytest.approx(seconds + OUTRO, abs=0.01)
    assert re.search(r"Video: h264.*yuv420p.*1080x1920.*30 fps", info)
    assert re.search(r"Audio: aac.*48000 Hz, stereo", info)
    assert result["captions"]["chunks"] and TITLE_SECONDS == 2.0


# ---------- render jobs through the API (mocked LLM and TTS) ----------


@pytest.fixture
def studio(app, settings, database, clock, tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    tone_file(music_dir / "calm-piano.wav", 3)
    story_settings = replace(
        settings,
        story_tts_provider="demo",
        whisper_align="",
        story_music_dir=str(music_dir),
    )
    providers = {"demo": CountingDemo(), "elevenlabs": ElevenLabsProvider(story_settings)}
    llm = FakeLLM()
    llm.usage = Usage()
    service = app.state.story
    service.settings, service.providers = story_settings, providers
    service.model_factory = lambda: llm
    worker = StoryWorker(database, story_settings, clock, providers=providers)
    return llm, worker


def voiced_story(client, headers, studio):
    llm, worker = studio
    llm.answers.append(sized(70))
    story = client.post("/v1/stories", headers=headers, json={**TOPIC, "targetSeconds": 30}).json()
    body = {"revision": story["revision"], "provider": "demo", "voice": "low", "speed": 1.0}
    client.post(f"/v1/stories/{story['id']}/voice", headers=headers, json=body)
    assert worker.run_once()
    return client.get(f"/v1/stories/{story['id']}", headers=headers).json()


def render_request(client, headers, story, **body):
    payload = {"revision": story["revision"], "captionPreset": "clean-bold", **body}
    return client.post(f"/v1/stories/{story['id']}/render", headers=headers, json=payload)


def test_end_to_end_story_render_with_mocked_llm_and_tts(
    client, headers, studio, database, settings, clock
):
    _llm, worker = studio
    story = voiced_story(client, headers, studio)
    background = story["renderSettings"]["background"]
    assert (background["kind"], background["id"]) == ("bundled", "midnight")  # genre default
    assets = check(client.get("/v1/story-assets", headers=headers), "/v1/story-assets", "get")
    assert {"kind": "bundled", "id": "midnight"} in [
        {"kind": a["kind"], "id": a["id"]} for a in assets["backgrounds"]
    ]
    assert assets["music"] == [{"id": "calm-piano.wav", "label": "calm piano"}]

    queued = render_request(
        client,
        headers,
        story,
        background={"kind": "bundled", "id": "ember"},
        music="calm-piano.wav",
        titleCard="hook",
    )
    queued = check(queued, "/v1/stories/{id}/render", "post")
    assert queued["status"] == "rendering" and queued["renderJob"]["request"]["music"]
    assert worker.run_once() and not worker.run_once()

    done = client.get(f"/v1/stories/{story['id']}", headers=headers).json()
    assert done["status"] == "rendered" and done["lastRenderId"]
    assert done["renderSettings"] == {
        "background": {"kind": "bundled", "id": "ember", "name": None, "projectId": None},
        "music": "calm-piano.wav",
        "captionPreset": "clean-bold",
        "captions": True,
        "titleCard": "hook",
    }
    listed = check(
        client.get(f"/v1/stories/{story['id']}/renders", headers=headers),
        "/v1/stories/{id}/renders",
        "get",
    )["renders"]
    assert [r["id"] for r in listed] == [done["lastRenderId"]]
    video = client.get(listed[0]["downloadUrl"])
    assert video.status_code == 200 and video.content[4:8] == b"ftyp"
    assert listed[0]["filename"] == "story-the-second-bottle-1080p.mp4"
    duration = listed[0]["durationSec"]
    assert duration == pytest.approx(done["voice"]["durationSec"] + OUTRO, abs=0.01)

    # An ordinary render on a hidden project: exports, publishing copy and history work.
    (render,) = rows(database, RenderRecord)
    project = next(p for p in rows(database, ProjectRecord) if p["id"] == render["projectId"])
    assert project["sourceType"] == "story" and render["clipId"] == story["id"]
    assert render["captions"]["chunks"] and set(exports(render["captions"])) == {"srt", "ass"}
    listed_projects = client.get("/v1/projects", headers=headers).json()
    assert project["id"] not in [p["id"] for p in listed_projects]  # hidden from Projects
    context = rows(database, RenderContext)[0]["context"]
    assert context["transcript_verified"] and context["clip_transcript"].startswith(
        "The bottle was still warm."
    )
    copy = client.get(f"/v1/renders/{render['id']}/social-copy", headers=headers).json()
    assert copy["transcript_verified"] is True and copy["source_transcript"]

    # Re-rendering with other settings reuses the cached voice (nothing re-spoken).
    spoken = len(studio[1].providers["demo"].spoken)
    clock.advance(5)
    again = render_request(client, headers, done, captionPreset="minimal-highlight")
    assert again.status_code == 200
    worker.run_once()
    assert len(studio[1].providers["demo"].spoken) == spoken
    both = client.get(f"/v1/stories/{story['id']}/renders", headers=headers).json()["renders"]
    assert [r["captionPreset"] for r in both] == ["minimal-highlight", "clean-bold"]

    # Deleting the story removes its renders and hides its project.
    client.delete(f"/v1/stories/{story['id']}", headers=headers)
    assert rows(database, RenderRecord) == []
    assert not list((settings.media_dir / "renders").glob("*.mp4"))
    assert not story_dir(settings.media_dir, story["id"]).exists()


@pytest.mark.parametrize(
    "body,code",
    [
        ({"captionPreset": "comic-sans"}, "INVALID_INPUT"),
        ({"background": {"kind": "bundled", "id": "nope"}}, "BACKGROUND_UNAVAILABLE"),
        ({"background": {"kind": "folder", "id": "../../etc.mp4"}}, "BACKGROUND_UNAVAILABLE"),
        ({"background": {"kind": "library", "id": "prj_someone_else"}}, "BACKGROUND_UNAVAILABLE"),
        ({"music": "missing.mp3"}, "MUSIC_UNAVAILABLE"),
        ({"revision": 0}, "STALE_REVISION"),
    ],
)
def test_invalid_render_requests(client, headers, studio, body, code):
    story = voiced_story(client, headers, studio)
    response = render_request(client, headers, story, **body)
    assert response.json()["error"]["code"] == code, response.text
    assert response.status_code in (409, 422)


def test_rendering_needs_a_current_voice(client, headers, studio):
    llm, _worker = studio
    llm.answers.append(sized(70))
    story = client.post("/v1/stories", headers=headers, json={**TOPIC, "targetSeconds": 30}).json()
    assert_error(render_request(client, headers, story), 409, "VOICE_REQUIRED")
    voiced = voiced_story(client, headers, studio)
    scenes = [dict(s) for s in voiced["script"]["scenes"]]
    scenes[1]["narration"] = "A different line now."
    edited = client.put(
        f"/v1/stories/{voiced['id']}/script",
        headers=headers,
        json={"revision": voiced["revision"], "script": {**voiced["script"], "scenes": scenes}},
    ).json()
    assert_error(render_request(client, headers, edited), 409, "VOICE_STALE")


def test_a_story_cannot_change_while_it_renders(client, headers, studio):
    story = voiced_story(client, headers, studio)
    queued = render_request(client, headers, story).json()
    assert_error(render_request(client, headers, queued), 409, "STORY_BUSY")
    put = client.put(
        f"/v1/stories/{story['id']}/script",
        headers=headers,
        json={"revision": queued["revision"], "script": queued["script"]},
    )
    assert_error(put, 409, "STORY_BUSY")
    assert "rendering" in put.json()["error"]["message"]


def test_a_background_that_disappears_fails_the_render_readably(
    client, headers, studio, database, settings
):
    _llm, worker = studio
    project_id = uploaded(client, headers)  # an upload in the creator's library
    story = voiced_story(client, headers, studio)
    queued = render_request(
        client, headers, story, background={"kind": "library", "id": project_id}
    )
    assert queued.status_code == 200, queued.text
    client.delete(f"/v1/projects/{project_id}", headers=headers)
    worker.run_once()
    failed = client.get(f"/v1/stories/{story['id']}", headers=headers).json()
    assert failed["status"] == "render_failed"
    assert failed["renderJob"]["error"]["code"] == "BACKGROUND_UNAVAILABLE"
    assert rows(database, RenderRecord) == []


def test_an_unreadable_background_fails_without_leaving_files(client, headers, studio, settings):
    _llm, worker = studio
    project_id = uploaded(client, headers)  # "test-video!!" is not a real video
    story = voiced_story(client, headers, studio)
    render_request(client, headers, story, background={"kind": "library", "id": project_id})
    worker.run_once()
    failed = client.get(f"/v1/stories/{story['id']}", headers=headers).json()
    assert failed["status"] == "render_failed"
    assert failed["renderJob"]["error"]["code"] == "RENDER_FAILED"
    assert not list((settings.media_dir / "renders").glob("*"))
    assert rows_count(client, headers, story) == 0


def rows_count(client, headers, story):
    return len(client.get(f"/v1/stories/{story['id']}/renders", headers=headers).json()["renders"])


def test_story_projects_disappear_with_the_account(client, headers, studio, database):
    _llm, worker = studio
    story = voiced_story(client, headers, studio)
    render_request(client, headers, story)
    worker.run_once()
    assert rows(database, RenderRecord)
    response = client.request(
        "DELETE", "/v1/me", headers=headers, json={"confirmEmail": "maya@example.com"}
    )
    assert response.status_code == 200, response.text
    assert rows(database, RenderRecord) == [] and rows(database, StoryRecord) == []


# ---------- robustness: looping, cache pruning, cost tracking ----------


def test_a_background_shorter_than_the_voice_loops_to_the_full_length(settings, tmp_path):
    clip = tmp_path / "one-second.mp4"
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi"]
    command += ["-i", "testsrc2=s=640x360:d=1:r=30", "-pix_fmt", "yuv420p", str(clip)]
    subprocess.run(command, check=True)  # landscape, 1 s: cropped to 9:16 and looped
    voice = tmp_path / "voice.wav"
    audio.write_wav(voice, audio.silence(3.2))
    plan = StoryRenderPlan(visuals.LoopBackground(clip), voice, 3.2, [], [], None, None, False)
    render_story(FFMPEG, plan, tmp_path / "out.mp4")
    duration, info = probe(tmp_path / "out.mp4")
    assert duration == pytest.approx(3.2 + OUTRO, abs=0.1) and "1080x1920" in info
    frames = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(tmp_path / "out.mp4"), "-map", "0:v", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    ).stderr
    count = int(re.findall(r"frame=\s*(\d+)", frames)[-1])
    assert count == pytest.approx((3.2 + OUTRO) * 30, abs=2)  # picture all the way through


def test_cache_entries_unused_for_a_month_are_pruned(settings, clock):
    import os

    from app.story import cache

    old = cache.file_path(settings.media_dir, "tts", {"a": 1}, ".wav")
    used = cache.file_path(settings.media_dir, "tts", {"b": 2}, ".wav")
    for path in (old, used):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (clock() - 40 * 86_400, clock() - 40 * 86_400))
    cache.touch(used)  # a cache hit keeps it
    os.utime(used, (clock(), clock()))
    assert cache.prune(settings.media_dir, 30, now=clock()) == 1
    assert not old.exists() and used.exists()
    assert cache.prune(settings.media_dir, 0, now=clock()) == 0  # 0 disables pruning


def test_the_worker_prunes_at_most_hourly_while_idle(
    client, settings, database, clock, monkeypatch
):
    calls = []
    monkeypatch.setattr("app.story.cache.prune", lambda *args: calls.append(args) or 0)
    worker = StoryWorker(database, settings, clock, providers={})
    assert worker.run_once() is False and len(calls) == 1
    clock.advance(600)
    worker.run_once()
    assert len(calls) == 1
    clock.advance(3600)
    worker.run_once()
    assert len(calls) == 2 and calls[-1][1] == settings.story_cache_days


def test_each_step_reports_its_time_and_cost_and_the_story_keeps_a_total(
    client, headers, studio, database
):
    llm, worker = studio
    llm.usage = Usage(calls=1, input_tokens=900, output_tokens=300, seconds=4.2, cost=0.03)
    providers = studio[1].providers  # shared by the API and the worker
    providers["demo"] = type("Paid", (CountingDemo,), {"cost_per_character": 0.0002})()
    story = voiced_story(client, headers, studio)
    usage = story["usage"]
    assert usage["script"] == {
        "seconds": 4.2,
        "inputTokens": 900,
        "outputTokens": 300,
        "cacheReadTokens": 0,
        "costUsd": 0.03,
        "cached": False,
    }
    characters = sum(len(s["narration"]) for s in story["script"]["scenes"])
    assert usage["voice"]["billedCharacters"] == characters
    assert usage["voice"]["costUsd"] == pytest.approx(characters * 0.0002)
    assert usage["totalCostUsd"] == pytest.approx(0.03 + characters * 0.0002)
    render_request(client, headers, story)
    worker.run_once()
    rendered = client.get(f"/v1/stories/{story['id']}", headers=headers).json()["usage"]
    assert rendered["render"]["seconds"] > 0 and rendered["render"]["costUsd"] == 0
    assert rendered["totalCostUsd"] == usage["totalCostUsd"]  # rendering is local and free
    # Asking for the same voice again is served from the cache: nothing more is billed.
    body = {
        "revision": client.get(f"/v1/stories/{story['id']}", headers=headers).json()["revision"]
    }
    client.post(
        f"/v1/stories/{story['id']}/voice",
        headers=headers,
        json={**body, "provider": "demo", "voice": "low", "speed": 1.0},
    )
    worker.run_once()
    again = client.get(f"/v1/stories/{story['id']}", headers=headers).json()["usage"]
    assert again["voice"]["billedCharacters"] == 0 and again["voice"]["costUsd"] == 0
    assert again["totalCostUsd"] == usage["totalCostUsd"]
