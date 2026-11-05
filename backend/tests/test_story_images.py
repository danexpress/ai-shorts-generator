import base64
import json
import re
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from conftest import assert_error
from test_contract import check
from test_highlights import FakeLLM
from test_story_render import FFMPEG, OUTRO, TOPIC, probe
from test_story_script import sized
from test_story_voice import CountingDemo

from app.llm import Usage
from app.story import audio, visuals
from app.story.images import ImageFailed, ImageUnavailable
from app.story.images.demo import DemoImageProvider
from app.story.images.flux import FluxProvider
from app.story.images.openai import OpenAIImageProvider
from app.story.render import StoryRenderPlan, render_story
from app.story.scene_images import SUFFIX, generate_scene_images, scene_image_file, scene_prompt
from app.story.worker import StoryWorker

SCRIPT = {
    "title": "T",
    "hook": "Hook.",
    "scenes": [
        {
            "id": 1,
            "narration": "One.",
            "visual_description": "A lighthouse at dusk.",
            "mood": "eerie",
        },
        {"id": 2, "narration": "Two.", "visual_description": "A bottle on rocks", "mood": ""},
        {
            "id": 3,
            "narration": "Three.",
            "visual_description": "Fog over the sea.",
            "mood": "tense",
        },
    ],
}


class CountingImages(DemoImageProvider):
    def __init__(self, settings, cost=0.0):
        super().__init__(settings)
        self.drawn, self._cost = [], cost

    @property
    def cost_per_image(self):
        return self._cost

    def generate(self, prompt, seed, destination):
        self.drawn.append((prompt, seed))
        super().generate(prompt, seed, destination)


# ---------- prompts and per-scene caching ----------


def test_prompts_combine_the_scene_the_genre_style_and_shared_rules():
    prompt = scene_prompt(SCRIPT["scenes"][0], "mystery")
    assert prompt.startswith("A lighthouse at dusk. Mood: eerie. Style: moody cinematic film still")
    assert prompt.endswith(SUFFIX) and "No real or famous people" in prompt
    assert "Mood:" not in scene_prompt(SCRIPT["scenes"][1], "funny")  # no mood given
    assert scene_prompt(SCRIPT["scenes"][1], "funny").startswith("A bottle on rocks.")


def test_scene_pictures_are_cached_redrawn_per_edit_and_rerolled_by_variant(settings):
    provider = CountingImages(settings, cost=0.04)
    first = generate_scene_images(SCRIPT, "mystery", provider, settings, "sty_1")
    assert first["drawn"] == 3 and first["costUsd"] == pytest.approx(0.12)
    assert all(scene_image_file(settings, s["key"]).is_file() for s in first["scenes"])
    assert len({s["seed"] for s in first["scenes"]}) == 3  # one base seed, offset per scene
    again = generate_scene_images(SCRIPT, "mystery", provider, settings, "sty_1")
    assert again["drawn"] == 0 and again["costUsd"] == 0 and len(provider.drawn) == 3
    edited = {**SCRIPT, "scenes": [dict(s) for s in SCRIPT["scenes"]]}
    edited["scenes"][1]["visual_description"] = "A bottle half buried in sand."
    generate_scene_images(edited, "mystery", provider, settings, "sty_1")
    assert len(provider.drawn) == 4 and "half buried" in provider.drawn[-1][0]
    rerolled = generate_scene_images(SCRIPT, "mystery", provider, settings, "sty_1", variant=1)
    assert rerolled["drawn"] == 3 and {s["seed"] for s in rerolled["scenes"]}.isdisjoint(
        {s["seed"] for s in first["scenes"]}
    )


def test_an_empty_picture_fails_and_leaves_nothing_cached(settings):
    class Empty(DemoImageProvider):
        def generate(self, prompt, seed, destination):
            destination.write_bytes(b"")

    with pytest.raises(ImageFailed, match="empty"):
        generate_scene_images(SCRIPT, "mystery", Empty(settings), settings, "sty_1")
    assert not list((settings.media_dir / "story-cache").rglob("*.png"))


# ---------- pan/zoom background ----------


def test_shots_crossfade_exactly_at_scene_starts():
    timeline = [{"start": 0.0}, {"start": 3.05}, {"start": 6.15}]
    chain = visuals.SceneImagesBackground(["a.png", "b.png", "c.png"]).filter(2, 9.8, timeline)
    assert "[2:v:0]" in chain and "[4:v:0]" in chain  # inputs follow the background's slots
    assert "xfade=transition=fade:duration=0.500:offset=3.050" in chain
    assert "xfade=transition=fade:duration=0.500:offset=6.150" in chain
    frames = [int(n) for n in re.findall(r":d=(\d+):", chain)]
    assert frames == [round((3.05 + 0.5) * 30), round((3.1 + 0.5) * 30), round(3.65 * 30)]
    assert "zoompan=z='1+0.12*(on/" in chain and "zoompan=z='1.12-0.12*(on/" in chain
    assert chain.endswith("[bg]") and visuals.DARKEN in chain
    single = visuals.SceneImagesBackground(["a.png"]).filter(0, 5, [{"start": 0.0}])
    assert "xfade" not in single


def test_a_picture_background_renders_the_exact_length(settings, tmp_path):
    images = []
    for i in range(3):
        path = tmp_path / f"s{i}.png"
        DemoImageProvider(settings).generate(f"scene {i}", i, path)
        images.append(path)
    voice = tmp_path / "v.wav"
    audio.write_wav(voice, audio.silence(6.0))
    timeline = [{"start": 0.0}, {"start": 2.1}, {"start": 4.2}]
    plan = StoryRenderPlan(
        visuals.SceneImagesBackground(images), voice, 6.0, [], timeline, None, None, False
    )
    render_story(FFMPEG, plan, tmp_path / "out.mp4")
    duration, info = probe(tmp_path / "out.mp4")
    assert duration == pytest.approx(6.0 + OUTRO, abs=0.05) and "1080x1920" in info
    count = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(tmp_path / "out.mp4"), "-map", "0:v", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    ).stderr
    frames = int(re.findall(r"frame=\s*(\d+)", count)[-1])
    assert abs(frames - round((6.0 + OUTRO) * 30)) <= 1  # the encoder cuts at the last frame


# ---------- provider adapters ----------


def openai_provider(handler, **overrides):
    values = {
        "openai_api_key": "sk-test",
        "openai_image_model": "gpt-image-2",
        "openai_image_size": "1024x1536",
        "openai_image_quality": "medium",
        "openai_cost_per_image": 0.05,
    }
    settings = SimpleNamespace(**{**values, **overrides})
    return OpenAIImageProvider(settings, httpx.Client(transport=httpx.MockTransport(handler)))


def test_openai_request_shape_and_cost(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(b"png!").decode()}]}
        )

    provider = openai_provider(handler)
    provider.generate("A lighthouse.", 5, tmp_path / "out.png")
    request = seen[0]
    assert request.url.path == "/v1/images/generations"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-image-2",
        "prompt": "A lighthouse.",
        "n": 1,
        "size": "1024x1536",
        "quality": "medium",
    }
    assert (tmp_path / "out.png").read_bytes() == b"png!"
    assert provider.size == (1024, 1536) and provider.cost_per_image == 0.05


@pytest.mark.parametrize(
    "status,error,match",
    [
        (401, ImageUnavailable, "API key"),
        (429, ImageUnavailable, "busy"),
        (500, ImageUnavailable, "busy"),
        (400, ImageFailed, "Your request was rejected by the safety system"),
    ],
)
def test_openai_errors(tmp_path, status, error, match):
    body = {"error": {"message": "Your request was rejected by the safety system."}}
    provider = openai_provider(lambda request: httpx.Response(status, json=body))
    with pytest.raises(error, match=match):
        provider.generate("x", 1, tmp_path / "out.png")


def test_openai_and_flux_explain_why_they_are_unavailable(monkeypatch):
    assert (
        "OPENAI_API_KEY" in openai_provider(lambda r: None, openai_api_key="").unavailable_reason()
    )
    settings = SimpleNamespace(flux_model="m", flux_size="577x1030")
    flux = FluxProvider(settings)
    assert flux.size == (576, 1024)  # rounded down to FLUX's 16 px blocks
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert "Apple-silicon" in flux.unavailable_reason()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.setattr("app.story.images.flux.has_mflux", lambda: False)
    assert "uv sync --extra images" in flux.unavailable_reason()


# ---------- AI pictures through the API and worker ----------


@pytest.fixture
def ai_studio(app, settings, database, clock):
    story_settings = replace(
        settings, story_tts_provider="demo", story_image_provider="demo", whisper_align=""
    )
    voices = {"demo": CountingDemo()}
    images = {
        "demo": CountingImages(story_settings, cost=0.02),
        "openai": OpenAIImageProvider(replace(story_settings, openai_api_key="")),
    }
    llm = FakeLLM()
    llm.usage = Usage()
    service = app.state.story
    service.settings, service.providers, service.image_providers = story_settings, voices, images
    service.model_factory = lambda: llm
    worker = StoryWorker(database, story_settings, clock, providers=voices, image_providers=images)
    return llm, worker, images


def voiced(client, headers, ai_studio):
    llm, worker, _images = ai_studio
    llm.answers.append(sized(70))
    story = client.post("/v1/stories", headers=headers, json={**TOPIC, "targetSeconds": 30}).json()
    body = {"revision": story["revision"], "provider": "demo", "voice": "low", "speed": 1.0}
    client.post(f"/v1/stories/{story['id']}/voice", headers=headers, json=body)
    worker.run_once()
    return client.get(f"/v1/stories/{story['id']}", headers=headers).json()


def render_ai(client, headers, story, **body):
    payload = {
        "revision": story["revision"],
        "background": {"kind": "ai", "id": "demo"},
        "captionPreset": "clean-bold",
        **body,
    }
    return client.post(f"/v1/stories/{story['id']}/render", headers=headers, json=payload)


def test_ai_pictures_render_one_image_per_scene_with_thumbnails(client, headers, ai_studio):
    _llm, worker, images = ai_studio
    assets = client.get("/v1/story-assets", headers=headers).json()["backgrounds"]
    ai = [a for a in assets if a["kind"] == "ai"]
    assert [a["id"] for a in ai] == ["demo"]  # OpenAI has no key here; demo is the default
    story = voiced(client, headers, ai_studio)
    queued = render_ai(client, headers, story)
    assert queued.status_code == 200, queued.text
    assert worker.run_once()
    done = check(
        client.get(f"/v1/stories/{story['id']}", headers=headers), "/v1/stories/{id}", "get"
    )
    assert done["status"] == "rendered"
    scenes = done["script"]["scenes"]
    assert [s["id"] for s in done["images"]["scenes"]] == [s["id"] for s in scenes]
    assert done["images"]["scenes"][0]["prompt"].startswith(
        scenes[0]["visual_description"].rstrip(".")
    )
    assert len(images["demo"].drawn) == len(scenes)
    assert done["usage"]["images"]["drawn"] == len(scenes)
    assert done["usage"]["images"]["costUsd"] == pytest.approx(0.02 * len(scenes))
    assert done["renderSettings"]["background"]["kind"] == "ai"
    links = check(
        client.post(f"/v1/stories/{story['id']}/images/links", headers=headers),
        "/v1/stories/{id}/images/links",
        "post",
    )["scenes"]
    picture = client.get(links[0]["url"])
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    assert picture.content[:4] == b"\x89PNG"
    assert_error(
        client.get(links[0]["url"].replace("signature=", "signature=0")), 401, "UNAUTHENTICATED"
    )
    forged = links[0]["url"].replace(f"/{scenes[0]['id']}?", f"/{scenes[1]['id']}?")
    assert_error(client.get(forged), 401, "UNAUTHENTICATED")  # signed per scene

    # Re-render: pictures come from the cache; "new pictures" redraws every scene.
    again = render_ai(client, headers, done)
    worker.run_once()
    assert len(images["demo"].drawn) == len(scenes)
    current = client.get(f"/v1/stories/{story['id']}", headers=headers).json()
    assert current["usage"]["images"]["drawn"] == 0 and again.status_code == 200
    render_ai(client, headers, current, newImages=True)
    worker.run_once()
    assert len(images["demo"].drawn) == 2 * len(scenes)
    assert (
        client.get(f"/v1/stories/{story['id']}", headers=headers).json()["images"]["variant"] == 1
    )


def test_an_unconfigured_image_engine_cannot_be_chosen(client, headers, ai_studio):
    story = voiced(client, headers, ai_studio)
    response = render_ai(client, headers, story, background={"kind": "ai", "id": "openai"})
    assert_error(response, 409, "BACKGROUND_UNAVAILABLE")


def test_refused_pictures_fail_readably(client, headers, ai_studio):
    _llm, worker, images = ai_studio

    def refuse(prompt, seed, destination):
        raise ImageFailed("The image engine declined this scene's picture.")

    images["demo"].generate = refuse
    story = voiced(client, headers, ai_studio)
    render_ai(client, headers, story)
    worker.run_once()
    failed = client.get(f"/v1/stories/{story['id']}", headers=headers).json()
    assert failed["status"] == "render_failed"
    assert failed["renderJob"]["error"] == {
        "code": "IMAGE_FAILED",
        "message": "The image engine declined this scene's picture.",
    }


def test_an_unavailable_image_engine_is_retried_then_fails(client, headers, ai_studio, clock):
    _llm, worker, images = ai_studio
    calls = []

    def down(prompt, seed, destination):
        calls.append(1)
        raise ImageUnavailable("FLUX could not draw this scene: out of memory")

    images["demo"].generate = down
    story = voiced(client, headers, ai_studio)
    render_ai(client, headers, story)
    worker.run_once()
    assert client.get(f"/v1/stories/{story['id']}", headers=headers).json()["status"] == "rendering"
    clock.advance(31)
    worker.run_once()
    clock.advance(61)
    worker.run_once()
    failed = client.get(f"/v1/stories/{story['id']}", headers=headers).json()
    assert len(calls) == 3 and failed["status"] == "render_failed"
    assert failed["renderJob"]["error"]["code"] == "IMAGES_UNAVAILABLE"


def test_render_progress_follows_the_pictures(client, headers, ai_studio, database):
    _llm, worker, images = ai_studio
    seen = []
    original = images["demo"].generate

    def watch(prompt, seed, destination):
        story_id = client.get("/v1/stories", headers=headers).json()["stories"][0]["id"]
        seen.append(
            client.get(f"/v1/stories/{story_id}", headers=headers).json()["renderJob"]["progress"]
        )
        original(prompt, seed, destination)

    images["demo"].generate = watch
    story = voiced(client, headers, ai_studio)
    render_ai(client, headers, story)
    worker.run_once()
    assert seen[0] is None  # nothing drawn yet
    assert 0 < seen[1] and seen[1:] == sorted(seen[1:]) and max(seen[1:]) <= 0.9
