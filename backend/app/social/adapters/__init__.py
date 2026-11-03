"""Adapter registry: which Publisher implementation serves each platform.

Built-in adapters talk to each platform's official API. Set
SOCIAL_ADAPTER_<PLATFORM>=package.module:ClassName to use a different implementation
for a platform, for example one adapter class backed by a unified posting API for
several platforms. The class must subclass SocialPublisher; it is constructed once
per platform with that platform's name. Nothing else in the app changes.
"""

import importlib

from ..config import PLATFORMS
from .base import SocialPublisher

BUILTIN = {
    "youtube": "app.social.adapters.youtube:YouTubePublisher",
    "tiktok": "app.social.adapters.tiktok:TikTokPublisher",
    "x": "app.social.adapters.x:XPublisher",
}


def load_adapter(spec: str) -> type[SocialPublisher]:
    module_name, _, class_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except (ImportError, AttributeError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot load social adapter {spec!r}: expected package.module:Class"
        ) from exc
    if not (isinstance(cls, type) and issubclass(cls, SocialPublisher)):
        raise TypeError(f"Social adapter {spec!r} must subclass SocialPublisher")
    return cls


def publishers(settings, http) -> dict[str, SocialPublisher]:
    adapters = {}
    for platform in PLATFORMS:
        spec = settings.adapter_overrides.get(platform) or BUILTIN[platform]
        adapters[platform] = load_adapter(spec)(settings, http, platform=platform)
    return adapters
