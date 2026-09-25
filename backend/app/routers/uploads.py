from pathlib import PurePath

from fastapi import APIRouter

from ..auth import StoreDep, UserDep
from ..errors import ApiError
from ..models import Project, UploadRequest, UploadSession
from ..store import oid

router = APIRouter(prefix="/v1/uploads", tags=["Uploads"])


def validate_media(body: UploadRequest):
    if PurePath(body.fileName).suffix.lower() not in {".mp4", ".mov", ".webm"} or (
        body.mimeType and not body.mimeType.startswith("video/")
    ):
        raise ApiError("UNSUPPORTED_MEDIA", "Upload an MP4, MOV or WebM video.", 415)
    if body.sizeBytes > 4 * 1024**3:
        raise ApiError("SOURCE_TOO_LARGE", "Files can be up to 4 GiB.", 413)
    if (
        not body.hasVideo
        or not body.hasAudio
        or body.durationSec <= 0
        or min(body.width, body.height, body.fps) <= 0
    ):
        raise ApiError("MEDIA_PROBE_FAILED", "Readable video and audio streams are required.", 422)
    if max(body.width, body.height) > 7680 or body.fps > 120:
        raise ApiError(
            "UNSUPPORTED_MEDIA", "Video dimensions or frame rate exceed the limits.", 415
        )
    if body.durationSec > 3600:
        raise ApiError("SOURCE_TOO_LONG", "Sources can be up to 60 minutes.", 422)
    if body.durationSec < 30:
        raise ApiError("SOURCE_TOO_SHORT", "Sources must have at least 30 seconds of video.", 422)


@router.post("", response_model=UploadSession, operation_id="createUpload")
def create_upload(body: UploadRequest, store: StoreDep, user: UserDep):
    """Demo metadata upload: no video is stored or probed in this demo backend."""
    validate_media(body)
    store.quota(user, body.durationSec)
    p = store.new_project(user, body)
    upload_id = oid("upl")
    upload = {
        "uploadId": upload_id,
        "projectId": p.id,
        "userId": user.id,
        "uploadUrl": f"mock://upload/{oid('obj')}",
        "expiresAt": store.now() + store.settings.upload_ttl_seconds * 1000,
    }
    store.add_upload(upload)
    return {**upload, "project": store.project_view(p)}


@router.post("/{id}/complete", response_model=Project, operation_id="completeUpload")
def complete_upload(id: str, store: StoreDep, user: UserDep):
    upload = store.upload(id)
    if not upload or upload.userId != user.id:
        raise ApiError("NOT_FOUND", "Upload not found.", 404)
    p = store.owned(user, upload.projectId)
    if upload.expiresAt <= store.now():
        raise ApiError("INVALID_STATE", "The upload link expired. Start again.", 409)
    p.sourceReady = True
    return store.project_view(p)
