"""Authenticated playback links and range-capable local media streaming."""

import hashlib
import hmac
import re

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..auth import StoreDep, UserDep
from ..db_models import ProjectRecord, RenderRecord, UploadRecord
from ..errors import ApiError
from ..models import PlaybackLink

router = APIRouter(tags=["Media"])
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def media_signature(project_id: str, expires: int, key: str) -> str:
    message = f"{project_id}.{expires}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


@router.post(
    "/v1/projects/{id}/playback",
    response_model=PlaybackLink,
    operation_id="getProjectPlayback",
)
def playback_link(id: str, store: StoreDep, user: UserDep):
    project = store.owned(user, id)
    upload = store.db.scalar(select(UploadRecord).where(UploadRecord.projectId == project.id))
    if not upload or not upload.storedName or project.sourceExpired:
        raise ApiError("NOT_FOUND", "This project has no playable source video.", 404)
    expires = store.now() // 1000 + 30 * 60
    signature = media_signature(project.id, expires, store.settings.media_signing_key)
    return {
        "url": f"/v1/media/{project.id}?expires={expires}&signature={signature}",
        "expiresAt": expires * 1000,
    }


@router.get("/v1/media/{project_id}", operation_id="streamProjectMedia")
def stream_media(project_id: str, request: Request, expires: int, signature: str):
    settings = request.app.state.settings
    now = int(request.app.state.clock())
    expected = media_signature(project_id, expires, settings.media_signing_key)
    if expires <= now or not hmac.compare_digest(signature, expected):
        raise ApiError("UNAUTHENTICATED", "This playback link is invalid or expired.", 401)
    database = request.app.state.database
    with database.sessions() as session:
        project = session.get(ProjectRecord, project_id)
        upload = session.scalar(select(UploadRecord).where(UploadRecord.projectId == project_id))
        if (
            not project
            or project.status == "deleted"
            or project.sourceExpired
            or (project.sourceExpiresAt is not None and now * 1000 >= project.sourceExpiresAt)
        ):
            raise ApiError("NOT_FOUND", "Source video not found.", 404)
        if not upload or not upload.storedName:
            raise ApiError("NOT_FOUND", "Source video not found.", 404)
        media_root = (settings.media_dir / "sources").resolve()
        path = (media_root / upload.storedName).resolve()
        if path.parent != media_root:
            raise ApiError("NOT_FOUND", "Source video not found.", 404)
        content_type = upload.contentType or "application/octet-stream"
    if not path.is_file():
        raise ApiError("NOT_FOUND", "Source video not found.", 404)

    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    range_header = request.headers.get("range")
    if range_header:
        match = RANGE_RE.fullmatch(range_header.strip())
        if not match or size == 0:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        first, last = match.groups()
        if not first:
            count = int(last or 0)
            start = max(0, size - count)
        else:
            start = int(first)
            end = min(size - 1, int(last)) if last else size - 1
        if start >= size or start > end:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        status = 206

    length = end - start + 1

    def chunks():
        with path.open("rb") as media_file:
            media_file.seek(start)
            remaining = length
            while remaining:
                chunk = media_file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, no-store",
        "Content-Disposition": "inline",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type=content_type, headers=headers)


@router.get("/v1/rendered/{render_id}", operation_id="streamRenderedClip")
def stream_rendered(
    render_id: str, request: Request, expires: int, signature: str, download: bool = False
):
    settings = request.app.state.settings
    now = int(request.app.state.clock())
    expected = media_signature(render_id, expires, settings.media_signing_key)
    if expires <= now or not hmac.compare_digest(signature, expected):
        raise ApiError("UNAUTHENTICATED", "This download link is invalid or expired.", 401)
    with request.app.state.database.sessions() as session:
        record = session.get(RenderRecord, render_id)
        project = session.get(ProjectRecord, record.projectId) if record else None
        if (
            not record
            or not project
            or project.status == "deleted"
            or now * 1000 >= record.expiresAt
        ):
            raise ApiError("NOT_FOUND", "Rendered video not found or expired.", 404)
        media_root = (settings.media_dir / "renders").resolve()
        path = (media_root / record.storedName).resolve()
        if path.parent != media_root:
            raise ApiError("NOT_FOUND", "Rendered video not found.", 404)
        filename = record.fileName
    if not path.is_file():
        raise ApiError("NOT_FOUND", "Rendered video not found.", 404)
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    range_header = request.headers.get("range")
    if range_header:
        match = RANGE_RE.fullmatch(range_header.strip())
        if not match or size == 0:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        first, last = match.groups()
        start = max(0, size - int(last or 0)) if not first else int(first)
        if first:
            end = min(size - 1, int(last)) if last else size - 1
        if start >= size or start > end:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        status = 206
    length = end - start + 1

    def chunks():
        with path.open("rb") as video:
            video.seek(start)
            remaining = length
            while remaining:
                chunk = video.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    disposition = "attachment" if download else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'{disposition}; filename="{filename}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type="video/mp4", headers=headers)
