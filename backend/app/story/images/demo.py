"""Offline stand-in pictures: FFmpeg gradients coloured from the prompt and seed.

Used with STORY_IMAGE_PROVIDER=demo and in tests; no model or key needed.
"""

import hashlib
import subprocess

from ...renderer import ffmpeg_path
from .base import ImageFailed, ImageProvider


class DemoImageProvider(ImageProvider):
    id = "demo"
    label = "Demo pictures (gradients)"
    description = "Coloured gradients instead of AI images, for testing."

    def generate(self, prompt, seed, destination):
        digest = hashlib.sha256(f"{seed}:{prompt}".encode()).hexdigest()
        colors = [f"0x{digest[i : i + 6]}" for i in (0, 6, 12)]
        width, height = self.size
        graph = (
            f"gradients=s={width}x{height}:c0={colors[0]}:c1={colors[1]}:c2={colors[2]}"
            f":nb_colors=3:seed={int(digest[18:26], 16)}:d=1"
        )
        command = [ffmpeg_path(self.settings.ffmpeg_binary), "-hide_banner", "-loglevel", "error"]
        command += ["-y", "-f", "lavfi", "-i", graph, "-frames:v", "1", str(destination)]
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
        if result.returncode or not destination.is_file():
            raise ImageFailed("The demo picture could not be drawn.")
