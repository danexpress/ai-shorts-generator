from pathlib import PurePath

from fastapi import APIRouter, Request

from ..auth import StoreDep, UserDep
from ..errors import ApiError
from ..media_limits import MAX_SOURCE_BYTES, MAX_SOURCE_DURATION_SEC, MIN_SOURCE_DURATION_SEC
from ..models import MediaUploadResult, Project, UploadRequest, UploadSession
from ..store import oid

router = APIRouter(prefix="/v1/uploads", tags=["Uploads"])


def validate_media(body: UploadRequest):
    if PurePath(body.fileName).suffix.lower() not in {".mp4", ".mov", ".webm"} or (
        body.mimeType and not body.mimeType.startswith("video/")
    ):
        raise ApiError("UNSUPPORTED_MEDIA", "Upload an MP4, MOV or WebM video.", 415)
    if body.sizeBytes > MAX_SOURCE_BYTES:
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
    if body.durationSec > MAX_SOURCE_DURATION_SEC:
        raise ApiError("SOURCE_TOO_LONG", "Sources can be up to 3 hours.", 422)
    if body.durationSec < MIN_SOURCE_DURATION_SEC:
        raise ApiError("SOURCE_TOO_SHORT", "Sources must have at least 30 seconds of video.", 422)


@router.post("", response_model=UploadSession, operation_id="createUpload")
def create_upload(body: UploadRequest, store: StoreDep, user: UserDep):
    """Validate metadata and create a local upload session."""
    validate_media(body)
    store.quota(user, body.durationSec)
    p = store.new_project(user, body)
    upload_id = oid("upl")
    upload = {
        "uploadId": upload_id,
        "projectId": p.id,
        "userId": user.id,
        "uploadUrl": f"/v1/uploads/{upload_id}/media",
        "expiresAt": store.now() + store.settings.upload_ttl_seconds * 1000,
    }
    store.add_upload(upload)
    return {**upload, "project": store.project_view(p)}


@router.post("/{id}/media", response_model=MediaUploadResult, operation_id="uploadMedia")
async def upload_media(id: str, request: Request, store: StoreDep, user: UserDep):
    """Stream the uploaded source into the configured local media directory."""
    upload = store.upload(id)
    if not upload or upload.userId != user.id:
        raise ApiError("NOT_FOUND", "Upload not found.", 404)
    if upload.expiresAt <= store.now():
        raise ApiError("INVALID_STATE", "The upload link expired. Start again.", 409)
    project = store.owned(user, upload.projectId)
    mime = request.headers.get("content-type", "").split(";", 1)[0].lower()
    suffix = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}.get(mime)
    if not suffix:
        raise ApiError("UNSUPPORTED_MEDIA", "Upload an MP4, MOV or WebM video.", 415)
    declared = request.headers.get("content-length")
    if declared and int(declared) > MAX_SOURCE_BYTES:
        raise ApiError("SOURCE_TOO_LARGE", "Files can be up to 4 GiB.", 413)

    media_dir = store.settings.media_dir / "sources"
    media_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{project.id}{suffix}"
    target = media_dir / stored_name
    temporary = target.with_suffix(target.suffix + ".part")
    received = 0
    try:
        with temporary.open("wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > min(project.sizeBytes, MAX_SOURCE_BYTES):
                    raise ApiError(
                        "SOURCE_TOO_LARGE", "Uploaded bytes exceed the declared file size.", 413
                    )
                output.write(chunk)
        if received != project.sizeBytes:
            raise ApiError(
                "INVALID_INPUT", "Uploaded byte count does not match the selected file.", 422
            )
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    upload.storedName = stored_name
    upload.contentType = mime
    return {"ok": True, "bytes": received}


@router.post("/{id}/complete", response_model=Project, operation_id="completeUpload")
def complete_upload(id: str, store: StoreDep, user: UserDep):
    upload = store.upload(id)
    if not upload or upload.userId != user.id:
        raise ApiError("NOT_FOUND", "Upload not found.", 404)
    p = store.owned(user, upload.projectId)
    if upload.expiresAt <= store.now():
        raise ApiError("INVALID_STATE", "The upload link expired. Start again.", 409)
    if not upload.storedName:
        raise ApiError(
            "INVALID_STATE", "Upload the source video before completing this upload.", 409
        )
    p.sourceReady = True
    return store.project_view(p)
