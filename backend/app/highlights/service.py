"""Find the most engaging 20-40 s moments of a transcript with an LLM."""

import logging

from . import prompt
from .transcript import format_lines, sentences, windows
from .validate import InvalidOutput, dedupe, parse_candidates, snap

logger = logging.getLogger(__name__)


class NoHighlights(Exception):
    """The video has no moment that can become a Short (not worth retrying)."""


class HighlightFailed(Exception):
    """The model did not produce usable output after one retry."""


def _transcript_block(units: list[dict], duration: float) -> str:
    return f'<transcript duration_seconds="{duration:.2f}">\n{format_lines(units)}\n</transcript>'


def _ask(llm, system: str, cached: str, guidance: str, accept) -> list[dict]:
    """Call the model; on unusable output retry once, telling it what was wrong."""
    problems = None
    for attempt in (1, 2):
        variable = guidance + (
            "\n\n" + prompt.RETRY.format(problems="\n".join(problems)) if problems else ""
        )
        try:
            result, issues = accept(llm.complete(system, cached, variable, schema=accept.schema))
        except InvalidOutput as exc:
            result, issues = None, [str(exc)]
        if result is not None:
            if issues:
                logger.info("Dropped invalid highlight candidates: %s", "; ".join(issues))
            return result
        problems = issues
        logger.warning("Highlight output rejected (attempt %d): %s", attempt, "; ".join(issues))
    raise HighlightFailed("; ".join(problems))


def _candidate_acceptor(units, duration, settings):
    def accept(data):
        parsed, issues = parse_candidates(data, duration)
        snapped = []
        for item in parsed:
            bounds = snap(
                item["start"],
                item["end"],
                units,
                settings.highlight_min_seconds,
                settings.highlight_max_seconds,
                duration,
            )
            if bounds is None:
                issues.append(
                    f"{item['start']:.2f}–{item['end']:.2f}s cannot form a "
                    f"{settings.highlight_min_seconds:g}–{settings.highlight_max_seconds:g}s "
                    "moment of whole lines."
                )
            else:
                snapped.append({**item, "start": bounds[0], "end": bounds[1]})
        # An empty list is a valid answer ("no usable moment"); errors alone are not.
        if snapped or not issues:
            return snapped, issues
        return None, issues

    accept.schema = prompt.candidate_schema()
    return accept


def _rerank(llm, candidates: list[dict], units: list[dict], count: int) -> list[dict]:
    """Final pass across windows: let the model compare all candidates side by side."""
    by_id = {f"m{i}": c for i, c in enumerate(candidates, 1)}
    blocks = []
    for key, c in by_id.items():
        text = " ".join(u["text"] for u in units if c["start"] - 0.5 <= u["start"] < c["end"])
        blocks.append(
            f'<candidate id="{key}" start="{c["start"]:.2f}" end="{c["end"]:.2f}">\n{text}\n</candidate>'
        )

    def accept(data):
        ranking = data.get("ranking") if isinstance(data, dict) else None
        if not isinstance(ranking, list):
            return None, ['Expected a JSON object with a "ranking" array.']
        result, issues, seen = [], [], set()
        for item in ranking:
            key = item.get("id") if isinstance(item, dict) else None
            score = item.get("score") if isinstance(item, dict) else None
            if key not in by_id or key in seen:
                issues.append(f"Unknown or repeated id {key!r}.")
                continue
            if not isinstance(score, int | float) or not 0 <= score <= 10:
                issues.append(f"{key} needs a score between 0 and 10.")
                continue
            seen.add(key)
            hook = item.get("hook") if isinstance(item.get("hook"), str) else ""
            reason = item.get("reason") if isinstance(item.get("reason"), str) else ""
            result.append(
                {
                    **by_id[key],
                    "score": float(score),
                    "hook": hook.strip()[:120] or by_id[key]["hook"],
                    "reason": reason.strip()[:500] or by_id[key]["reason"],
                }
            )
        return (result, issues) if result else (None, issues or ["No candidates were ranked."])

    accept.schema = prompt.ranking_schema()
    try:
        return _ask(
            llm,
            prompt.RERANK_SYSTEM.format(count=count),
            "\n".join(blocks),
            f"Rank up to {count} candidates.",
            accept,
        )
    except HighlightFailed:
        logger.warning("Re-rank pass failed; keeping per-window scores")
        return sorted(candidates, key=lambda c: -c["score"])


def find_highlights(
    segments: list[dict],
    duration: float,
    params: dict,
    llm,
    settings,
    signals: dict | None = None,
) -> list[dict]:
    """Ranked candidates [{start, end, hook, reason, score[, signals]}], best first."""
    units = sentences(segments)
    count = settings.highlight_candidates
    if not units or units[-1]["end"] - units[0]["start"] < settings.highlight_min_seconds:
        raise NoHighlights("There is not enough speech for a Short.")
    system = prompt.SYSTEM.format(
        min_seconds=settings.highlight_min_seconds,
        max_seconds=settings.highlight_max_seconds,
        count=count,
    )
    guidance = prompt.guidance_text(
        params, count, settings.highlight_min_seconds, settings.highlight_max_seconds
    )
    accept = _candidate_acceptor(units, duration, settings)
    parts = windows(units, settings.highlight_window_tokens, settings.highlight_max_seconds + 5)
    candidates, failures = [], 0
    for part in parts:
        try:
            candidates += _ask(llm, system, _transcript_block(part, duration), guidance, accept)
        except HighlightFailed:
            if len(parts) == 1:
                raise
            failures += 1  # keep the other windows' moments
    if failures == len(parts):
        raise HighlightFailed("No window produced usable highlights.")
    candidates = dedupe(candidates, limit=count * len(parts))
    if len(parts) > 1 and len(candidates) > 1:
        candidates = _rerank(llm, candidates, units, count)
    if signals:
        from .signals import rerank

        candidates = rerank(candidates, signals, duration)
    final = dedupe(candidates, limit=count)
    if not final:
        raise NoHighlights("No moment in this video works as a standalone Short.")
    logger.info(
        "Found %d highlight(s) in %d window(s); LLM usage: %s",
        len(final),
        len(parts),
        llm.usage.summary() if hasattr(llm, "usage") else "n/a",
    )
    return final
