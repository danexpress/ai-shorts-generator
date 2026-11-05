"""The image provider interface for AI scene backgrounds (Story Mode v2).

A provider turns one prompt into one portrait image file. Prompts, seeds, caching,
cost logging and the pan/zoom motion are handled by app/story/scene_images.py and
visuals.SceneImagesBackground, so a provider only has to draw. See README:
"Adding an image provider".
"""

from abc import ABC, abstractmethod
from pathlib import Path


class ImageUnavailable(RuntimeError):
    """Not installed, not configured, or unreachable (worth retrying later)."""


class ImageFailed(RuntimeError):
    """The provider refused this prompt (retrying the same prompt will not help)."""


class ImageProvider(ABC):
    id: str = ""
    label: str = ""
    description: str = ""
    # Bump when output for the same prompt and seed changes (part of the cache key).
    version: str = "1"

    def __init__(self, settings):
        self.settings = settings

    @property
    def size(self) -> tuple[int, int]:
        """(width, height) generated; portrait, cropped to 9:16 by the background."""
        return 768, 1344

    @property
    def model(self) -> str:
        return ""

    @property
    def cost_per_image(self) -> float:
        """USD per image, for cost logs. Local providers are free."""
        return 0.0

    def unavailable_reason(self) -> str | None:
        return None

    @abstractmethod
    def generate(self, prompt: str, seed: int, destination: Path) -> None:
        """Draw `prompt` into `destination` (PNG or JPEG). Same prompt and seed should
        give the same picture where the provider supports seeds."""

    def public(self) -> dict:
        reason = self.unavailable_reason()
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "available": reason is None,
            "reason": reason,
        }
