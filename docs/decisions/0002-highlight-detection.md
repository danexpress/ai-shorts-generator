# 0002 — LLM highlight detection

Date: 2026-10-27

## Context

The analysis stage ranked canned demo moments. The product owner asked for the most engaging 20–40 s windows of a real transcript, chosen by the Claude API, validated, snapped to sentence boundaries, retried once on invalid output, windowed for long videos, and optionally re-ranked by loudness and scene changes.

## Decisions

- **LLM highlights replace the demo analyzer in the analysis stage** and are stored as ordinary analysis runs, so the existing suggestion UI is where the creator picks one. `HIGHLIGHT_MODEL=demo` keeps the old analyzer for keyless local development.
- **Structured outputs plus local validation.** The JSON schema guarantees shape; ranges, in-video timestamps and boundaries are still validated locally because model output is untrusted.
- **Single 0–10 score.** It is stored as `combined = score × 10`; demo sub-scores became optional in the Clip contract.
- **Snapping prefers whole sentences** and keeps the model's start (the hook), adjusting length at the end first. Word boundaries are a fallback for run-on speech.
- **Retry by re-issuing** the request with the problems appended after the cached transcript, rather than replaying the assistant turn. This avoids replaying thinking blocks and keeps the cache prefix stable.
- **Windows overlap by 45 s.** That is longer than the longest clip; overlap is given up only next to a single sentence too large to share a window. Multi-window results get one comparative re-rank request.
- **Signals use FFmpeg's `ebur128` and `scdet`** (already bundled) instead of PySceneDetect/OpenCV, and are capped at ±1.25 points so content judgement dominates.
- **Server-side refusal fallback on by default** for models that support it, per Anthropic's guidance; it can be turned off.
- **"No highlights" is terminal and non-retryable**; API outages are retryable.
