import dataclasses
from types import SimpleNamespace

import pytest
from conftest import assert_error, login, record, rows, uploaded
from fastapi.testclient import TestClient

from app.db_models import JobRecord, TranscriptRecord
from app.jobs.worker import ProcessingWorker
from app.main import create_app
from app.transcription import (
    FasterWhisperTranscriber,
    TranscriptionError,
    keep_segment,
    to_segments,
)
from app.transcription.cache import cache_path, source_digest

SPEECH = {
    "language": "en",
    "duration": 300.0,
    "model": "fake",
    "segments": [
        {
            "start": 0.5,
            "end": 1.6,
            "text": "Hire for tomorrow.",
            "words": [
                {"text": "Hire", "start": 0.5, "end": 0.8, "probability": 0.99},
                {"text": "for", "start": 0.8, "end": 1.0, "probability": 0.98},
                {"text": "tomorrow.", "start": 1.0, "end": 1.6, "probability": 0.97},
            ],
        }
    ],
}


class FakeTranscriber:
    key = "fake"

    def __init__(self, result=SPEECH, during=None, error=None):
        self.result, self.during, self.error, self.calls = result, during, error, 0

    def transcribe(self, source, on_progress=None):
        self.calls += 1
        if self.during:
            self.during()
        if on_progress:
            on_progress(0.5)
        if self.error:
            raise self.error
        return dict(self.result)


@pytest.fixture
def local_settings(settings):
    # Transcription tests keep the demo analyzer; test_highlights covers the LLM path.
    return dataclasses.replace(settings, processing_mode="local", highlight_model="demo")


@pytest.fixture
def local_client(database, local_settings, clock):
    with TestClient(create_app(database=database, settings=local_settings, clock=clock)) as c:
        yield c


def start(client, headers):
    project_id = uploaded(client, headers)
    response = client.post(
        f"/v1/projects/{project_id}/process", headers=headers, json={"goal": "educational"}
    )
    assert response.status_code == 200, response.text
    return project_id


def status(client, headers, project_id):
    return client.get(f"/v1/projects/{project_id}/status", headers=headers).json()


def worker(database, settings, clock, transcriber):
    w = ProcessingWorker(database, settings, transcriber, clock)
    w.heartbeat_seconds = 0
    return w


def test_worker_transcribes_then_analyzes_real_words(local_client, database, local_settings, clock):
    headers = login(local_client)
    project_id = start(local_client, headers)
    clock.advance(60)  # the API no longer simulates progress in local mode
    assert status(local_client, headers, project_id)["status"] == "transcribing"

    w = worker(database, local_settings, clock, FakeTranscriber())
    assert w.run_once()
    current = status(local_client, headers, project_id)
    assert current["status"] == "analyzing" and current["stage"] == "analyzing"
    transcript = local_client.get(f"/v1/projects/{project_id}/transcript", headers=headers).json()
    assert transcript["language"] == "en"
    assert transcript["segments"][0]["words"] == [
        {"w": "Hire", "s": 0.5, "e": 0.8},
        {"w": "for", "s": 0.8, "e": 1.0},
        {"w": "tomorrow.", "s": 1.0, "e": 1.6},
    ]

    assert w.run_once()
    assert status(local_client, headers, project_id)["status"] == "ready"
    clips = local_client.get(f"/v1/projects/{project_id}/clips", headers=headers).json()
    assert clips["clips"] and clips["clips"][0]["start"] == 0.5
    assert not w.run_once()  # nothing left to claim
    assert rows(database, JobRecord)[-1]["state"] == "succeeded"


def test_same_source_is_never_transcribed_twice(local_client, database, local_settings, clock):
    headers = login(local_client)
    fake = FakeTranscriber()
    w = worker(database, local_settings, clock, fake)
    first = start(local_client, headers)
    while w.run_once():
        pass
    second = start(local_client, headers)  # identical bytes, new project
    while w.run_once():
        pass
    assert fake.calls == 1
    assert status(local_client, headers, second)["status"] == "ready"
    digest = record(database, TranscriptRecord, second)["sourceDigest"]
    cached = cache_path(local_settings.media_dir, digest, "fake")
    assert cached.is_file()

    # The shared cache file is removed with the last transcript that uses it.
    local_client.delete(f"/v1/projects/{first}", headers=headers)
    assert cached.is_file()
    local_client.delete(f"/v1/projects/{second}", headers=headers)
    assert not cached.exists()


def test_no_speech_fails_clearly_without_retry(local_client, database, local_settings, clock):
    headers = login(local_client)
    project_id = start(local_client, headers)
    silent = {**SPEECH, "segments": []}
    w = worker(database, local_settings, clock, FakeTranscriber(silent))
    assert w.run_once()
    current = status(local_client, headers, project_id)
    assert current["status"] == "failed"
    assert current["failureCode"] == "NO_SPEECH"
    assert current["canRetry"] is False
    assert_error(
        local_client.post(f"/v1/projects/{project_id}/retry", headers=headers),
        409,
        "INVALID_STATE",
    )
    # The creator can start another video; the active-job slot was released.
    assert start(local_client, headers)


def test_transcription_errors_are_retryable(local_client, database, local_settings, clock):
    headers = login(local_client)
    project_id = start(local_client, headers)
    w = worker(database, local_settings, clock, FakeTranscriber(error=TranscriptionError("x")))
    assert w.run_once()
    current = status(local_client, headers, project_id)
    assert (current["failureCode"], current["canRetry"]) == ("TRANSCRIPTION_FAILED", True)

    w._transcriber = FakeTranscriber()
    assert local_client.post(f"/v1/projects/{project_id}/retry", headers=headers).status_code == 200
    while w.run_once():
        pass
    assert status(local_client, headers, project_id)["status"] == "ready"


def test_cancel_during_transcription_discards_result(local_client, database, local_settings, clock):
    headers = login(local_client)
    project_id = start(local_client, headers)

    def cancel():
        response = local_client.post(f"/v1/projects/{project_id}/cancel", headers=headers)
        assert response.status_code == 200, response.text

    w = worker(database, local_settings, clock, FakeTranscriber(during=cancel))
    assert w.run_once()
    assert status(local_client, headers, project_id)["status"] == "canceled"
    assert record(database, TranscriptRecord, project_id) is None


def test_progress_is_reported_and_leases_block_other_workers(
    local_client, database, local_settings, clock
):
    headers = login(local_client)
    project_id = start(local_client, headers)
    first = worker(database, local_settings, clock, FakeTranscriber())
    second = worker(database, local_settings, clock, FakeTranscriber())
    assert first.claim()
    assert second.claim() is None
    clock.advance(local_settings.job_lease_seconds + 1)  # an abandoned lease expires
    assert second.claim()

    def check_progress():
        assert status(local_client, headers, project_id)["progress"] == 0.3  # 0.6 * 0.5

    class Checking(FakeTranscriber):
        def transcribe(self, source, on_progress=None):
            on_progress(0.5)
            check_progress()
            return dict(self.result)

    second._transcriber = Checking()
    second.transcribe(rows(database, JobRecord)[-1]["id"])
    assert status(local_client, headers, project_id)["status"] == "analyzing"


def test_keep_segment_filters_music_and_hallucinations():
    assert keep_segment("Hello there.", 0.1, -0.3, 1.2)
    assert not keep_segment(" ♪ ♪ ", 0.1, -0.3, 1.0)
    assert not keep_segment("Thank you.", 0.9, -1.5, 1.0)
    assert not keep_segment("the the the the the", 0.1, -0.2, 3.1)


def test_faster_whisper_output_is_normalized(tmp_path):
    word = lambda text, s, e: SimpleNamespace(word=text, start=s, end=e, probability=0.91234)
    segments = [
        SimpleNamespace(
            text=" Hello world.",
            start=0.0,
            end=1.2,
            no_speech_prob=0.01,
            avg_logprob=-0.2,
            compression_ratio=1.1,
            words=[word(" Hello", 0.1, 0.5), word(" world.", 0.5, 1.15)],
        ),
        SimpleNamespace(
            text=" ♪",
            start=2.0,
            end=9.0,
            no_speech_prob=0.2,
            avg_logprob=-0.4,
            compression_ratio=1.0,
            words=[word(" ♪", 2.0, 9.0)],
        ),
    ]
    seen = []

    class Model:
        def transcribe(self, path, **options):
            assert options["word_timestamps"] and options["vad_filter"]
            return iter(segments), SimpleNamespace(language="en", duration=10.0)

    transcriber = FasterWhisperTranscriber("small", device="cpu", compute_type="int8")
    transcriber._model = Model()
    result = transcriber.transcribe(tmp_path / "a.mp4", seen.append)
    assert seen == [0.12, 0.9]
    assert result["segments"] == [
        {
            "start": 0.1,
            "end": 1.15,
            "text": "Hello world.",
            "words": [
                {"text": "Hello", "start": 0.1, "end": 0.5, "probability": 0.912},
                {"text": "world.", "start": 0.5, "end": 1.15, "probability": 0.912},
            ],
        }
    ]
    assert to_segments(result)[0]["words"][1] == {"w": "world.", "s": 0.5, "e": 1.15, "p": 0.912}


def test_decoder_failures_become_transcription_errors(tmp_path):
    class Broken:
        def transcribe(self, path, **options):
            raise RuntimeError("Invalid data found when processing input")

    transcriber = FasterWhisperTranscriber("small", device="cpu", compute_type="int8")
    transcriber._model = Broken()
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(tmp_path / "a.mp4")


def test_source_digest_is_content_based(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    assert source_digest(a) == source_digest(b)


# ---------- MLX (Apple-silicon GPU) backend, with a fake mlx_whisper ----------

import sys

import numpy as np

from app.transcription import service as transcription_service
from app.transcription.service import (
    SAMPLE_RATE,
    MLXWhisperTranscriber,
    build_transcriber,
    mlx_repo,
    split_points,
)


def test_chunks_cut_at_the_quietest_moment_near_each_boundary():
    samples = np.full(SAMPLE_RATE * 700, 0.5, np.float32)
    quiet = SAMPLE_RATE * 304  # silence 4 s after the 300 s target
    samples[quiet : quiet + SAMPLE_RATE // 5] = 0.0
    chunks = split_points(samples, chunk_seconds=300, search_seconds=10)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(samples)
    assert abs(chunks[0][1] - quiet) < SAMPLE_RATE // 5
    assert all(a < b for a, b in chunks)
    assert [b for _, b in chunks[:-1]] == [a for a, _ in chunks[1:]]  # contiguous
    # A short remainder is merged rather than left as a fragment.
    assert len(split_points(np.zeros(SAMPLE_RATE * 320, np.float32), 300)) == 1
    assert split_points(np.zeros(0, np.float32)) == []


def test_mlx_model_names_map_to_hugging_face_repos():
    assert mlx_repo("small") == "mlx-community/whisper-small-mlx"
    assert mlx_repo("turbo") == "mlx-community/whisper-large-v3-turbo"
    assert mlx_repo("small.en") == "mlx-community/whisper-small.en-mlx"
    assert mlx_repo("me/custom") == "me/custom"


class FakeMLX:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append({"seconds": len(audio) / SAMPLE_RATE, **options})
        word = lambda text, s, e: {"word": " " + text, "start": s, "end": e, "probability": 0.9}
        return {
            "language": "en",
            "segments": [
                {
                    "text": " Hello there.",
                    "no_speech_prob": 0.01,
                    "avg_logprob": -0.2,
                    "compression_ratio": 1.1,
                    "words": [word("Hello", 1.0, 1.4), word("there.", 1.4, 1.9)],
                },
                {
                    "text": " ♪",
                    "no_speech_prob": 0.2,
                    "avg_logprob": -0.4,
                    "compression_ratio": 1.0,
                    "words": [word("♪", 5.0, 9.0)],
                },
            ],
        }


def test_mlx_transcriber_offsets_chunks_pins_language_and_reports_progress(monkeypatch, tmp_path):
    fake = FakeMLX()
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    monkeypatch.setattr(
        transcription_service,
        "decode_audio",
        lambda source, ffmpeg: np.ones(SAMPLE_RATE * 650, np.float32),
    )
    progress = []
    result = MLXWhisperTranscriber("small", ffmpeg="ffmpeg").transcribe(
        tmp_path / "v.webm", progress.append
    )
    assert len(fake.calls) >= 2 and sum(c["seconds"] for c in fake.calls) == pytest.approx(650)
    assert fake.calls[0]["language"] is None and {c["language"] for c in fake.calls[1:]} == {"en"}
    assert all(
        c["word_timestamps"] and c["path_or_hf_repo"].endswith("whisper-small-mlx")
        for c in fake.calls
    )
    # One kept segment per chunk ("♪" dropped), shifted by the chunk's start time.
    assert [s["text"] for s in result["segments"]] == ["Hello there."] * len(fake.calls)
    assert result["segments"][1]["start"] == pytest.approx(fake.calls[0]["seconds"] + 1.0, abs=0.01)
    assert len(progress) == len(fake.calls) and progress[-1] == 1.0
    assert result["duration"] == 650 and result["model"] == "mlx-whisper-small-auto"


def test_mlx_errors_become_transcription_errors(monkeypatch, tmp_path):
    class Broken:
        def transcribe(self, audio, **options):
            raise RuntimeError("download failed")

    monkeypatch.setitem(sys.modules, "mlx_whisper", Broken())
    monkeypatch.setattr(
        transcription_service, "decode_audio", lambda s, f: np.ones(SAMPLE_RATE * 40, np.float32)
    )
    with pytest.raises(TranscriptionError):
        MLXWhisperTranscriber("small", ffmpeg="ffmpeg").transcribe(tmp_path / "v.mp4")


def test_backend_selection(monkeypatch, settings):
    monkeypatch.setattr(transcription_service, "mlx_available", lambda: True)
    assert isinstance(
        build_transcriber(dataclasses.replace(settings, whisper_backend="auto")),
        MLXWhisperTranscriber,
    )
    forced = build_transcriber(dataclasses.replace(settings, whisper_backend="faster-whisper"))
    assert isinstance(forced, FasterWhisperTranscriber)
    monkeypatch.setattr(transcription_service, "mlx_available", lambda: False)
    assert isinstance(
        build_transcriber(dataclasses.replace(settings, whisper_backend="auto")),
        FasterWhisperTranscriber,
    )
    with pytest.raises(ValueError):
        build_transcriber(dataclasses.replace(settings, whisper_backend="gpu-please"))


def test_unexpected_stage_errors_fail_the_job_but_never_stop_the_worker(
    local_client, database, local_settings, clock
):
    headers = login(local_client)
    project_id = start(local_client, headers)

    class Exploding:
        key = "boom"

        def transcribe(self, source, on_progress=None):
            raise KeyError("unexpected")  # not a TranscriptionError

    w = worker(database, local_settings, clock, Exploding())
    assert w.run_once() is True  # handled, not raised
    current = status(local_client, headers, project_id)
    assert (current["status"], current["failureCode"], current["canRetry"]) == (
        "failed",
        "TRANSCRIPTION_FAILED",
        True,
    )
    assert w.run_once() is False  # the worker is still alive and the queue is empty
