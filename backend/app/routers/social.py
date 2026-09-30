"""Social endpoints use short transactions and never serialize provider tokens."""

import html
import json
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from ..errors import ApiError
from ..social.models import (
    SocialAccountsView,
    SocialConnectView,
    SocialDisconnected,
    SocialPlatform,
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
