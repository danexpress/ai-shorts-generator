from fastapi import APIRouter

from ..auth import StoreDep, UserDep
from ..errors import ApiError
from ..models import Clips, Guidance, Status
from .projects import IdempotencyKey

router = APIRouter(prefix="/v1/projects", tags=["Analysis"])


@router.get(
    "/{id}/clips", response_model=Clips, response_model_exclude_unset=True, operation_id="getClips"
)
def clips(id: str, store: StoreDep, user: UserDep):
    p = store.owned(user, id)
    if p.status == "expired":
        raise ApiError("ASSET_EXPIRED", "These suggestions have expired.", 410)
    if not p.currentRunId:
        return {"runId": None, "goal": None, "stale": False, "clips": []}
    return {**store.run(p.currentRunId), "stale": p.clipsStale}


@router.post("/{id}/analysis-runs", response_model=Status, operation_id="createAnalysisRun")
def analyze(
    id: str, body: Guidance, store: StoreDep, user: UserDep, idempotency_key: IdempotencyKey = None
):
    return store.start(user, store.owned(user, id), "analysis", body, idempotency_key)
