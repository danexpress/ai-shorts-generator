import copy
import json
import re
from urllib.parse import quote

from fastapi import APIRouter

from ..auth import StoreDep, UserDep
from ..errors import ApiError
from ..models import Download, Transcript, TranscriptEdit

router = APIRouter(prefix="/v1/projects", tags=["Transcripts"])


@router.get("/{id}/transcript", response_model=Transcript, operation_id="getTranscript")
def get_transcript(id: str, store: StoreDep, user: UserDep):
    return store.transcript(store.owned(user, id))


@router.patch("/{id}/transcript", response_model=Transcript, operation_id="saveTranscript")
def save_transcript(id: str, body: TranscriptEdit, store: StoreDep, user: UserDep):
    p = store.owned(user, id)
    transcript = store.transcript(p)
    if store.project_job(id, active=True):
        raise ApiError(
            "ACTIVE_JOB_EXISTS", "Wait for processing before editing the transcript.", 409
        )
    # Validate every edit before committing any mutations.
    updated = copy.deepcopy(transcript)
    segments = {seg["id"]: seg for seg in updated["segments"]}
    if len({edit.id for edit in body.segments}) != len(body.segments):
        raise ApiError("INVALID_INPUT", "Each segment may appear only once.", 422)
    changed = False
    for edit in body.segments:
        seg = segments.get(edit.id)
        if not seg:
            raise ApiError("INVALID_INPUT", "Unknown transcript segment.", 422)
        if edit.text == seg["text"]:
            continue
        words = edit.text.split()
        if seg["timing"] == "word" and len(words) == len(seg["words"]):
            seg["words"] = [{**word, "w": text} for word, text in zip(seg["words"], words)]
        else:
            # Preserve truthful segment boundaries instead of fabricating word timestamps.
            seg["timing"] = "segment"
            seg["words"] = [{"w": edit.text, "s": seg["start"], "e": seg["end"]}]
        seg["text"] = edit.text
        changed = True
    if changed:
        updated["revision"] += 1
        updated["userCorrected"] = True
        store.save_transcript(id, updated)
        p.transcriptRevision = updated["revision"]
        p.clipsStale = p.currentRunId is not None
    return updated


@router.get("/{id}/downloads", response_model=list[Download], operation_id="getProjectDownloads")
def downloads(id: str, store: StoreDep, user: UserDep):
    p = store.owned(user, id)
    transcript = store.transcript(p)
    plain = "\n\n".join(seg["text"] for seg in transcript["segments"])
    structured = json.dumps(
        {k: transcript[k] for k in ("language", "revision", "segments")},
        ensure_ascii=False,
        indent=2,
    )
    base = re.sub(r"[^a-zA-Z0-9_]+", "-", p.name).strip("-").lower() or "project"
    return [
        {
            "kind": kind,
            "filename": f"{base}-transcript.{kind}",
            "url": f"data:{mime};charset=utf-8,{quote(content, safe='')}",
            "bytes": len(content.encode("utf-8")),
        }
        for kind, mime, content in [
            ("txt", "text/plain", plain),
            ("json", "application/json", structured),
        ]
    ]
