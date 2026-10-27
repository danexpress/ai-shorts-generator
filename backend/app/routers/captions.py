"""Caption presets and per-clip editable caption tracks."""

from fastapi import APIRouter

from ..auth import StoreDep, UserDep
from ..captions.presets import PRESETS
from ..captions.service import current_clip, save_track, track_view
from ..errors import ApiError
from ..models import CaptionPresetInfo, CaptionTrack, CaptionTrackEdit

router = APIRouter(prefix="/v1", tags=["Captions"])


@router.get(
    "/caption-presets", response_model=list[CaptionPresetInfo], operation_id="listCaptionPresets"
)
def presets(user: UserDep):
    return [preset.public() for preset in PRESETS.values()]


@router.get(
    "/projects/{id}/clips/{clipId}/captions",
    response_model=CaptionTrack,
    operation_id="getCaptions",
)
def get_captions(id: str, clipId: str, store: StoreDep, user: UserDep):
    project = store.owned(user, id)
    return track_view(store, project, current_clip(store, project, clipId))


@router.patch(
    "/projects/{id}/clips/{clipId}/captions",
    response_model=CaptionTrack,
    operation_id="saveCaptions",
)
def save_captions(id: str, clipId: str, body: CaptionTrackEdit, store: StoreDep, user: UserDep):
    project = store.owned(user, id)
    if store.project_job(id, active=True):
        raise ApiError("ACTIVE_JOB_EXISTS", "Wait for processing before editing captions.", 409)
    return save_track(store, project, current_clip(store, project, clipId), body)
