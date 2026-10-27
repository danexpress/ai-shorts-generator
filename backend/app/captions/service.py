"""Caption tracks: derived from the stored transcript, saved once a creator edits them."""

from ..db_models import CaptionTrackRecord, ProjectRecord
from ..errors import ApiError
from .chunking import CaptionEditError, ChunkOptions, build_chunks, normalize_chunks
from .formats import build_ass, build_srt
from .presets import DEFAULT_PRESET, PRESETS, get_preset


def current_clip(store, project: ProjectRecord, clip_id: str) -> dict:
    if not project.currentRunId:
        raise ApiError("INVALID_STATE", "Project suggestions must be ready first.", 409)
    clip = next((c for c in store.run(project.currentRunId)["clips"] if c["id"] == clip_id), None)
    if not clip:
        raise ApiError("NOT_FOUND", "Choose a clip from the current suggestions.", 404)
    return clip


def derive_chunks(transcript: dict, clip: dict, keep_fillers: bool) -> list[dict]:
    options = ChunkOptions(remove_fillers=not keep_fillers)
    return build_chunks(transcript["segments"], clip["start"], clip["end"], options)


def track_view(store, project: ProjectRecord, clip: dict) -> dict:
    transcript = store.transcript(project)
    saved = store.db.get(CaptionTrackRecord, (project.id, clip["id"]))
    if saved:
        preset, keep_fillers, edited = saved.preset, saved.keepFillers, saved.edited
        chunks, revision = saved.chunks, saved.transcriptRevision
    else:
        preset, keep_fillers, edited = DEFAULT_PRESET, False, False
        chunks = derive_chunks(transcript, clip, keep_fillers)
        revision = transcript["revision"]
    return {
        "projectId": project.id,
        "clipId": clip["id"],
        "clipStart": clip["start"],
        "clipEnd": clip["end"],
        "preset": preset if preset in PRESETS else DEFAULT_PRESET,
        "keepFillers": keep_fillers,
        "edited": edited,
        # The transcript was corrected after these captions were saved.
        "stale": revision != transcript["revision"],
        "transcriptRevision": revision,
        "chunks": chunks,
    }


def save_track(store, project: ProjectRecord, clip: dict, edit) -> dict:
    if edit.preset is not None and edit.preset not in PRESETS:
        raise ApiError("INVALID_INPUT", "Choose one of the available caption styles.", 422)
    view = track_view(store, project, clip)
    transcript = store.transcript(project)
    preset = edit.preset or view["preset"]
    keep_fillers = view["keepFillers"] if edit.keepFillers is None else edit.keepFillers
    if edit.chunks is not None:
        try:
            chunks = normalize_chunks(
                [c.model_dump() for c in edit.chunks],
                clip["start"],
                clip["end"],
                ChunkOptions().min_duration,
            )
        except CaptionEditError as exc:
            raise ApiError("INVALID_INPUT", str(exc), 422)
        edited, revision = True, view["transcriptRevision"]
    elif edit.reset or keep_fillers != view["keepFillers"]:
        if view["edited"] and not edit.reset:
            raise ApiError(
                "CAPTIONS_EDITED",
                "Changing filler words rebuilds the captions. Reset them to continue.",
                409,
            )
        chunks = derive_chunks(transcript, clip, keep_fillers)
        edited, revision = False, transcript["revision"]
    else:
        chunks, edited, revision = view["chunks"], view["edited"], view["transcriptRevision"]
    record = store.db.get(CaptionTrackRecord, (project.id, clip["id"]))
    if record is None:
        record = CaptionTrackRecord(projectId=project.id, clipId=clip["id"])
        store.db.add(record)
    record.update(
        preset=preset,
        keepFillers=keep_fillers,
        edited=edited,
        transcriptRevision=revision,
        chunks=chunks,
        updatedAt=store.now(),
    )
    store.db.flush()
    return track_view(store, project, clip)


def render_captions(view: dict, preset_id: str | None = None) -> tuple[str, dict] | None:
    """ASS text and a snapshot to store with the render, or None without speech."""
    if not view["chunks"]:
        return None
    preset = get_preset(preset_id or view["preset"])
    snapshot = {"preset": preset.id, "clipStart": view["clipStart"], "chunks": view["chunks"]}
    return build_ass(view["chunks"], preset, view["clipStart"]), snapshot


def exports(snapshot: dict) -> dict[str, tuple[str, str]]:
    """{kind: (mime type, content)} for a render's caption snapshot."""
    preset = get_preset(snapshot["preset"])
    return {
        "srt": ("application/x-subrip", build_srt(snapshot["chunks"], snapshot["clipStart"])),
        "ass": ("text/x-ssa", build_ass(snapshot["chunks"], preset, snapshot["clipStart"])),
    }
