"""Social endpoints use short transactions and never serialize provider tokens."""

import html
import json
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ..errors import ApiError
from ..social.accounts import rate_limit, safe_error
from ..social.adapters.base import ProviderError
from ..social.copy import owned_render
from ..social.db_models import SocialAccount, SocialCopy
from ..social.models import (
    SocialAccountsView,
    SocialConnectView,
    SocialCopySave,
    SocialCopyView,
    SocialCreatorInfo,
    SocialDisconnected,
    SocialGenerateRequest,
    SocialPlatform,
    SocialPublishBatchView,
    SocialPublishJobView,
    SocialPublishRequest,
)
from ..social.security import SocialUser

router = APIRouter(prefix="/v1", tags=["Social publishing"])


@router.get(
    "/social-accounts", response_model=SocialAccountsView, operation_id="listSocialAccounts"
)
def list_accounts(request: Request, user: SocialUser):
    return request.app.state.social_accounts.list(user)


@router.get(
    "/social-accounts/{platform}/connect",
    response_model=SocialConnectView,
    operation_id="connectSocialAccount",
)
def connect(platform: SocialPlatform, request: Request, response: Response, user: SocialUser):
    service = request.app.state.social_accounts
    url, binding = service.connect(user, platform)
    response.set_cookie(
        f"social_oauth_{platform}",
        binding,
        max_age=600,
        httponly=True,
        secure=urlparse(service.settings.public_url).scheme == "https",
        samesite="lax",
        path=f"/v1/social-accounts/{platform}/callback",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authorization_url": url}


@router.get(
    "/social-accounts/{platform}/callback",
    openapi_extra={
        "parameters": [
            {
                "name": name,
                "in": "query",
                "required": name == "state",
                "schema": {"type": "string"},
                "description": description,
            }
            for name, description in [
                ("state", "Single-use OAuth state bound to the connecting browser."),
                ("code", "Provider authorization code; exchanged only on the server."),
                ("error", "Provider denial; no authorization code is required."),
            ]
        ]
    },
    response_class=HTMLResponse,
    operation_id="socialOAuthCallback",
    responses={200: {"description": "Connection result. Provider credentials are never returned."}},
)
def callback(platform: SocialPlatform, request: Request):
    service = request.app.state.social_accounts
    params = request.scope.get("social_callback_params", {})
    ok, message = True, "Account connected. You can close this window."
    try:
        service.callback(
            platform,
            params.get("state"),
            request.cookies.get(f"social_oauth_{platform}"),
            params.get("code"),
            bool(params.get("error")),
        )
    except ApiError as error:
        ok, message = False, error.message
    frontend = service.settings.frontend_url
    parsed = urlparse(frontend)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    nonce = __import__("secrets").token_urlsafe(24)
    payload = json.dumps(
        {"type": "shorts-social-connection", "platform": platform, "ok": ok}
    ).replace("<", "\\u003c")
    result = HTMLResponse(
        f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Social connection</title><body><p>{html.escape(message)}</p><a href="{html.escape(frontend, quote=True)}">Return to Shorts Studio</a><script nonce="{nonce}">if(window.opener){{window.opener.postMessage({payload},{json.dumps(origin)});window.close();}}</script></body></html>',
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; base-uri 'none'; frame-ancestors 'none'",
        },
    )
    result.delete_cookie(
        f"social_oauth_{platform}", path=f"/v1/social-accounts/{platform}/callback"
    )
    return result


@router.delete(
    "/social-accounts/{platform}",
    response_model=SocialDisconnected,
    operation_id="disconnectSocialAccount",
)
def disconnect(platform: SocialPlatform, request: Request, user: SocialUser):
    request.app.state.social_accounts.disconnect(user, platform)
    return {"ok": True}


@router.get(
    "/renders/{id}/social-copy", response_model=SocialCopyView, operation_id="getSocialCopy"
)
def get_copy(id: str, request: Request, user: SocialUser):
    return request.app.state.social_copy.get(user, id)


@router.post(
    "/renders/{id}/social-copy", response_model=SocialCopyView, operation_id="generateSocialCopy"
)
def generate_copy(id: str, body: SocialGenerateRequest, request: Request, user: SocialUser):
    return request.app.state.social_copy.generate(
        user, id, body.platforms, transcript_confirmed=body.transcript_confirmed
    )


@router.post(
    "/renders/{id}/social-copy/regenerate",
    response_model=SocialCopyView,
    operation_id="regenerateSocialCopy",
)
def regenerate_copy(id: str, body: SocialGenerateRequest, request: Request, user: SocialUser):
    return request.app.state.social_copy.generate(
        user, id, body.platforms, True, body.transcript_confirmed
    )


@router.get(
    "/social-accounts/tiktok/creator-info",
    response_model=SocialCreatorInfo,
    operation_id="getTikTokCreatorInfo",
)
def tiktok_creator(request: Request, user: SocialUser):
    service = request.app.state.social_accounts
    with service.database.transaction() as db:
        account = db.scalar(
            select(SocialAccount).where(
                SocialAccount.user_id == user, SocialAccount.platform == "tiktok"
            )
        )
        if not account:
            raise ApiError("NOT_FOUND", "Connect TikTok first.", 404)
        account_id = account.id
        rate_limit(db, user, "creator_info", service.now(), 30)
    try:
        creator = service.adapter("tiktok").creator_info(service.token(account_id, user, "tiktok"))
        with service.database.transaction() as db:
            account = db.get(SocialAccount, account_id)
            if account and account.user_id == user:
                account.username = creator.get("username") or account.username
        return creator
    except ProviderError as error:
        raise ApiError(error.code, safe_error(error), 409) from None


@router.post(
    "/renders/{id}/publish",
    response_model=SocialPublishBatchView,
    status_code=202,
    operation_id="publishRender",
)
def publish(
    id: str,
    body: SocialPublishRequest,
    request: Request,
    user: SocialUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    return request.app.state.social_publishing.start(user, id, body, idempotency_key)


@router.get(
    "/publish-batches/{id}", response_model=SocialPublishBatchView, operation_id="getPublishBatch"
)
def batch(id: str, request: Request, user: SocialUser):
    return request.app.state.social_publishing.get(user, id)


@router.post(
    "/publish-jobs/{id}/retry", response_model=SocialPublishJobView, operation_id="retryPublishJob"
)
def retry_publish(id: str, request: Request, user: SocialUser):
    return request.app.state.social_publishing.retry(user, id)


@router.get(
    "/renders/{id}/publishing-history",
    response_model=list[SocialPublishBatchView],
    operation_id="getPublishingHistory",
)
def history(id: str, request: Request, user: SocialUser):
    return request.app.state.social_publishing.history(user, id)


@router.patch(
    "/renders/{id}/social-copy", response_model=SocialCopyView, operation_id="saveSocialCopy"
)
def save_copy(id: str, body: SocialCopySave, request: Request, user: SocialUser):
    service = request.app.state.social_copy
    now = int(service.clock() * 1000)
    with service.database.transaction() as db:
        owned_render(db, user, id, now)
        for platform, content in body.content.items():
            record = db.get(SocialCopy, (id, platform))
            if not record:
                record = SocialCopy(render_id=id, platform=platform)
                db.add(record)
            record.content, record.model, record.updated_at = content, "creator-edited", now
    return service.get(user, id)
