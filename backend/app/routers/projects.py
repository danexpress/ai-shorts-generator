import re
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Header

from ..auth import StoreDep, UserDep
from ..errors import ApiError
from ..models import Deleted, Guidance, Project, RetryRequest, Status, UploadRequest, YoutubeRequest

router = APIRouter(prefix="/v1/projects", tags=["Projects"])
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.post("/youtube", response_model=Project, operation_id="createYoutubeProject")
def youtube(body: YoutubeRequest, store: StoreDep, user: UserDep):
    """Feature-flagged demo import. Never downloads a supplied URL."""
    if not store.settings.youtube_import:
        raise ApiError(
            "IMPORT_UNAVAILABLE", "URL import is unavailable. Upload a file instead.", 503
        )
    try:
        parsed = urlparse(body.url)
        video_id = None
        if (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        ):
            if (
                parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}
                and parsed.path == "/watch"
            ):
                video_id = parse_qs(parsed.query).get("v", [None])[0]
            elif parsed.hostname == "youtu.be":
                video_id = parsed.path[1:]
        if not video_id or not re.fullmatch(r"[\w-]{6,15}", video_id, flags=re.ASCII):
            raise ValueError("unsupported")
    except ValueError:
        raise ApiError("UNSUPPORTED_URL", "Use a supported public HTTPS YouTube video link.", 422)
    if not body.rightsConfirmed:
        raise ApiError("RIGHTS_NOT_CONFIRMED", "Confirm your rights to reuse this video.", 422)
    store.quota(user, 1934)
    p = store.new_project(
        user,
        UploadRequest(
            fileName="youtube.mp4",
            title=f"YouTube · {video_id}",
            mimeType="video/mp4",
            sizeBytes=0,
            durationSec=1934,
            width=1920,
            height=1080,
            fps=30,
            hasVideo=True,
            hasAudio=True,
        ),
        source_type="youtube_url",
    )
    p.sourceReady = True
    return store.project_view(p)


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
