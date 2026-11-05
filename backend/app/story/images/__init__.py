"""AI image providers for scene backgrounds, selected per render (default STORY_IMAGE_PROVIDER)."""

from .base import ImageFailed, ImageProvider, ImageUnavailable
from .demo import DemoImageProvider
from .flux import FluxProvider
from .openai import OpenAIImageProvider

PROVIDERS: dict[str, type[ImageProvider]] = {
    "flux": FluxProvider,
    "openai": OpenAIImageProvider,
    "demo": DemoImageProvider,
}


def build_image_providers(settings) -> dict[str, ImageProvider]:
    return {name: cls(settings) for name, cls in PROVIDERS.items()}


__all__ = [
    "PROVIDERS",
    "ImageFailed",
    "ImageProvider",
    "ImageUnavailable",
    "build_image_providers",
]
