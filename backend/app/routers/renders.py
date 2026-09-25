"""Authenticated render creation for selected suggestions."""

import re
import uuid

from fastapi import APIRouter
from sqlalchemy import select

from ..auth import StoreDep, UserDep
from ..db_models import RenderRecord, UploadRecord
from ..errors import ApiError
from ..models import RenderedClip, RenderRequest
from ..renderer import RendererUnavailable, RenderFailed, render_short
from .media import media_signature

router = APIRouter(prefix="/v1/projects", tags=["Renders"])


@router.post("/{id}/render", response_model=RenderedClip, operation_id="renderClip")
def render_project_clip(id: str, body: RenderRequest, store: StoreDep, user: UserDep):
    project = store.owned(user, id)
    if project.status != "ready" or project.clipsStale or not project.currentRunId:
        raise ApiError("INVALID_STATE", "Project suggestions must be ready before rendering.", 409)
    upload = store.db.scalar(select(UploadRecord).where(UploadRecord.projectId == id))
    if not upload or not upload.storedName or project.sourceExpired:
        raise ApiError("NOT_FOUND", "The source video is no longer available to render.", 404)
    source_root = (store.settings.media_dir / "sources").resolve()
    source = (source_root / upload.storedName).resolve()
    if source.parent != source_root or not source.is_file():
        raise ApiError("NOT_FOUND", "The source video is no longer available to render.", 404)
    clips = store.run(project.currentRunId)["clips"]
    clip = next((item for item in clips if item["id"] == body.clipId), None)
    if not clip:
        raise ApiError("INVALID_INPUT", "Choose a clip from the current suggestions.", 422)

    render_id = "rnd_" + uuid.uuid4().hex
    render_root = (store.settings.media_dir / "renders").resolve()
    render_root.mkdir(parents=True, exist_ok=True)
    stored_name = render_id + ".mp4"
    temporary = render_root / (render_id + ".tmp.mp4")
    destination = render_root / stored_name
    try:
        render_short(
            source,
            temporary,
            clip["start"],
            clip["duration"],
            body.resolution,
            store.settings.ffmpeg_binary,
        )
        temporary.replace(destination)
    except RendererUnavailable:
        temporary.unlink(missing_ok=True)
        raise ApiError(
            "RENDERER_UNAVAILABLE", "Video rendering is not available on this server.", 503
        )
    except RenderFailed:
        temporary.unlink(missing_ok=True)
        raise ApiError("RENDER_FAILED", "The video could not be rendered. Try again.", 422)

    now = store.now()
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", project.name).strip("-").lower() or "short"
    hook = re.sub(r"[^a-zA-Z0-9_-]+", "-", clip["hook"]).strip("-").lower()[:48].strip("-")
    filename = f"{base}-{hook or 'short'}-{body.resolution}p.mp4"
    expires_at = now + store.settings.render_retention_days * 86_400_000
    record = RenderRecord(
        id=render_id,
        projectId=id,
        userId=user.id,
        clipId=body.clipId,
        storedName=stored_name,
        fileName=filename,
        resolution=body.resolution,
        durationSec=clip["duration"],
        sizeBytes=destination.stat().st_size,
        createdAt=now,
        expiresAt=expires_at,
    )
    store.db.add(record)
    project.renderCount += 1
    expires = now // 1000 + 30 * 60
    signature = media_signature(render_id, expires, store.settings.media_signing_key)
    base_url = f"/v1/rendered/{render_id}?expires={expires}&signature={signature}"
    return {
        "id": render_id,
        "projectId": id,
        "clipId": body.clipId,
        "filename": filename,
        "resolution": body.resolution,
        "durationSec": clip["duration"],
        "bytes": destination.stat().st_size,
        "createdAt": now,
        "expiresAt": expires_at,
        "url": base_url,
        "downloadUrl": base_url + "&download=true",
    }
