"""FLUX.1-schnell, free and local on Apple silicon via mflux (Apache-2.0).

Install with `uv sync --extra images`. The first image downloads a 4-bit build of
the model (about 9.6 GB, mflux-community/flux-1-schnell-mflux-q4). Images are
FLUX_SIZE (default 576x1024) in 4 steps. Speed depends heavily on free memory:
measured 59 s per 576x1024 image (163 s at 768x1344) on a 24 GB M4 Pro that was
already deep in swap; expect much less on a machine with memory to spare.
"""

import logging
import platform
import threading
import time

from .base import ImageFailed, ImageProvider, ImageUnavailable

logger = logging.getLogger(__name__)

STEPS = 4  # schnell is distilled for 1-4 steps


def has_mflux() -> bool:
    try:
        import mflux  # noqa: F401
    except ImportError:
        return False
    return True


class FluxProvider(ImageProvider):
    id = "flux"
    label = "FLUX.1 schnell (free, runs locally)"
    description = "Open model on this Mac's GPU; first use downloads about 9.6 GB."

    def __init__(self, settings):
        super().__init__(settings)
        self._model = None
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self.settings.flux_model

    @property
    def size(self):
        width, _, height = self.settings.flux_size.partition("x")
        return int(width) // 16 * 16, int(height) // 16 * 16  # FLUX works in 16 px blocks

    def unavailable_reason(self):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return "Local FLUX needs an Apple-silicon Mac."
        if not has_mflux():
            return "FLUX is not installed on the server (uv sync --extra images)."
        return None

    def load(self):
        if self._model is None:
            reason = self.unavailable_reason()
            if reason:
                raise ImageUnavailable(reason)
            from mflux.models.common.config.model_config import ModelConfig
            from mflux.models.flux.variants.txt2img.flux import Flux1

            started = time.monotonic()
            self._model = Flux1(model_path=self.model, model_config=ModelConfig.schnell())
            logger.info("Loaded %s in %.1fs", self.model, time.monotonic() - started)
        return self._model

    def generate(self, prompt, seed, destination):
        width, height = self.size
        with self._lock:  # one image at a time: the model uses most of the GPU memory
            try:
                image = self.load().generate_image(
                    seed=seed,
                    prompt=prompt,
                    num_inference_steps=STEPS,
                    width=width,
                    height=height,
                )
            except ImageUnavailable:
                raise
            except Exception as exc:  # download, memory or inference failure
                raise ImageUnavailable(f"FLUX could not draw this scene: {exc}") from exc
        try:
            image.image.save(destination, format="PNG")
        except Exception as exc:
            raise ImageFailed("FLUX produced an unreadable image.") from exc
