"""Story Mode: write and edit narration scripts (voice and render follow)."""

from fastapi import APIRouter, Request

from ..models import Ok
from ..social.security import SocialUser
from ..story.models import (
    Story,
    StoryCreateRequest,
    StoryGenres,
    StoryList,
    StoryRegenerateRequest,
    StoryScriptUpdate,
)
from ..story.script import LENGTHS

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
