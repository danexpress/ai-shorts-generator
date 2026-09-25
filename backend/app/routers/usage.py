from fastapi import APIRouter

from ..auth import StoreDep, UserDep
from ..models import Usage

router = APIRouter(tags=["Usage"])


@router.get("/v1/usage", response_model=Usage, operation_id="getUsage")
def usage(store: StoreDep, user: UserDep):
    return {**store.usage(user), "entries": store.usage_entries(user)}
