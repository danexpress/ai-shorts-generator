"""Story Mode: write and edit narration scripts, then generate the voiceover."""

import hmac

from fastapi import APIRouter, Request

from ..errors import ApiError
from ..models import Ok, PlaybackLink
from ..social.security import SocialUser
from ..story.models import (
    Story,
    StoryAssets,
    StoryCreateRequest,
    StoryGenres,
    StoryList,
    StoryRegenerateRequest,
    StoryRenderList,
    StoryRenderRequest,
    StoryScriptUpdate,
    StoryVoiceRequest,
    StoryVoices,
)
from ..story.script import LENGTHS
from .media import media_signature, stream_file

router = APIRouter(prefix="/v1", tags=["Story"])


def service(request: Request):
    return request.app.state.story


@router.get("/story-genres", response_model=StoryGenres, operation_id="listStoryGenres")
def genres(request: Request, user: SocialUser):
    return {"genres": service(request).genres(), "lengths": list(LENGTHS)}


@router.post("/stories", response_model=Story, operation_id="createStory")
def create(body: StoryCreateRequest, request: Request, user: SocialUser):
    return service(request).create(user, body.model_dump())


@router.get("/stories", response_model=StoryList, operation_id="listStories")
def list_stories(request: Request, user: SocialUser):
    return {"stories": service(request).list(user)}


@router.get("/stories/{id}", response_model=Story, operation_id="getStory")
def get_story(id: str, request: Request, user: SocialUser):
    return service(request).get(user, id)


@router.put("/stories/{id}/script", response_model=Story, operation_id="updateStoryScript")
def update_script(id: str, body: StoryScriptUpdate, request: Request, user: SocialUser):
    return service(request).edit(user, id, body.revision, body.script.model_dump())


@router.post(
    "/stories/{id}/script/regenerate", response_model=Story, operation_id="regenerateStoryScript"
)
def regenerate(id: str, body: StoryRegenerateRequest, request: Request, user: SocialUser):
    return service(request).regenerate(user, id, body.revision, body.shorten)


@router.delete("/stories/{id}", response_model=Ok, operation_id="deleteStory")
def delete(id: str, request: Request, user: SocialUser):
    service(request).delete(user, id)
    return {"ok": True}


@router.get("/story-voices", response_model=StoryVoices, operation_id="listStoryVoices")
def voices(request: Request, user: SocialUser):
    return service(request).voices()


@router.post("/stories/{id}/voice", response_model=Story, operation_id="requestStoryVoice")
def request_voice(id: str, body: StoryVoiceRequest, request: Request, user: SocialUser):
    """Queue voice generation; poll GET /v1/stories/{id} until status is "voiced"."""
    return service(request).request_voice(user, id, body.model_dump())


@router.post(
    "/stories/{id}/voice/playback",
    response_model=PlaybackLink,
    operation_id="getStoryVoicePlayback",
)
def voice_playback(id: str, request: Request, user: SocialUser):
    return service(request).playback(user, id)


@router.get("/story-audio/{story_id}", operation_id="streamStoryAudio")
def stream_voice(story_id: str, request: Request, expires: int, signature: str):
    settings = request.app.state.settings
    expected = media_signature(f"story:{story_id}", expires, settings.media_signing_key)
    if expires <= int(request.app.state.clock()) or not hmac.compare_digest(signature, expected):
        raise ApiError("UNAUTHENTICATED", "This playback link is invalid or expired.", 401)
    return stream_file(request, service(request).voice_file(story_id), "audio/wav")


@router.get("/story-assets", response_model=StoryAssets, operation_id="listStoryAssets")
def assets(request: Request, user: SocialUser):
    """Backgrounds (bundled loops, your folder, your uploads), music and caption styles."""
    return service(request).assets(user)


@router.post("/stories/{id}/render", response_model=Story, operation_id="renderStory")
def render(id: str, body: StoryRenderRequest, request: Request, user: SocialUser):
    """Queue the final render; poll GET /v1/stories/{id} until status is "rendered"."""
    return service(request).request_render(user, id, body.model_dump())


@router.get(
    "/stories/{id}/renders", response_model=StoryRenderList, operation_id="listStoryRenders"
)
def renders(id: str, request: Request, user: SocialUser):
    return {"renders": service(request).renders(user, id)}
