# 0001 — Local transcription and karaoke captions

Date: 2026-10-27

## Context

Processing was a clock-driven simulator. The spec asks for faster-whisper transcription, word-highlighted captions in three presets, and SRT/ASS export, with no Whisper or FFmpeg work inside HTTP handlers.

## Decisions

- **Worker, not request ticks.** `app.jobs.worker` claims jobs with a renewable lease and runs Whisper outside DB transactions. The simulator stays available as `PROCESSING_MODE=demo` (used by the API tests).
- **One transcript per source file.** Transcripts are cached by the source file's SHA-256 and the transcriber options, and are deleted with the last transcript that refers to them.
- **Device.** CUDA when present, else CPU int8. CTranslate2 has no Apple-silicon GPU backend. WhisperX alignment is an opt-in extra because it pulls in PyTorch.
- **No speech** is a terminal, non-retryable `NO_SPEECH` failure with an explanation, not an empty project.
- **Captions are a per-clip track**, derived from the transcript until edited, then stored. Presets are visual only; chunking rules are global, so style changes never re-chunk edits.
- **Caption timing edits are allowed** (nudges and retimed words). This deliberately departs from spec 7.20 ("no manual timing in MVP") at the product owner's request. Text edits that change the word count spread words over the chunk's original span and mark them `estimated`, rather than inventing timing elsewhere.
- **Framing.** Renders default to a center crop; the previous full-frame fit stays available as `framing: "fit"`.
- **Rendering stays synchronous** in `POST /render` for now: a 20–40 s clip renders in about a second. Moving it to the worker is a follow-up.
- **Fonts are bundled** (Montserrat, OFL) and passed to libass via `fontsdir`, because the bundled FFmpeg has no fontconfig.
