from pathlib import Path

import pytest

from app import youtube


@pytest.mark.parametrize(
    "url,expected_id",
    [
        ("https://www.youtube.com/watch?v=abcDEF_1234&t=20", "abcDEF_1234"),
        ("https://youtu.be/abcDEF_1234?si=example", "abcDEF_1234"),
        ("https://m.youtube.com/watch?v=abcDEF_1234", "abcDEF_1234"),
    ],
)
def test_youtube_urls_are_normalized_to_video_ids(url, expected_id):
    video_id, canonical = youtube.normalize_youtube_url(url)
    assert video_id == expected_id
    assert canonical == f"https://www.youtube.com/watch?v={expected_id}"


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=abcDEF_1234",
        "https://youtube.com.evil.test/watch?v=abcDEF_1234",
        "https://user:pass@youtu.be/abcDEF_1234",
        "https://youtu.be:8443/abcDEF_1234",
        "https://youtu.be/abcDEF_1234/other",
        "https://youtube.com/shorts/abcDEF_1234",
        "file:///etc/passwd",
    ],
)
def test_youtube_urls_reject_untrusted_hosts_and_shapes(url):
    with pytest.raises(youtube.InvalidVideoUrl):
        youtube.normalize_youtube_url(url)


def test_download_youtube_saves_one_capped_source(monkeypatch, tmp_path):
    observed = {}

    class FakeDownloader:
        def __init__(self, options):
            observed.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download):
            observed["url"] = url
            observed["metadata_download"] = download
            return {
                "id": "abcDEF_1234",
                "title": "Creator's episode",
                "duration": 95,
                "filesize": 1024,
                "width": 1920,
                "height": 1080,
                "fps": 30,
            }

        def download(self, urls):
            observed["download_urls"] = urls
            output = Path(observed["outtmpl"].replace("%(id)s.%(ext)s", "abcDEF_1234.mp4"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"downloaded source")

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeDownloader)
    monkeypatch.setattr(youtube, "ffmpeg_path", lambda _configured: "/fake/ffmpeg")
    quota = []
    result = youtube.download_youtube(
        "https://youtu.be/abcDEF_1234", tmp_path / "sources", before_download=quota.append
    )

    assert result["path"].read_bytes() == b"downloaded source"
    assert result["durationSec"] == 95
    assert result["mimeType"] == "video/mp4"
    assert quota == [95.0]
    assert observed["url"] == "https://www.youtube.com/watch?v=abcDEF_1234"
    assert observed["metadata_download"] is False
    assert observed["download_urls"] == [observed["url"]]
    assert observed["noplaylist"] is True
    assert observed["max_filesize"] == 4 * 1024**3
    assert observed["socket_timeout"] <= 20


def test_download_checks_duration_before_fetching_bytes(monkeypatch, tmp_path):
    class FakeDownloader:
        downloaded = False

        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            return {"id": "abcDEF_1234", "duration": 3601}

        def download(self, _urls):
            self.downloaded = True

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeDownloader)
    monkeypatch.setattr(youtube, "ffmpeg_path", lambda _configured: "/fake/ffmpeg")
    with pytest.raises(youtube.VideoLimitError) as error:
        youtube.download_youtube("https://youtu.be/abcDEF_1234", tmp_path)
    assert error.value.code == "SOURCE_TOO_LONG"


def test_progress_hook_aborts_after_size_limit(monkeypatch, tmp_path):
    class FakeDownloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            return {"id": "abcDEF_1234", "duration": 90}

        def download(self, _urls):
            self.options["progress_hooks"][0](
                {"status": "downloading", "downloaded_bytes": youtube.MAX_BYTES + 1}
            )

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeDownloader)
    monkeypatch.setattr(youtube, "ffmpeg_path", lambda _configured: "/fake/ffmpeg")
    with pytest.raises(youtube.VideoDownloadError):
        youtube.download_youtube("https://youtu.be/abcDEF_1234", tmp_path)
    assert not list(tmp_path.rglob("*.mp4"))
