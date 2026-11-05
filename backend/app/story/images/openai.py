"""OpenAI image generation (hosted). Needs OPENAI_API_KEY.

OPENAI_IMAGE_MODEL defaults to gpt-image-2 (gpt-image-1 was retired on
2026-10-23); OPENAI_IMAGE_SIZE and OPENAI_IMAGE_QUALITY are passed through as-is.
Set OPENAI_COST_PER_IMAGE to your price for cost logs. GPT image models take no
seed, so regenerating a scene gives a new picture each time.
"""

import base64

import httpx

from .base import ImageFailed, ImageProvider, ImageUnavailable

API = "https://api.openai.com/v1/images/generations"


class OpenAIImageProvider(ImageProvider):
    id = "openai"
    label = "OpenAI images (hosted)"
    description = "Highest detail; billed per image by OpenAI."

    def __init__(self, settings, http: httpx.Client | None = None):
        super().__init__(settings)
        self.http = http

    @property
    def model(self) -> str:
        return self.settings.openai_image_model

    @property
    def size(self):
        width, _, height = self.settings.openai_image_size.partition("x")
        return int(width), int(height)

    @property
    def cost_per_image(self) -> float:
        return self.settings.openai_cost_per_image

    def unavailable_reason(self):
        if not self.settings.openai_api_key:
            return "Set OPENAI_API_KEY on the server to use OpenAI images."
        return None

    def generate(self, prompt, seed, destination):
        if self.unavailable_reason():
            raise ImageUnavailable(self.unavailable_reason())
        body = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": self.settings.openai_image_size,
            "quality": self.settings.openai_image_quality,
        }
        client = self.http or httpx.Client(timeout=httpx.Timeout(180, connect=10))
        try:
            response = client.post(
                API, headers={"Authorization": f"Bearer {self.settings.openai_api_key}"}, json=body
            )
        except httpx.HTTPError as exc:
            raise ImageUnavailable("Could not reach OpenAI.") from exc
        finally:
            if self.http is None:
                client.close()
        if response.status_code in (401, 403):
            raise ImageUnavailable("OpenAI rejected the API key or this image model.")
        if response.status_code == 429 or response.status_code >= 500:
            raise ImageUnavailable(f"OpenAI is busy (HTTP {response.status_code}); retry later.")
        if response.status_code != 200:
            message = ""
            try:
                message = response.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            raise ImageFailed(f"OpenAI declined this scene's picture. {message}".strip())
        try:
            data = base64.b64decode(response.json()["data"][0]["b64_json"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageUnavailable("OpenAI returned an unreadable image.") from exc
        destination.write_bytes(data)
