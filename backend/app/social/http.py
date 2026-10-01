"""Provider transport: no redirects, no raw exceptions or response-body logging."""

import logging
from urllib.parse import urlparse

import httpx

from .adapters.base import ProviderError


class ProviderHTTP:
    def __init__(self, transport=None):
        # HTTP debug logs can include upload capability URLs and auth headers.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self.client = httpx.Client(
            timeout=httpx.Timeout(45, connect=10), follow_redirects=False, transport=transport
        )

    def request(self, method, url, *, hosts, expected=(200, 201, 202, 204), **kwargs):
        parsed = urlparse(url)
        if (
            not parsed.hostname
            or parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or not any(
                parsed.hostname == host or parsed.hostname.endswith("." + host) for host in hosts
            )
        ):
            raise ProviderError("PROVIDER_RESPONSE_INVALID")
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise ProviderError(
                "PROVIDER_UNAVAILABLE", retryable=True, uncertain=method not in {"GET", "HEAD"}
            ) from None
        if response.status_code in expected:
            return response
        code = "PROVIDER_REJECTED"
        retry = response.status_code == 429 or response.status_code >= 500
        if response.status_code == 401:
            code = "RECONNECT_REQUIRED"
        elif response.status_code == 403:
            code = "PERMISSION_REQUIRED"
        elif response.status_code == 429:
            code = "PROVIDER_RATE_LIMITED"
        # Only inspect known machine-readable codes; never propagate provider messages.
        try:
            data = response.json()
            error = data.get("error", {})
            reason = error.get("code") if isinstance(error, dict) else error
            if isinstance(error, dict) and error.get("errors"):
                reason = error["errors"][0].get("reason")
            if reason in {"invalid_grant", "access_token_invalid"}:
                code, retry = "RECONNECT_REQUIRED", False
            elif reason in {"scope_not_authorized", "insufficientPermissions"}:
                code, retry = "PERMISSION_REQUIRED", False
            elif reason in {
                "unaudited_client_can_only_post_to_private_accounts",
                "privacy_level_option_mismatch",
            }:
                code, retry = "PRIVATE_POSTING_ONLY", False
        except (ValueError, TypeError, AttributeError, IndexError):
            pass
        raise ProviderError(
            code,
            retryable=retry,
            uncertain=response.status_code >= 500 and method not in {"GET", "HEAD"},
        )

    def json(self, method, url, **kwargs):
        try:
            data = self.request(method, url, **kwargs).json()
            if not isinstance(data, dict):
                raise TypeError
            return data
        except (ValueError, TypeError):
            raise ProviderError("PROVIDER_RESPONSE_INVALID") from None

    def close(self):
        self.client.close()
