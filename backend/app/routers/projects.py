import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header

from ..auth import StoreDep, UserDep
from ..errors import ApiError
from ..models import Deleted, Guidance, Project, RetryRequest, Status, UploadRequest, YoutubeRequest
from ..store import oid
from ..youtube import (
    InvalidVideoUrl,
    VideoDownloadError,
    VideoLimitError,
    download_youtube,
    normalize_youtube_url,
)
from .uploads import validate_media

router = APIRouter(prefix="/v1/projects", tags=["Projects"])
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.post("/youtube", response_model=Project, operation_id="createYoutubeProject")
def youtube(body: YoutubeRequest, store: StoreDep, user: UserDep):
    """Download a rights-confirmed YouTube source into private local media storage."""
    if not store.settings.youtube_import:
        raise ApiError(
            "IMPORT_UNAVAILABLE", "URL import is unavailable. Upload a file instead.", 503
        )
    try:
        video_id, canonical_url = normalize_youtube_url(body.url)
    except InvalidVideoUrl:
        raise ApiError("UNSUPPORTED_URL", "Use a supported public HTTPS YouTube video link.", 422)
    if not body.rightsConfirmed:
        raise ApiError("RIGHTS_NOT_CONFIRMED", "Confirm your rights to reuse this video.", 422)
    media_root = (store.settings.media_dir / "sources").resolve()
    try:
        result = download_youtube(
            canonical_url,
            media_root / ".downloads",
            store.settings.ffmpeg_binary,
            before_download=lambda duration: store.quota(user, duration),
        )
    except VideoLimitError as exc:
        raise ApiError(exc.code, exc.message, 413 if exc.code == "SOURCE_TOO_LARGE" else 422)
    except VideoDownloadError:
        raise ApiError(
            "DOWNLOAD_FAILED",
            "The YouTube video could not be downloaded. Try uploading the file instead.",
            422,
        )
    suffix = Path(result["fileName"]).suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm"}:
        shutil.rmtree(result["path"].parent, ignore_errors=True)
        raise ApiError(
            "UNSUPPORTED_MEDIA",
            "YouTube returned an unsupported video format. Upload an MP4, MOV or WebM file.",
            415,
        )
    source = UploadRequest(
        fileName=result["fileName"],
        title=result["title"],
        mimeType=result["mimeType"],
        sizeBytes=result["sizeBytes"],
        durationSec=result["durationSec"],
        width=result["width"],
        height=result["height"],
        fps=result["fps"],
        hasVideo=True,
        hasAudio=True,
    )
    target = None
    try:
        validate_media(source)
        store.quota(user, source.durationSec)
        project = store.new_project(user, source, source_type="youtube_url")
        stored_name = f"{project.id}{suffix}"
        target = media_root / stored_name
        media_root.mkdir(parents=True, exist_ok=True)
        result["path"].replace(target)
        shutil.rmtree(result["path"].parent, ignore_errors=True)
        project.sourceReady = True
        project.sourceExpiresAt = store.now() + store.settings.source_retention_hours * 3_600_000
        store.add_upload(
            {
                "uploadId": oid("upl"),
                "projectId": project.id,
                "userId": user.id,
                "uploadUrl": f"youtube://{video_id}",
                "expiresAt": project.sourceExpiresAt,
                "storedName": stored_name,
                "contentType": source.mimeType,
            }
        )
        return store.project_view(project)
    except Exception:
        result["path"].unlink(missing_ok=True)
        if target:
            target.unlink(missing_ok=True)
        shutil.rmtree(result["path"].parent, ignore_errors=True)
        raise


@router.get("", response_model=list[Project], operation_id="listProjects")
def list_projects(store: StoreDep, user: UserDep):
    return store.list_projects(user)


@router.get("/{id}", response_model=Project, operation_id="getProject")
def get_project(id: str, store: StoreDep, user: UserDep):
    return store.project_view(store.owned(user, id))


@router.delete("/{id}", response_model=Deleted, operation_id="deleteProject")
def delete_project(id: str, store: StoreDep, user: UserDep):
    store.delete(store.owned(user, id))
    return Deleted()


@router.post("/{id}/process", response_model=Status, operation_id="processProject")
def process(
    id: str, body: Guidance, store: StoreDep, user: UserDep, idempotency_key: IdempotencyKey = None
):
    return store.start(user, store.owned(user, id), "process", body, idempotency_key)


@router.post("/{id}/retry", response_model=Status, operation_id="retryProject")
def retry(
    id: str,
    store: StoreDep,
    user: UserDep,
    body: RetryRequest | None = None,
    idempotency_key: IdempotencyKey = None,
):
    return store.start(user, store.owned(user, id), "retry", None, idempotency_key)


@router.post("/{id}/cancel", response_model=Status, operation_id="cancelProject")
def cancel(id: str, store: StoreDep, user: UserDep):
    return store.cancel(store.owned(user, id))


@router.get("/{id}/status", response_model=Status, operation_id="getStatus")
def status(id: str, store: StoreDep, user: UserDep):
    return store.status(store.owned(user, id))
