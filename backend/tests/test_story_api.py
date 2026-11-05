from dataclasses import replace

import pytest
from conftest import assert_error, login, rows
from test_contract import check
from test_highlights import FakeLLM
from test_story_script import sized

from app.db_models import StoryRecord
from app.llm import InvalidOutput, LLMUnavailable, Usage
from app.story import cache

TOPIC = {"topic": "a lighthouse keeper who finds a message in a bottle", "genre": "mystery"}


@pytest.fixture
def llm(app):
    fake = FakeLLM()
    fake.usage = Usage(calls=1, input_tokens=900, output_tokens=300, seconds=4.2, cost=0.0096)
    app.state.story.model_factory = lambda: fake
    return fake


def create(client, headers, **body):
    return client.post("/v1/stories", headers=headers, json={**TOPIC, "targetSeconds": 30, **body})


def test_create_writes_a_script_and_saves_the_project_document(client, headers, llm, database):
    llm.answers.append(sized(70))
    story = check(create(client, headers), "/v1/stories", "post")
    assert story["title"] == "The Second Bottle" and story["status"] == "draft"
    assert story["genre"] == "mystery" and story["targetSeconds"] == 30
    assert story["wordCount"] == 70 and story["wordBudget"] == [65, 75]
    assert story["warnings"] == [] and story["scriptSource"] == "generated"
    assert [s["id"] for s in story["script"]["scenes"]] == [1, 2, 3]
    (record,) = rows(database, StoryRecord)
    document = record["document"]
    assert document["request"]["topic"] == TOPIC["topic"] and document["scriptModel"]
    assert {"voice", "timings", "background", "music", "style"} <= set(document)
    assert document["usage"]["script"] == {
        "seconds": 4.2,
        "inputTokens": 900,
        "outputTokens": 300,
        "cacheReadTokens": 0,
        "costUsd": 0.0096,
        "cached": False,
    }
    fetched = check(
        client.get(f"/v1/stories/{story['id']}", headers=headers), "/v1/stories/{id}", "get"
    )
    assert fetched == story


def test_identical_requests_hit_the_script_cache(client, headers, llm, settings):
    llm.answers.append(sized(70))
    first = create(client, headers).json()
    second = create(client, headers).json()
    assert len(llm.calls) == 1 and second["script"] == first["script"]
    assert len(list((cache.cache_root(settings.media_dir) / "scripts").glob("*.json"))) == 1
    llm.answers.append(sized(72))
    create(client, headers, tone="playful")  # a different request is a different key
    assert len(llm.calls) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"topic": "cats", "story": "A long enough story about a cat and a dog."},
        {"topic": None},
        {"topic": "  "},
        {"topic": "ab"},
        {"topic": None, "story": "too short"},
        {"genre": "romance"},
        {"targetSeconds": 90},
        {"tone": "x" * 81},
        {"surprise": True},
    ],
)
def test_invalid_requests_are_rejected_before_calling_the_model(client, headers, llm, body):
    assert_error(create(client, headers, **body), 422, "INVALID_INPUT")
    assert llm.calls == []


def test_editing_saves_the_script_and_returns_timing_warnings(client, headers, llm):
    llm.answers.append(sized(70))
    story = create(client, headers).json()
    edited = sized(95)
    edited["title"] = "My Title"
    response = client.put(
        f"/v1/stories/{story['id']}/script",
        headers=headers,
        json={"revision": story["revision"], "script": edited},
    )
    updated = check(response, "/v1/stories/{id}/script", "put")
    assert updated["title"] == "My Title" and updated["scriptSource"] == "edited"
    assert updated["revision"] == story["revision"] + 1
    assert any("budget is 65-75" in w for w in updated["warnings"])
    # Saving over a newer revision (another tab) is refused.
    stale = client.put(
        f"/v1/stories/{story['id']}/script",
        headers=headers,
        json={"revision": story["revision"], "script": edited},
    )
    assert_error(stale, 409, "STALE_REVISION")


def test_structurally_invalid_edits_explain_the_problem(client, headers, llm):
    llm.answers.append(sized(70))
    story = create(client, headers).json()
    broken = {**sized(70), "hook": " "}
    response = client.put(
        f"/v1/stories/{story['id']}/script",
        headers=headers,
        json={"revision": story["revision"], "script": broken},
    )
    assert_error(response, 422, "INVALID_SCRIPT")
    assert '"hook"' in response.json()["error"]["message"]


def test_regenerate_writes_a_new_variant_and_shorten_rewrites_the_current_one(
    client, headers, llm, database
):
    llm.answers += [sized(70), {**sized(68), "title": "Second Take"}]
    story = create(client, headers).json()
    again = client.post(
        f"/v1/stories/{story['id']}/script/regenerate",
        headers=headers,
        json={"revision": story["revision"]},
    )
    again = check(again, "/v1/stories/{id}/script/regenerate", "post")
    assert again["title"] == "Second Take" and len(llm.calls) == 2
    assert rows(database, StoryRecord)[0]["document"]["variant"] == 1
    llm.answers.append({**sized(66), "title": "Shorter"})
    shorter = client.post(
        f"/v1/stories/{story['id']}/script/regenerate",
        headers=headers,
        json={"revision": again["revision"], "shorten": True},
    ).json()
    assert shorter["title"] == "Shorter" and "Second Take" in llm.calls[2]["variable"]


def test_model_failures_have_readable_errors(client, headers, llm, database):
    llm.answers.append(LLMUnavailable("Claude API rate limit reached; retry later."))
    response = create(client, headers)
    assert_error(response, 503, "STORY_MODEL_UNAVAILABLE")
    assert "rate limit" in response.json()["error"]["message"]
    llm.answers += [InvalidOutput("bad"), InvalidOutput("bad again")]
    assert_error(create(client, headers), 422, "SCRIPT_FAILED")
    assert rows(database, StoryRecord) == []


def test_demo_model_writes_scripts_without_an_api_key(client, headers, app):
    app.state.story.settings = replace(app.state.settings, story_model="demo")
    story = create(client, headers, targetSeconds=60).json()
    assert story["warnings"] == [] and story["wordBudget"] == [120, 135]


def test_stories_are_private_listed_newest_first_and_deletable(client, headers, llm, clock):
    llm.answers += [sized(70), {**sized(70), "title": "Newer"}]
    older = create(client, headers).json()
    clock.advance(5)
    newer = create(client, headers, tone="dry").json()
    listed = check(client.get("/v1/stories", headers=headers), "/v1/stories", "get")
    assert [s["id"] for s in listed["stories"]] == [newer["id"], older["id"]]
    other = login(client, "jon@example.com")
    assert_error(client.get(f"/v1/stories/{older['id']}", headers=other), 404, "NOT_FOUND")
    assert client.get("/v1/stories", headers=other).json() == {"stories": []}
    assert_error(client.delete(f"/v1/stories/{older['id']}", headers=other), 404, "NOT_FOUND")
    deleted = client.delete(f"/v1/stories/{older['id']}", headers=headers)
    assert deleted.json() == {"ok": True}
    assert_error(client.get(f"/v1/stories/{older['id']}", headers=headers), 404, "NOT_FOUND")


def test_genres_are_listed_for_the_story_tab(client, headers):
    genres = check(client.get("/v1/story-genres", headers=headers), "/v1/story-genres", "get")
    assert [g["id"] for g in genres["genres"]][:2] == ["mystery", "horror"]
    assert genres["lengths"] == [30, 45, 60]


def test_script_generation_is_rate_limited(client, headers, app):
    app.state.story.settings = replace(app.state.settings, story_model="demo")
    for tone in range(6):
        assert create(client, headers, tone=str(tone)).status_code == 200
    assert_error(create(client, headers, tone="7"), 429, "SOCIAL_RATE_LIMITED")


def test_deleting_the_account_deletes_its_stories(client, headers, app, database):
    app.state.story.settings = replace(app.state.settings, story_model="demo")
    create(client, headers)
    response = client.request(
        "DELETE", "/v1/me", headers=headers, json={"confirmEmail": "maya@example.com"}
    )
    assert response.status_code == 200, response.text
    assert rows(database, StoryRecord) == []
