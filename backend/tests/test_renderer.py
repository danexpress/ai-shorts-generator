"""Decode real exports to verify framing, rather than mocking FFmpeg."""

import subprocess

import imageio_ffmpeg
import pytest

from app.renderer import render_short


@pytest.mark.parametrize("resolution", [720, 1080])
@pytest.mark.parametrize(
    "source_width,source_height,sar",
    [(160, 90, 1), (90, 160, 1), (100, 100, 1), (60, 160, 1), (162, 92, 2)],
    ids=["landscape", "portrait", "square", "tall", "non-square-pixels"],
)
def test_render_preserves_all_corners_and_aspect_ratio(
    tmp_path, resolution, source_width, source_height, sar
):
    # Only the corners are colored. A center crop loses these markers.
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    pixels = bytearray()
    for y in range(source_height):
        for x in range(source_width):
            corner_x = x < source_width * 0.2 or x >= source_width * 0.8
            corner_y = y < source_height * 0.2 or y >= source_height * 0.8
            color = colors[int(y >= source_height / 2) * 2 + int(x >= source_width / 2)]
            pixels.extend(color if corner_x and corner_y else (128, 128, 128))
    still = tmp_path / "corners.ppm"
    still.write_bytes(f"P6\n{source_width} {source_height}\n255\n".encode() + pixels)
    source, output = tmp_path / "source.mp4", tmp_path / "short.mp4"
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(still),
            "-t",
            "0.3",
            "-r",
            "10",
            "-vf",
            f"setsar={sar}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    render_short(source, output, 0, 0.2, resolution, executable)
    reader = imageio_ffmpeg.read_frames(str(output), pix_fmt="rgb24")
    try:
        metadata, frame = next(reader), next(reader)
    finally:
        reader.close()

    width, height = resolution, resolution * 16 // 9
    assert metadata["size"] == (width, height)

    def pixel(x, y):
        offset = (int(y) * width + int(x)) * 3
        return tuple(frame[offset : offset + 3])

    ratio = source_width * sar / source_height
    fitted_width = min(width, height * ratio)
    fitted_height = min(height, width / ratio)
    left, top = (width - fitted_width) / 2, (height - fitted_height) / 2
    for (x, y), expected in zip([(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)], colors):
        actual = pixel(left + x * fitted_width, top + y * fitted_height)
        assert all(abs(a - b) < 35 for a, b in zip(actual, expected)), (actual, expected)

    # Check the padding boundary to catch stretching or an incorrect aspect ratio.
    if top > 10:
        assert max(pixel(width / 2, top - 6)) < 20
        assert min(pixel(width / 2, top + 6)) > 90
        assert max(pixel(width / 2, height - top + 6)) < 20
    if left > 10:
        assert max(pixel(left - 6, height / 2)) < 20
        assert min(pixel(left + 6, height / 2)) > 90
        assert max(pixel(width - left + 6, height / 2)) < 20
