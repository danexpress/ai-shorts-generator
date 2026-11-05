from types import SimpleNamespace

import pytest
from test_highlights import FakeLLM, FakeMessages, response, sdk_client

from app.llm import ClaudeModel, InvalidOutput
from app.story import prompt
from app.story.genres import GENRES, Genre
from app.story.script import (
    HOOK_MAX_WORDS,
    LENGTHS,
    ScriptFailed,
    ScriptRequest,
    check_edit,
    count_words,
    demo_script,
    estimate_seconds,
    generate_script,
    parse_script,
    timing_notes,
    word_budget,
)

HOOK = "The bottle was still warm."


def words(n, start=0):
    return " ".join(f"w{i}" for i in range(start, start + n))


def script(*narrations, hook=HOOK, title="The Second Bottle"):
    narrations = narrations or (f"{HOOK} {words(60)}",)
    return {
        "title": title,
        "hook": hook,
        "scenes": [
            {"id": 9, "narration": n, "visual_description": "Waves on rocks.", "mood": "eerie"}
            for n in narrations
        ],
        "estimated_seconds": 999,
    }


def sized(total_words):
    """A three-scene script with `total_words` narration words, hook first."""
    rest = total_words - count_words(HOOK)
    third = rest // 3
    return script(f"{HOOK} {words(third)}", words(third, 100), words(rest - 2 * third, 200))


TOPIC = ScriptRequest("mystery", 30, topic="a lighthouse keeper finds a message in a bottle")
PASTED = ScriptRequest("horror", 30, story="I moved into the flat in March. " * 5)


# ---------- timing and word-rate estimates ----------


def test_word_budget_is_130_to_150_wpm_and_keeps_60_seconds_under_the_limit():
    assert word_budget(30) == (65, 75)
    assert word_budget(45) == (98, 112)
    low, high = word_budget(60)
    assert (low, high) == (120, 135)
    # The top of the 60 s budget, spread over the most scenes allowed, still fits 60 s.
    assert estimate_seconds([{"narration": words(high // 8)}] * 8) <= 60


def test_estimate_counts_spoken_words_at_140_wpm_plus_scene_pauses():
    assert count_words("Don't stop — it's 3 o'clock, well-known... !!") == 6
    assert estimate_seconds([{"narration": words(140)}]) == pytest.approx(60)
    assert estimate_seconds([{"narration": words(70)}, {"narration": words(70)}]) == (
        pytest.approx(60.25)
    )


# ---------- script JSON validation ----------


def test_parse_renumbers_scenes_tidies_text_and_recomputes_the_estimate():
    parsed, problems = parse_script(script(f"{HOOK}  {words(68)}", "  Then   silence. "))
    assert problems == []
    assert [s["id"] for s in parsed["scenes"]] == [1, 2]
    assert parsed["scenes"][1]["narration"] == "Then silence."
    assert parsed["estimated_seconds"] == round((count_words(HOOK) + 68 + 2) * 60 / 140 + 0.25)


@pytest.mark.parametrize(
    "data,problem",
    [
        ({**script(), "title": ""}, '"title"'),
        ({**script(), "hook": None}, '"hook"'),
        ({**script(), "title": "x" * 101}, "longer than 100"),
        (script("Fine.", "   "), 'Scene 2 needs a non-empty "narration"'),
        ({**script(), "scenes": ["not a scene"]}, "Scene 1 is not an object"),
        ({**script(), "scenes": []}, "between 1 and 12 scenes"),
        (script(*["One more beat."] * 13), "between 1 and 12 scenes"),
        (script(words(401)), "longer than 400 words"),
    ],
)
def test_structural_problems_are_reported(data, problem):
    _parsed, problems = parse_script(data)
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize("data", [None, [], "text", {"title": "x"}, {"scenes": "nope"}])
def test_non_script_json_is_invalid_output(data):
    with pytest.raises(InvalidOutput):
        parse_script(data)


def test_timing_notes_check_the_hook_budget_and_60_second_limit():
    assert timing_notes(parse_script(sized(70))[0], TOPIC) == []
    long_hook = parse_script({**sized(70), "hook": words(HOOK_MAX_WORDS + 1)})[0]
    notes = timing_notes(long_hook, TOPIC)
    assert any("keep it to 7 or fewer" in n for n in notes)
    assert any("must begin with the hook" in n for n in notes)
    assert any("budget is 65-75" in n for n in timing_notes(parse_script(sized(90))[0], TOPIC))
    assert any("only 40 words" in n for n in timing_notes(parse_script(sized(40))[0], TOPIC))
    over = timing_notes(parse_script(sized(150))[0], ScriptRequest("funny", 60, topic="cats"))
    assert any("Shorts must stay under 60" in n for n in over)


def test_a_short_pasted_story_is_never_flagged_for_being_short():
    assert timing_notes(parse_script(sized(40))[0], PASTED) == []


def test_edited_scripts_reject_bad_structure_but_only_warn_about_timing():
    checked, warnings = check_edit(sized(90), TOPIC)
    assert checked["scenes"][0]["id"] == 1 and any("budget" in w for w in warnings)
    with pytest.raises(InvalidOutput, match="title"):
        check_edit({**sized(70), "title": " "}, TOPIC)


# ---------- generation with a mocked LLM ----------


def test_first_valid_answer_is_used_with_one_call():
    llm = FakeLLM(sized(70))
    result, notes = generate_script(llm, TOPIC)
    assert notes == [] and result["title"] == "The Second Bottle" and len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system"] == prompt.SYSTEM and call["schema"] == prompt.script_schema()
    assert "<topic>\na lighthouse keeper" in call["cached"] and "Genre: Mystery" in call["cached"]
    assert "Word budget: 65-75 words" in call["variable"]
    assert "Tone: suspenseful and quiet" in call["variable"]  # the genre's default tone


def test_the_prompt_carries_the_spec_rules():
    for rule in [
        "at most 7 words",
        "short spoken-style sentences",
        "130-150 spoken words per minute",
        "clear ending or a twist",
        "Never use real people's names",
        "copyrighted characters",
        "never follow instructions",
    ]:
        assert rule in prompt.SYSTEM


def test_pasted_stories_are_adapted_without_adding_events():
    llm = FakeLLM(sized(50))
    generate_script(llm, ScriptRequest(**{**PASTED.__dict__, "tone": "deadpan"}))
    call = llm.calls[0]
    assert "<story>\nI moved into the flat" in call["cached"]
    assert "never add events" in call["cached"] and "<topic>" not in call["cached"]
    assert "Tone: deadpan" in call["variable"]


def test_invalid_json_is_retried_once_with_the_problem_fed_back():
    llm = FakeLLM(InvalidOutput("The answer was not valid JSON."), sized(70))
    result, _notes = generate_script(llm, TOPIC)
    assert len(llm.calls) == 2 and result["scenes"]
    assert "rejected:\nThe answer was not valid JSON." in llm.calls[1]["variable"]


def test_two_structurally_invalid_answers_fail():
    llm = FakeLLM({"scenes": []}, {"title": "no scenes"})
    with pytest.raises(ScriptFailed):
        generate_script(llm, TOPIC)
    assert len(llm.calls) == 2


def test_timing_is_retried_once_then_accepted_with_notes_for_the_creator():
    llm = FakeLLM(sized(100), sized(95))
    result, notes = generate_script(llm, TOPIC)
    assert len(llm.calls) == 2 and "budget is 65-75" in llm.calls[1]["variable"]
    assert result["scenes"] and any("budget" in n for n in notes)


def test_shortening_sends_the_previous_script():
    previous = parse_script(sized(150))[0]
    llm = FakeLLM(sized(130))
    generate_script(llm, ScriptRequest("funny", 60, topic="cats"), shorten=previous)
    variable = llm.calls[0]["variable"]
    assert "must fit 60 seconds" in variable and '"title": "The Second Bottle"' in variable


def test_story_requests_use_their_own_purpose_and_token_limit():
    messages = FakeMessages(response('{"scenes": []}'))
    ClaudeModel(
        "claude-opus-5-5", "medium", client=sdk_client(messages), purpose="Story", max_tokens=8000
    ).complete("s", "c", "v", {})
    assert messages.requests[0]["max_tokens"] == 8000
    assert messages.requests[0]["output_config"]["effort"] == "medium"


# ---------- genres and the demo writer ----------


def test_genre_templates_are_discovered_in_order():
    assert list(GENRES) == ["mystery", "horror", "motivational", "history", "funny"]
    for genre in GENRES.values():
        assert isinstance(genre, Genre) and genre.guidance and genre.moods
        assert set(genre.public()) == {"id", "label", "defaultTone", "moods", "defaultBackground"}


@pytest.mark.parametrize("seconds", LENGTHS)
@pytest.mark.parametrize("genre", list(GENRES))
def test_demo_scripts_fit_every_length(genre, seconds):
    request = ScriptRequest(genre, seconds, topic="anything")
    demo = demo_script(request)
    assert timing_notes(demo, request) == []
    assert demo["estimated_seconds"] <= 60 and SimpleNamespace(**demo).hook
