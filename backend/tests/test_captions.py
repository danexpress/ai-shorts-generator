import copy
import itertools
import json
import re
from pathlib import Path

import pytest
from conftest import assert_error, login, ready

from app.captions.chunking import (
    CaptionEditError,
    ChunkOptions,
    apply_min_duration,
    build_chunks,
    clip_words,
    is_filler,
    layout,
    normalize_chunks,
)
from app.captions.formats import (
    ass_color,
    ass_time,
    build_ass,
    build_srt,
    chunk_lines,
    display,
    escape_ass,
    fit_scale,
    srt_time,
)
from app.captions.measure import text_width
from app.captions.presets import CANVAS, FONTS_DIR, PRESETS, SAFE_ZONE, max_text_width

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "podcast_transcript.json").read_text())
SEGMENTS = FIXTURE["segments"]


def texts(chunks):
    return [[w["text"] for w in c["words"]] for c in chunks]


# ---------- chunking ----------


def test_fixture_chunks_follow_the_reading_rules():
    chunks = build_chunks(SEGMENTS, 10.0, 22.0)
    assert texts(chunks) == [
        ["So,"],
        ["most", "founders", "hire"],
        ["backwards,"],
        ["right?"],
        ["They", "hire", "for"],
        ["the", "work", "they"],
        ["have", "today"],
        ["and", "not", "tomorrow."],
        ["Internationalization", "matters."],
        ["Write", "the", "decisions"],
        ["down", "first."],
    ]
    for chunk in chunks:
        words = [w["text"] for w in chunk["words"]]
        assert 1 <= len(words) <= 3
        lines = layout(words, 15, 2)
        assert lines is not None and len(lines) <= 2
        assert all(len(" ".join(line)) <= 15 or len(line) == 1 for line in lines)
        # Punctuation only ever ends a chunk.
        assert not any(re.search(r"[.,!?]$", w) for w in words[:-1])
        assert chunk["end"] - chunk["start"] >= 0.3 - 1e-9
    for previous, current in itertools.pairwise(chunks):
        assert previous["end"] <= current["start"]


def test_words_are_never_split_or_reordered():
    words = clip_words(SEGMENTS, 10.0, 22.0)
    kept = [w["text"] for w in words if not is_filler(w["text"])]
    assert [w for chunk in texts(build_chunks(SEGMENTS, 10.0, 22.0)) for w in chunk] == kept


def test_fillers_are_removed_by_default_and_toggleable():
    default = [w for c in texts(build_chunks(SEGMENTS, 10.0, 17.3)) for w in c]
    kept = [
        w
        for c in texts(build_chunks(SEGMENTS, 10.0, 17.3, ChunkOptions(remove_fillers=False)))
        for w in c
    ]
    assert "um," not in default and "uh" not in default
    assert "um," in kept and "uh" in kept
    assert is_filler("Um,") and is_filler("uh...") and not is_filler("umbrella")


def test_pause_starts_a_new_chunk():
    chunks = build_chunks(SEGMENTS, 13.5, 17.3)
    assert ["have", "today"] in texts(chunks)  # 0.8s pause before "and"
    tight = build_chunks(SEGMENTS, 13.5, 17.3, ChunkOptions(pause=1.0))
    assert ["have", "today", "and"] in texts(tight)


def test_chunk_timing_spans_its_words_with_a_minimum_duration():
    chunks = build_chunks(SEGMENTS, 10.0, 22.0)
    by_text = {" ".join(t): c for t, c in zip(texts(chunks), chunks)}
    assert (by_text["most founders hire"]["start"], by_text["most founders hire"]["end"]) == (
        10.7,
        11.65,
    )
    assert by_text["So,"]["end"] == 10.3  # 0.25s word extended to 0.3s
    assert by_text["Internationalization matters."]["end"] == 19.0


def test_min_duration_never_overlaps_neighbours():
    chunks = [
        {"start": 1.0, "end": 1.1, "words": []},
        {"start": 1.2, "end": 1.25, "words": []},
        {"start": 1.3, "end": 2.0, "words": []},
    ]
    apply_min_duration(chunks, 0.3, 0.0, 3.0)
    assert chunks[0] == {"start": 0.9, "end": 1.2, "words": []}
    # Squeezed between neighbours, it takes all the room there is.
    assert (chunks[1]["start"], chunks[1]["end"]) == (1.2, 1.3)


def test_clip_words_use_midpoints_and_clamp_to_the_clip():
    words = clip_words(SEGMENTS, 10.8, 11.6)
    assert [w["text"] for w in words] == ["most", "founders", "hire"]
    assert words[0]["start"] == 10.8 and words[-1]["end"] == 11.6


def test_segment_timing_is_split_and_marked_estimated():
    words = clip_words(SEGMENTS, 20.0, 22.0)
    assert [w["text"] for w in words] == ["Write", "the", "decisions", "down", "first."]
    assert all(w["estimated"] for w in words)
    assert words[0]["start"] == 20.0 and words[-1]["end"] == pytest.approx(22.0)


def test_layout_balances_lines_and_never_splits_words():
    assert layout(["hire", "for", "the"], 15, 2) == [["hire", "for", "the"]]
    assert layout(["most", "founders", "hire"], 15, 2) == [["most"], ["founders", "hire"]]
    assert layout(["Internationalization"], 15, 2) == [["Internationalization"]]
    assert layout(["Internationalization", "is", "hard"], 15, 1) is None
    assert layout([], 15, 2) == []


def test_edited_chunks_are_validated_and_retimed():
    edited = normalize_chunks(
        [
            {"words": [{"text": " Most ", "start": 10.7, "end": 10.95}]},
            {"words": []},
            {"words": [{"text": "founders", "start": 11.0, "end": 11.1}]},
        ],
        10.0,
        12.0,
    )
    assert texts(edited) == [["Most"], ["founders"]]
    assert [c["id"] for c in edited] == ["c0", "c1"]
    assert (edited[1]["start"], edited[1]["end"]) == (11.0, 11.3)
    for bad, message in [
        ([{"text": "x", "start": 9.0, "end": 9.5}], "outside the clip"),
        ([{"text": "x", "start": 11.0, "end": 11.0}], "end after it starts"),
        (
            [{"text": "a", "start": 11.0, "end": 11.5}, {"text": "b", "start": 11.2, "end": 11.6}],
            "overlaps",
        ),
    ]:
        with pytest.raises(CaptionEditError, match=message):
            normalize_chunks([{"words": bad}], 10.0, 12.0)


# ---------- ASS / SRT ----------


def test_color_and_time_formats():
    assert ass_color("#FFE500") == "&H0000E5FF"
    assert ass_color("#000000", 0.6) == "&H66000000"
    assert ass_time(3725.456) == "1:02:05.46"
    assert srt_time(3725.456) == "01:02:05,456"
    assert escape_ass("{\\b1}x") == "(⧵b1)x"


def test_ass_highlights_each_word_while_it_is_spoken():
    chunks = build_chunks(SEGMENTS, 10.0, 12.0)
    ass = build_ass(chunks, PRESETS["clean-bold"], 10.0)
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == sum(len(c["words"]) for c in chunks)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Caption,Montserrat ExtraBold,124,&H00FFFFFF," in ass
    # "most founders hire": three events, times relative to the clip, contiguous.
    trio = [e for e in events if "most" in e]
    assert [e.split(",")[1:3] for e in trio] == [
        ["0:00:00.70", "0:00:00.95"],
        ["0:00:00.95", "0:00:01.40"],
        ["0:00:01.40", "0:00:01.65"],
    ]
    yellow = "\\1c&H00E5FF&"
    for event, word in zip(trio, ["most", "founders", "hire"]):
        assert f"{yellow}\\1a&H00&\\fscx100\\fscy100}}{word}" in event
        assert event.count(yellow) == 1
    assert "\\an5\\pos(540,1286)" in trio[0]
    assert "most\\Nfounders" in trio[0].replace("{\\1c&HFFFFFF&\\1a&H00&\\fscx100\\fscy100}", "")


def test_srt_export_is_plain_text_with_clip_relative_times():
    srt = build_srt(build_chunks(SEGMENTS, 10.0, 12.0), 10.0)
    assert srt.startswith(
        "1\n00:00:00,000 --> 00:00:00,300\nSo,\n\n2\n00:00:00,700 --> 00:00:01,650\n"
    )
    assert "most\nfounders hire\n" in srt


def test_every_preset_fits_the_safe_zone():
    worst = ["Internationalization", "WWWWWWW MMMMMMM"]
    for preset in PRESETS.values():
        assert (FONTS_DIR / preset.font_file).is_file()
        lines = [display(line, preset) for line in worst]
        scale = fit_scale(lines, preset) / 100
        widest = max(text_width(line, preset.font_file, preset.font_size) for line in lines)
        assert widest * scale * preset.active_scale / 100 <= max_text_width()
        block = 2 * preset.font_size * preset.line_height + 2 * preset.outline
        centre = CANVAS[1] * preset.position
        assert centre - block / 2 >= SAFE_ZONE["top"]
        assert centre + block / 2 <= CANVAS[1] - SAFE_ZONE["bottom"]


def test_overlong_edited_chunks_still_wrap():
    assert chunk_lines(["aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc", "dddd"], 15, 2) == [
        [0],
        [1],
        [2, 3],
    ]


# ---------- API ----------


def first_clip(client, headers, project):
    return client.get(f"/v1/projects/{project}/clips", headers=headers).json()["clips"][0]


def captions_url(project, clip):
    return f"/v1/projects/{project}/clips/{clip['id']}/captions"


def test_presets_are_listed(client, headers):
    presets = client.get("/v1/caption-presets", headers=headers).json()
    assert [p["id"] for p in presets] == ["clean-bold", "creator-pop", "minimal-highlight"]
    assert presets[0]["style"]["active_color"] == "#FFE500"


def test_caption_track_is_derived_then_edited_and_reset(client, headers, clock):
    project = ready(client, headers, clock)
    clip = first_clip(client, headers, project)
    url = captions_url(project, clip)
    track = client.get(url, headers=headers).json()
    assert (track["preset"], track["edited"], track["keepFillers"], track["stale"]) == (
        "clean-bold",
        False,
        False,
        False,
    )
    assert track["chunks"] and track["chunks"][0]["start"] >= clip["start"]

    # Fix a typo and nudge a chunk's start later; chunk timing is recomputed from words.
    chunks = copy.deepcopy([{"words": c["words"]} for c in track["chunks"]])
    chunks[0]["words"][0]["text"] = "Typo-fixed"
    chunks[1]["words"][0]["start"] += 0.05
    edited = client.patch(url, headers=headers, json={"chunks": chunks, "preset": "creator-pop"})
    assert edited.status_code == 200, edited.text
    edited = edited.json()
    assert edited["edited"] and edited["preset"] == "creator-pop"
    assert edited["chunks"][0]["words"][0]["text"] == "Typo-fixed"
    nudged = track["chunks"][1]["words"][0]["start"] + 0.05
    assert edited["chunks"][1]["words"][0]["start"] == pytest.approx(nudged, abs=1e-3)
    # The chunk still lasts at least 0.3s (pulled back into the previous gap if needed).
    assert edited["chunks"][1]["end"] - edited["chunks"][1]["start"] >= 0.3 - 1e-3
    assert client.get(url, headers=headers).json() == edited

    assert_error(
        client.patch(url, headers=headers, json={"keepFillers": True}), 409, "CAPTIONS_EDITED"
    )
    reset = client.patch(url, headers=headers, json={"keepFillers": True, "reset": True}).json()
    assert not reset["edited"] and reset["keepFillers"] and reset["preset"] == "creator-pop"

    overlap = [{"words": [{"text": "a", "start": clip["start"] + 1, "end": clip["start"] + 2}]}] * 2
    assert_error(client.patch(url, headers=headers, json={"chunks": overlap}), 422, "INVALID_INPUT")
    assert_error(client.patch(url, headers=headers, json={"preset": "neon"}), 422, "INVALID_INPUT")


def test_transcript_corrections_mark_saved_captions_stale(client, headers, clock):
    project = ready(client, headers, clock)
    clip = first_clip(client, headers, project)
    url = captions_url(project, clip)
    client.patch(url, headers=headers, json={"preset": "minimal-highlight"})
    transcript = client.get(f"/v1/projects/{project}/transcript", headers=headers).json()
    segment = transcript["segments"][0]
    client.patch(
        f"/v1/projects/{project}/transcript",
        headers=headers,
        json={"segments": [{"id": segment["id"], "text": segment["text"] + " Really."}]},
    )
    # Analysis is now stale but the clip still exists in the current run.
    assert client.get(url, headers=headers).json()["stale"] is True


def test_captions_require_ownership_and_a_current_clip(client, headers, clock):
    project = ready(client, headers, clock)
    clip = first_clip(client, headers, project)
    assert_error(
        client.get(f"/v1/projects/{project}/clips/clp_missing/captions", headers=headers),
        404,
        "NOT_FOUND",
    )
    stranger = login(client, "jon@example.com")
    assert_error(client.get(captions_url(project, clip), headers=stranger), 404, "NOT_FOUND")
    assert_error(
        client.patch(captions_url(project, clip), headers=stranger, json={}), 404, "NOT_FOUND"
    )


def test_render_burns_in_saved_captions_and_exports_srt_and_ass(
    client, headers, clock, monkeypatch
):
    from app.routers import renders

    calls = []

    def fake_render(source, destination, start, duration, resolution, ffmpeg, **options):
        calls.append(options)
        destination.write_bytes(b"rendered")

    monkeypatch.setattr(renders, "render_short", fake_render)
    project = ready(client, headers, clock)
    clip = first_clip(client, headers, project)
    url = captions_url(project, clip)
    track = client.get(url, headers=headers).json()
    chunks = [{"words": c["words"]} for c in track["chunks"]]
    chunks[0]["words"][0]["text"] = "Edited"
    client.patch(url, headers=headers, json={"chunks": chunks})

    result = client.post(
        f"/v1/projects/{project}/render", headers=headers, json={"clipId": clip["id"]}
    ).json()
    assert (result["captions"], result["captionPreset"], result["framing"]) == (
        True,
        "clean-bold",
        "crop",
    )
    assert calls[-1]["framing"] == "crop" and calls[-1]["fonts_dir"] == FONTS_DIR
    assert "}Edited{" in calls[-1]["captions_ass"]

    downloads = client.get(f"/v1/projects/{project}/downloads", headers=headers).json()
    exports = {d["kind"]: d for d in downloads if d.get("renderId") == result["id"]}
    assert set(exports) == {"mp4", "srt", "ass"}
    assert exports["srt"]["filename"] == result["filename"].replace(".mp4", ".srt")
    assert "Edited" in exports["srt"]["url"]

    plain = client.post(
        f"/v1/projects/{project}/render",
        headers=headers,
        json={"clipId": clip["id"], "captions": False, "framing": "fit", "resolution": 720},
    ).json()
    assert plain["captions"] is False and calls[-1]["captions_ass"] is None
    assert calls[-1]["framing"] == "fit"
    assert_error(
        client.post(
            f"/v1/projects/{project}/render",
            headers=headers,
            json={"clipId": clip["id"], "captionPreset": "neon"},
        ),
        422,
        "INVALID_INPUT",
    )
