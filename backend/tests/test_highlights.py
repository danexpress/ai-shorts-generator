import dataclasses
import itertools
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import anthropic
import imageio_ffmpeg
import pytest
from conftest import assert_error, login, rows, uploaded
from fastapi.testclient import TestClient

from app.db_models import AnalysisRunRecord
from app.highlights import (
    PROMPT_VERSION,
    ClaudeHighlightModel,
    HighlightFailed,
    HighlightUnavailable,
    InvalidOutput,
    NoHighlights,
    find_highlights,
)
from app.highlights.signals import extract, rerank
from app.highlights.transcript import format_lines, sentences, windows
from app.highlights.validate import dedupe, parse_candidates, snap
from app.jobs.worker import ProcessingWorker
from app.main import create_app

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "podcast_long.json").read_text())
SEGMENTS, DURATION = FIXTURE["segments"], FIXTURE["duration"]
UNITS = sentences(SEGMENTS)
WORDS = [w for u in UNITS for w in u["words"]]
PARAMS = {"goal": "educational", "instruction": "Focus on hiring", "avoid": ["pricing"]}


def inside_a_word(t):
    return any(w["start"] + 1e-6 < t < w["end"] - 1e-6 for w in WORDS)


class FakeLLM:
    """Returns queued answers (dicts, or exceptions to raise) and records each call."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []
        self.usage = SimpleNamespace(summary=lambda: "fake")

    def complete(self, system, cached, variable, schema):
        self.calls.append(
            {"system": system, "cached": cached, "variable": variable, "schema": schema}
        )
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def candidate(start, end, score=8, hook="Most founders hire backwards", reason="Strong hook."):
    return {"start": start, "end": end, "hook": hook, "reason": reason, "score": score}


@pytest.fixture
def hl_settings(settings):
    return dataclasses.replace(settings, processing_mode="local")


# ---------- transcript units ----------


def test_sentences_split_at_punctuation_and_long_pauses():
    assert UNITS[2]["text"] == "Most founders hire backwards."
    assert (UNITS[2]["start"], UNITS[2]["end"]) == (5.85, 7.72)
    pause = [{"words": [{"w": "so", "s": 0, "e": 0.3}, {"w": "anyway", "s": 1.5, "e": 2.0}]}]
    assert [u["text"] for u in sentences(pause)] == ["so", "anyway"]
    edited = [{"words": [{"w": "Two words.", "s": 1.0, "e": 2.0}]}]
    assert [w["text"] for w in sentences(edited)[0]["words"]] == ["Two", "words."]


def test_windows_overlap_by_more_than_a_clip_and_cover_everything():
    parts = windows(UNITS, max_tokens=300, overlap_seconds=45)
    assert len(parts) > 1
    assert parts[0][0] is UNITS[0] and parts[-1][-1] is UNITS[-1]
    for left, right in itertools.pairwise(parts):
        # Overlap is only given up when a sentence alone is too big to share a window.
        oversized = any(u["end"] - u["start"] > 40 for u in right)
        assert right[0]["start"] <= left[-1]["end"] - 45 or oversized
        assert left[-1] in right or right[0]["start"] > left[-1]["start"]
    covered = {id(u) for part in parts for u in part}
    assert covered == {id(u) for u in UNITS}
    assert windows(UNITS, max_tokens=100_000, overlap_seconds=45) == [UNITS]
    assert format_lines(UNITS[:1]) == "[0.50-2.40] Welcome back to the show."


# ---------- validation ----------


def test_parse_rejects_malformed_and_out_of_range_candidates():
    with pytest.raises(InvalidOutput):
        parse_candidates([], DURATION)
    valid, problems = parse_candidates(
        {
            "candidates": [
                candidate(5.85, 36.46),
                candidate(-1, 20),
                candidate(140, 200),
                candidate(10, 30, score=11),
                {"start": "5", "end": 30, "hook": "x", "reason": "y", "score": 5},
                candidate(10, 30, hook="  "),
                "nope",
            ]
        },
        DURATION,
    )
    assert valid == [candidate(5.85, 36.46, score=8.0)]
    assert len(problems) == 6
    assert any("outside the video" in p for p in problems)
    assert any("between 0 and 10" in p for p in problems)


def test_snap_moves_boundaries_to_whole_sentences():
    start, end = snap(6.3, 35.0, UNITS, 20, 40, DURATION)  # both mid-sentence
    assert 5.6 <= start <= 5.85 and 36.46 <= end <= 36.8  # sentence edges plus padding
    assert not inside_a_word(start) and not inside_a_word(end)
    assert 20 <= end - start <= 40


@pytest.mark.parametrize(
    "start,end",
    [(5.85, 70.0), (42.2, 50.0), (0.0, 3.0), (140.0, 149.0), (90.0, 140.0), (60.0, 100.0)],
    ids=["too-long", "too-short", "start-of-video", "end-of-video", "run-on", "into-run-on"],
)
def test_snapped_clips_are_20_to_40s_and_never_cut_words(start, end):
    bounds = snap(start, end, UNITS, 20, 40, DURATION)
    assert bounds is not None
    assert 20 <= bounds[1] - bounds[0] <= 40 + 1e-6
    assert not inside_a_word(bounds[0]) and not inside_a_word(bounds[1])
    assert 0 <= bounds[0] and bounds[1] <= DURATION


def test_snap_fails_when_no_valid_clip_exists():
    short = sentences(SEGMENTS[:3])  # 7 s of speech
    assert snap(0, 7, short, 20, 40, DURATION) is None


def test_dedupe_keeps_the_best_of_overlapping_candidates():
    a, b, c = candidate(0, 30, 9), candidate(5, 35, 7), candidate(40, 70, 6)
    assert dedupe([b, c, a], limit=3) == [a, c]
    assert dedupe([a, b, c], limit=1) == [a]


# ---------- highlight search (mocked LLM) ----------


def test_find_highlights_prompts_validates_and_ranks(hl_settings):
    llm = FakeLLM({"candidates": [candidate(24.6, 41.7, 6), candidate(5.85, 36.46, 9)]})
    found = find_highlights(SEGMENTS, DURATION, PARAMS, llm, hl_settings)
    assert [h["score"] for h in found] == [9.0]  # the overlapping weaker one is dropped
    assert not inside_a_word(found[0]["start"]) and 20 <= found[0]["end"] - found[0]["start"] <= 40
    call = llm.calls[0]
    assert "[5.85-7.72] Most founders hire backwards." in call["cached"]
    assert call["cached"].startswith('<transcript duration_seconds="149.41">')
    assert "between 20 and 40 seconds" in call["system"] and "at most 3 moments" in call["system"]
    assert "Educational" in call["variable"] and "Focus on hiring" in call["variable"]
    assert "Avoid moments about: pricing" in call["variable"]
    assert call["schema"]["required"] == ["candidates"]


def test_invalid_output_is_retried_once_with_the_problems(hl_settings):
    llm = FakeLLM(
        {"candidates": [candidate(200, 230)]},
        {"candidates": [candidate(5.85, 36.46)]},
    )
    found = find_highlights(SEGMENTS, DURATION, PARAMS, llm, hl_settings)
    assert len(llm.calls) == 2 and len(found) == 1
    assert "outside the video" in llm.calls[1]["variable"]
    assert llm.calls[1]["cached"] == llm.calls[0]["cached"]  # cacheable prefix unchanged


def test_second_invalid_output_fails_the_analysis(hl_settings):
    llm = FakeLLM(
        InvalidOutput("The answer was not valid JSON."), {"candidates": [candidate(-5, 2)]}
    )
    with pytest.raises(HighlightFailed):
        find_highlights(SEGMENTS, DURATION, PARAMS, llm, hl_settings)
    assert "not valid JSON" in llm.calls[1]["variable"]


def test_no_usable_moment_and_too_little_speech_are_reported(hl_settings):
    with pytest.raises(NoHighlights):
        find_highlights(SEGMENTS, DURATION, PARAMS, FakeLLM({"candidates": []}), hl_settings)
    llm = FakeLLM()
    with pytest.raises(NoHighlights, match="not enough speech"):
        find_highlights(SEGMENTS[:3], 30, PARAMS, llm, hl_settings)
    assert llm.calls == []


def test_long_transcripts_use_overlapping_windows_and_a_final_rerank(hl_settings):
    settings = dataclasses.replace(hl_settings, highlight_window_tokens=400)
    parts = windows(UNITS, 400, 45)
    window_answers = [
        {"candidates": [candidate(w[0]["start"], min(w[0]["start"] + 25, DURATION), 5 + i)]}
        for i, w in enumerate(parts)
    ]
    llm = FakeLLM(
        *window_answers, {"ranking": [{"id": "m1", "score": 9.5, "hook": "Best", "reason": "Why"}]}
    )
    found = find_highlights(SEGMENTS, DURATION, PARAMS, llm, settings)
    assert len(llm.calls) == len(parts) + 1
    rerank_call = llm.calls[-1]
    assert rerank_call["schema"]["required"] == ["ranking"]
    assert rerank_call["cached"].count("<candidate id=") >= 2
    assert [(h["hook"], h["score"]) for h in found] == [("Best", 9.5)]


def test_signals_rerank_rewards_louder_more_dynamic_windows():
    quiet, loud = candidate(0, 20, 7.0), candidate(30, 50, 6.8)
    signals = {
        "loudness": [(t / 10, -30.0 if t < 300 else -18.0) for t in range(600)],
        "scenes": [35.0, 40.0, 45.0],
    }
    ranked = rerank([quiet, loud], signals, 60)
    assert ranked[0]["start"] == 30 and ranked[0]["score"] > 6.8
    assert ranked[0]["signals"]["sceneChanges"] == 3 and ranked[0]["signals"]["loudnessLu"] > 0
    assert -1.25 <= ranked[1]["signals"]["adjustment"] <= 1.25


def test_signal_extraction_reads_real_loudness_and_cuts(tmp_path):
    source = tmp_path / "signals.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=160x90:d=3,format=yuv420p[a];color=blue:s=160x90:d=3,format=yuv420p[b];[a][b]concat=n=2:v=1",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=3,volume=0.05[q];sine=f=440:d=3,volume=0.8[l];[q][l]concat=n=2:v=0:a=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    signals = extract(source, imageio_ffmpeg.get_ffmpeg_exe())
    early = [m for t, m in signals["loudness"] if 1 < t < 2.5]
    late = [m for t, m in signals["loudness"] if 4 < t < 5.5]
    assert early and late and min(late) > max(early) + 10
    assert any(2.5 <= t <= 3.5 for t in signals["scenes"])


# ---------- Claude API client (fake SDK client) ----------


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def sdk_client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def response(text='{"candidates": []}', stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(
        model=model,
        stop_reason=stop,
        stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=0,
        ),
        _request_id="req_test",
    )


def test_claude_request_uses_structured_output_caching_and_fallbacks():
    messages = FakeMessages(response('{"candidates": [{"start": 1}]}'))
    model = ClaudeHighlightModel("claude-opus-5-5", "high", "default", client=sdk_client(messages))
    assert model.complete("SYS", "TRANSCRIPT", "GUIDE", {"type": "object"}) == {
        "candidates": [{"start": 1}]
    }
    request = messages.requests[0]
    assert request["model"] == "claude-opus-5-5" and request["system"] == "SYS"
    assert request["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    assert (
        request["betas"] == ["server-side-fallback-2026-07-01"]
        and request["fallbacks"] == "default"
    )
    first, second = request["messages"][0]["content"]
    assert first == {"type": "text", "text": "TRANSCRIPT", "cache_control": {"type": "ephemeral"}}
    assert second == {"type": "text", "text": "GUIDE"}
    assert "thinking" not in request and "temperature" not in request
    # $4/M in, $20/M out, $0.20/M cache read
    assert model.usage.cost == pytest.approx((1000 * 4 + 500 * 20 + 2000 * 0.2) / 1e6)


def test_models_without_server_fallback_do_not_send_it():
    messages = FakeMessages(response(model="claude-haiku-5-5"))
    ClaudeHighlightModel("claude-haiku-5-5", client=sdk_client(messages)).complete(
        "s", "c", "v", {}
    )
    assert "fallbacks" not in messages.requests[0] and "betas" not in messages.requests[0]
    messages = FakeMessages(response())
    ClaudeHighlightModel("claude-opus-5-5", fallbacks="off", client=sdk_client(messages)).complete(
        "s", "c", "v", {}
    )
    assert "fallbacks" not in messages.requests[0]


@pytest.mark.parametrize(
    "reply,match",
    [
        (response(stop="refusal"), "declined"),
        (response(stop="max_tokens"), "cut off"),
        (response(text="not json"), "valid JSON"),
    ],
)
def test_unusable_responses_become_invalid_output(reply, match):
    model = ClaudeHighlightModel("claude-opus-5-5", client=sdk_client(FakeMessages(reply)))
    with pytest.raises(InvalidOutput, match=match):
        model.complete("s", "c", "v", {})


def test_api_failures_are_reported_as_unavailable():
    import httpx2

    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com")
    )
    model = ClaudeHighlightModel("claude-opus-5-5", client=sdk_client(FakeMessages(error=error)))
    with pytest.raises(HighlightUnavailable, match="Could not reach"):
        model.complete("s", "c", "v", {})


def test_unknown_models_log_tokens_without_inventing_a_price():
    reply = response(model="claude-future-9")
    model = ClaudeHighlightModel("claude-future-9", client=sdk_client(FakeMessages(reply)))
    model.complete("s", "c", "v", {})
    assert model.usage.cost is None and "cost unknown" in model.usage.summary()


# ---------- worker integration (local mode, mocked LLM) ----------


class FixtureTranscriber:
    key = "fixture"

    def transcribe(self, source, on_progress=None):
        return {
            "language": "en",
            "duration": DURATION,
            "model": "fixture",
            "segments": [
                {
                    "start": s["start"],
                    "end": s["end"],
                    "text": s["text"],
                    "words": [
                        {"text": w["w"], "start": w["s"], "end": w["e"], "probability": 0.9}
                        for w in s["words"]
                    ],
                }
                for s in SEGMENTS
            ],
        }


@pytest.fixture
def hl_client(database, hl_settings, clock):
    with TestClient(create_app(database=database, settings=hl_settings, clock=clock)) as c:
        yield c


def run_worker(database, settings, clock, llm):
    worker = ProcessingWorker(database, settings, FixtureTranscriber(), clock, lambda: llm)
    while worker.run_once():
        pass


def start(client, headers):
    project = uploaded(client, headers, durationSec=DURATION)
    client.post(f"/v1/projects/{project}/process", headers=headers, json=PARAMS)
    return project


def test_worker_stores_llm_highlights_as_suggestions(hl_client, database, hl_settings, clock):
    headers = login(hl_client)
    project = start(hl_client, headers)
    llm = FakeLLM({"candidates": [candidate(5.85, 36.46, 8.6), candidate(42.2, 66.9, 7.2)]})
    run_worker(database, hl_settings, clock, llm)
    status = hl_client.get(f"/v1/projects/{project}/status", headers=headers).json()
    assert status["status"] == "ready", status
    clips = hl_client.get(f"/v1/projects/{project}/clips", headers=headers).json()
    assert clips["promptVersion"] == PROMPT_VERSION and clips["modelName"] == "claude-opus-5-5"
    assert [(c["rank"], c["combined"], c["scores"]) for c in clips["clips"]] == [
        (1, 86, None),
        (2, 72, None),
    ]
    assert all(20 <= c["end"] - c["start"] <= 40 for c in clips["clips"])
    assert rows(database, AnalysisRunRecord)[-1]["goal"] == "educational"
    # Captions for the chosen highlight come from the same stored transcript.
    clip = clips["clips"][0]
    track = hl_client.get(f"/v1/projects/{project}/clips/{clip['id']}/captions", headers=headers)
    assert track.json()["chunks"][0]["words"][0]["text"] == "Most"


def test_worker_reports_no_highlights_without_retry(hl_client, database, hl_settings, clock):
    headers = login(hl_client)
    project = start(hl_client, headers)
    run_worker(database, hl_settings, clock, FakeLLM({"candidates": []}))
    status = hl_client.get(f"/v1/projects/{project}/status", headers=headers).json()
    assert (status["status"], status["failureCode"], status["canRetry"]) == (
        "failed",
        "NO_HIGHLIGHTS",
        False,
    )
    assert_error(
        hl_client.post(f"/v1/projects/{project}/retry", headers=headers), 409, "INVALID_STATE"
    )


def test_worker_marks_llm_outages_retryable(hl_client, database, hl_settings, clock):
    headers = login(hl_client)
    project = start(hl_client, headers)
    run_worker(database, hl_settings, clock, FakeLLM(HighlightUnavailable("down")))
    status = hl_client.get(f"/v1/projects/{project}/status", headers=headers).json()
    assert (status["failureCode"], status["canRetry"]) == ("ANALYSIS_FAILED", True)
    # Retrying re-runs analysis only: the transcript is kept, so nothing is re-transcribed.
    hl_client.post(f"/v1/projects/{project}/retry", headers=headers)
    run_worker(database, hl_settings, clock, FakeLLM({"candidates": [candidate(5.85, 36.46)]}))
    assert (
        hl_client.get(f"/v1/projects/{project}/status", headers=headers).json()["status"] == "ready"
    )


def test_missing_credentials_are_reported_as_unavailable_not_a_crash():
    error = TypeError(
        '"Could not resolve authentication method. Expected one of api_key, auth_token"'
    )
    model = ClaudeHighlightModel("claude-opus-5-5", client=sdk_client(FakeMessages(error=error)))
    with pytest.raises(HighlightUnavailable, match="ANTHROPIC_API_KEY"):
        model.complete("s", "c", "v", {})
    unrelated = ClaudeHighlightModel(
        "claude-opus-5-5", client=sdk_client(FakeMessages(error=TypeError("bug")))
    )
    with pytest.raises(TypeError):
        unrelated.complete("s", "c", "v", {})
