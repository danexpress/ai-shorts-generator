import json
from dataclasses import replace

import httpx
import pytest
from conftest import assert_error, rows
from pydantic import ValidationError

from app.social.config import SocialSettings
from app.social.copy import SocialCopyGenerator, StructuredCopyModel
from app.social.db_models import RenderContext, SocialCopy
from app.social.models import TikTokCopy, XCopy

COPY = {
    "youtube": {
        "title": "Hire for the future",
        "description": "The speaker explains how to plan your next hire.",
        "hashtags": ["#Hiring", "#Shorts"],
    },
    "tiktok": {
        "caption": "Hiring for today can leave you behind tomorrow.",
        "hashtags": ["#hiring"],
    },
    "x": {"text": "Plan your next hire for the work ahead, not just the team you have today."},
}


class FakeModel:
    def __init__(self):
        self.calls = []
        self.bad = False

    def generate(self, context, schema):
        self.calls.append(context)
        if self.bad:
            return {"wrong": "output"}
        return (
            COPY
            if schema.get("title") == "SocialCopyBundle"
            else COPY[
                {"YouTubeCopy": "youtube", "TikTokCopy": "tiktok", "XCopy": "x"}[schema["title"]]
            ]
        )


def test_copy_validates_platform_schemas_and_empty_transcript():
    model = FakeModel()
    generator = SocialCopyGenerator(model)
    assert generator.generate_all({"clip_transcript": "Hire for the future"}) == COPY
    for method in [generator.generate_youtube, generator.generate_tiktok, generator.generate_x]:
        assert method({"clip_transcript": "Hire for the future"})
    with pytest.raises(Exception, match="no transcript"):
        generator.generate_all({"clip_transcript": ""})
    model.bad = True
    with pytest.raises(Exception, match="invalid"):
        generator.generate_all({"clip_transcript": "Source"})


def test_caption_length_and_hashtag_validation():
    with pytest.raises(ValidationError):
        TikTokCopy(caption="👀" * 1100, hashtags=["#career"])
    with pytest.raises(ValidationError):
        XCopy(text="漢" * 141)
    with pytest.raises(ValidationError):
        XCopy(text="x" * 281)
    model = FakeModel()
    original = model.generate
    model.generate = lambda context, schema: {
        **COPY,
        "tiktok": {"caption": "Valid", "hashtags": ["no-hash"]},
    }
    with pytest.raises(Exception, match="invalid"):
        SocialCopyGenerator(model).generate_all({"clip_transcript": "Source"})
    model.generate = original


def test_model_uses_strict_schema_and_refuses_malformed_response():
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(COPY)}}]},
        )

    settings = replace(
        SocialSettings(),
        copy_url="https://api.openai.com/v1/chat/completions",
        copy_key="private-key",
        copy_model="configured-model",
    )
    generator = SocialCopyGenerator(StructuredCopyModel(settings, httpx.MockTransport(transport)))
    assert generator.generate_all({"clip_transcript": "Source"}) == COPY
    assert calls[0]["store"] is False
    assert calls[0]["response_format"]["json_schema"]["strict"]
    invalid = SocialCopyGenerator(
        StructuredCopyModel(
            settings,
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={
                        "choices": [{"finish_reason": "length", "message": {"content": '{"bad"'}}]
                    },
                )
            ),
        )
    )
    with pytest.raises(Exception, match="could not be generated"):
        invalid.generate_all({"clip_transcript": "Source"})


def test_copy_caches_and_individual_regeneration_preserves_other_platforms(
    client, app, headers, render, database
):
    model = FakeModel()
    app.state.social_copy.generator = SocialCopyGenerator(model)
    url = f"/v1/renders/{render}/social-copy"
    first = client.post(url, headers=headers, json={"transcript_confirmed": True})
    assert first.status_code == 200, first.text
    assert first.json()["copy"] == COPY
    assert len(model.calls) == 1
    assert client.post(url, headers=headers, json={"transcript_confirmed": True}).status_code == 200
    assert len(model.calls) == 1
    assert (
        client.post(
            url + "/regenerate",
            headers=headers,
            json={"transcript_confirmed": True, "platforms": ["youtube"]},
        ).status_code
        == 200
    )
    assert len(model.calls) == 2
    assert len(rows(database, SocialCopy)) == 3
    assert rows(database, RenderContext)[0]["context"]["clip_transcript"]


def test_copy_permissions_and_malformed_model_do_not_overwrite_cache(
    client, app, headers, render, database
):
    from conftest import login

    model = FakeModel()
    app.state.social_copy.generator = SocialCopyGenerator(model)
    url = f"/v1/renders/{render}/social-copy"
    assert_error(
        client.post(
            url, headers=login(client, "jon@example.com"), json={"transcript_confirmed": True}
        ),
        404,
        "NOT_FOUND",
    )
    assert client.post(url, headers=headers, json={"transcript_confirmed": True}).status_code == 200
    model.bad = True
    assert_error(
        client.post(url + "/regenerate", headers=headers, json={"transcript_confirmed": True}),
        502,
        "COPY_GENERATION_FAILED",
    )
    assert client.get(url, headers=headers).json()["copy"] == COPY


def test_edited_copy_is_saved_and_validated(client, headers, render):
    url = f"/v1/renders/{render}/social-copy"
    edited = {**COPY["youtube"], "title": "My own title"}
    result = client.patch(url, headers=headers, json={"copy": {"youtube": edited}})
    assert result.status_code == 200, result.text
    assert client.get(url, headers=headers).json()["copy"]["youtube"]["title"] == "My own title"
    assert_error(
        client.patch(url, headers=headers, json={"copy": {"x": {"text": "漢" * 141}}}),
        422,
        "INVALID_INPUT",
    )


def test_demo_transcript_requires_creator_review(client, app, headers, render):
    model = FakeModel()
    app.state.social_copy.generator = SocialCopyGenerator(model)
    url = f"/v1/renders/{render}/social-copy"
    view = client.get(url, headers=headers).json()
    assert view["source_transcript"] and not view["transcript_verified"]
    assert_error(client.post(url, headers=headers, json={}), 409, "TRANSCRIPT_REVIEW_REQUIRED")
    assert not model.calls
    assert client.post(url, headers=headers, json={"transcript_confirmed": True}).status_code == 200
    assert client.get(url, headers=headers).json()["transcript_verified"]
    assert (
        client.post(url + "/regenerate", headers=headers, json={"platforms": ["x"]}).status_code
        == 200
    )
