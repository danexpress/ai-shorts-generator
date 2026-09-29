# AI Shorts Generator

## Product and Technical Specification — MVP v1.0

**Status:** Draft — implementation-ready scope  
**Date:** September 24, 2026  
**Audience:** Product, design, engineering, QA, security, and private-beta testers  
**Primary objective:** Turn long-form creator video into a publish-ready vertical Short with minimal editing.

---

## 1. Product summary

AI Shorts Generator is a creator-first web application that turns long-form video into short-form vertical clips for YouTube Shorts, TikTok, and Instagram Reels.

A creator uploads a video or, when URL import is enabled, supplies a supported public YouTube URL. The system transcribes the source, analyzes transcript and basic visual signals, recommends the strongest candidate moments, explains why they were selected, and lets the creator preview one candidate at a time. The creator can make lightweight text/style adjustments and render a polished 9:16 video with smart framing, animated captions, hook text, basic audio cleanup, a cover frame, and platform-specific posting copy.

The first user is the builder. The first external users are invited creators in a private beta.

### Core promise

> Give the system a long video. Get a small set of strong, understandable clip suggestions. Pick one. Receive a publish-ready Short without opening a traditional video editor.

### MVP design principle

The product is **not** a general-purpose video editor. It is an opinionated repurposing workflow:

```text
SOURCE VIDEO
     ↓
TRANSCRIBE + ANALYZE
     ↓
AI SUGGESTS MOMENTS
     ↓
CREATOR PICKS ONE
     ↓
LIGHTWEIGHT PREVIEW / TEXT EDITS
     ↓
ONE-CLICK RENDER
     ↓
PUBLISH-READY ASSETS
```

---

## 2. Goals

The MVP must:

1. Let an invited creator authenticate with Google.
2. Accept a supported video upload up to 3 hours.
3. Support an experimental YouTube URL import path when a compliant ingestion method is available.
4. Generate an editable transcript with word-level timestamps.
5. Let the creator choose an analysis goal: **Educational**, **Entertaining**, or **Viral**.
6. Accept an optional custom instruction and optional topics to avoid.
7. Analyze transcript plus basic visual signals and return 3–10 coherent clip suggestions based on source length.
8. Show separate scores for hook, value, standalone clarity, and visual quality.
9. Let the creator regenerate suggestions using a different goal without retranscribing the source.
10. Let the creator preview one suggested clip at a time.
11. Render a 9:16 Short with face-aware framing, word-highlighted captions, hook text, optional creator watermark, and basic audio cleanup.
12. Let the creator correct transcript/caption text without manually editing caption timing.
13. Support three caption presets.
14. Export 720p or 1080p MP4, defaulting to 1080p.
15. Generate a cover frame and allow the creator to choose an alternate frame from the clip.
16. Generate separate posting metadata for YouTube Shorts, TikTok, and Instagram Reels.
17. Export MP4, cover image, SRT, ASS, plain-text transcript, and timestamped transcript JSON.
18. Keep a basic project history.
19. Track a private-beta allowance of 60 processed source minutes per creator per month.
20. Recover cleanly from worker failures and avoid double-counting usage after retries.
21. Automatically expire media according to the retention policy.
22. Prevent one creator from accessing another creator's projects or assets.
23. Give the beta operator enough telemetry to understand failures, processing time, cost, and whether creators actually download outputs.

---

## 3. Non-goals for MVP

The following are intentionally excluded from the first release:

- Full timeline editing
- Manual frame-by-frame editing
- Manual caption-timing editing
- Arbitrary crop/keyframe editing
- Batch rendering of multiple clips
- Multiple simultaneous compute-intensive jobs per creator
- Direct publishing to YouTube
- Direct publishing to TikTok
- Direct publishing to Instagram
- Social scheduling/calendar features
- Performance analytics after publishing
- Paid subscriptions or billing
- Teams, workspaces, collaboration, or comments
- Public self-service signup
- Native mobile apps
- 4K export
- AI-generated B-roll
- Automatic stock-footage insertion
- Stock-music library
- AI-generated music
- Automatic sound effects
- Advanced voice isolation or mastering
- AI avatars
- Voice cloning
- Automatic dubbing
- Caption translation
- Multi-speaker split-screen layouts
- Full brand kits or custom-font upload
- User-created caption-template editor
- Complex version history
- Automatic stitching of unrelated source moments into one Short
- Guaranteed virality or predicted view counts

These can be considered only after the core workflow reliably produces clips creators are willing to publish.

---

## 4. Users and roles

### 4.1 Creator

An invited, authenticated user who can:

- Sign in with Google.
- Create a project from a file upload.
- Use YouTube URL import when the beta feature is enabled.
- View only their own projects.
- Set an analysis goal.
- Provide optional custom guidance.
- Provide optional topics to avoid.
- Review and correct the transcript.
- Review AI clip suggestions and scores.
- Regenerate suggestions with a different goal.
- Preview one clip at a time.
- Edit hook text.
- Correct caption text.
- Switch among three caption presets.
- Enable or edit a creator name/handle watermark.
- Select 720p or 1080p export.
- Render one clip at a time.
- Re-render an existing selected clip with different presentation settings without re-analysis.
- Select an alternate cover frame.
- Download generated assets.
- View simple monthly usage.
- Delete a project and its assets.

A creator cannot:

- Invite new beta users unless explicitly granted operator access.
- Access another creator's project by changing an identifier.
- Raise their own monthly allowance.
- Bypass retention or file-size limits.
- Run more than one compute-intensive job at the same time.

### 4.2 Beta operator

An authenticated internal operator who can:

- Create or revoke private-beta invitations.
- View account-level status and usage.
- View job state, failure code, timing, and infrastructure diagnostics.
- Retry safe failed jobs.
- Disable a creator account when necessary.
- Adjust a creator's beta processing allowance.
- Inspect cost and reliability telemetry.
- Trigger or verify cleanup jobs.

The beta operator should **not** receive unrestricted access to creator video/transcript content merely because they are an operator. Support access to private content should be exceptional, auditable, and minimized.

---

## 5. Product principles and scope guardrails

### 5.1 Opinionated over infinitely configurable

The MVP should make good default choices rather than expose dozens of controls.

### 5.2 AI suggests; creator decides

The system recommends moments. The creator chooses what becomes a final Short.

### 5.3 Preserve source meaning

The system may tighten silence, filler, and obvious false starts, but must not intentionally change what the speaker meant.

### 5.4 Fail safely

If face tracking, filler removal, or another enhancement is uncertain, fall back to a simpler output instead of producing a visibly broken edit.

### 5.5 Reuse expensive work

Transcript, visual analysis, and clip-candidate results should be reusable for regeneration and re-rendering whenever possible.

### 5.6 Build for measurable cost

Every expensive stage should have measurable duration and estimated infrastructure/API cost.

### 5.7 One scope question

Whenever a new feature is proposed, ask:

> Does this materially improve the path from long video to a publish-ready Short?

If not, put it in the backlog.

---

## 6. Core user flows

### 6.1 Sign in and enter the beta

1. The creator opens the application.
2. The creator selects **Continue with Google**.
3. The system authenticates the Google identity.
4. The system checks whether the email/account has an active beta invitation.
5. Invited creators enter the dashboard.
6. Non-invited users receive a clear private-beta message; no project access is granted.

### 6.2 Create a project from an uploaded file

1. The creator selects **New project**.
2. The creator chooses **Upload video**.
3. The application validates file extension, media type, size, duration, and readable streams.
4. The creator sees source duration and an approximate processing-time estimate.
5. The creator selects one goal:
   - Educational
   - Entertaining
   - Viral
6. The creator may add a short custom instruction such as `Find clips about career advice`.
7. The creator may add topics to avoid.
8. The creator starts processing.
9. The source moves through the processing lifecycle.
10. When analysis is complete, the project becomes **Ready**.

### 6.3 Create a project from a YouTube URL

1. The creator selects **YouTube URL**.
2. The creator pastes a supported public YouTube URL.
3. The system verifies that the host and URL shape are supported.
4. The UI tells the creator that they are responsible for having the rights or permission necessary to reuse the content.
5. The creator confirms the rights notice.
6. If compliant ingestion is available and the media passes duration/size rules, processing continues.
7. If import is unavailable or fails, the application explains that the creator can upload the source file instead.

**Recommendation:** keep URL import behind a beta feature flag so it cannot block the core upload-first product.

### 6.4 Review transcript

1. The creator opens the transcript after transcription completes.
2. The system displays timestamped text.
3. The creator may correct words or punctuation.
4. The creator saves edits.
5. Clip analysis uses the latest saved transcript text while preserving the original timing map.

### 6.5 Review clip suggestions

1. The creator receives 3–10 suggestions based on source duration.
2. Each suggestion displays:
   - start time
   - end time
   - duration
   - proposed hook
   - Hook score
   - Value score
   - Standalone clarity score
   - Visual quality score
   - short reason for selection
3. The creator can preview a suggestion.
4. The creator may regenerate suggestions using another goal or guidance.
5. Regeneration reuses prior transcript/visual analysis where possible.

### 6.6 Preview and render

1. The creator chooses a suggestion.
2. The app produces a lightweight preview or preview representation.
3. The creator may:
   - edit the hook
   - correct caption text
   - switch caption preset
   - enable/edit watermark
   - choose 720p or 1080p
4. The creator selects **Render**.
5. The render worker creates the final output.
6. The app selects a recommended cover frame.
7. Platform-specific metadata is generated.
8. The project result screen displays downloads.

### 6.7 Re-render

1. The creator opens an existing rendered Short whose source analysis is still available.
2. The creator changes caption preset, hook, watermark, or resolution.
3. The creator selects **Re-render**.
4. The system reuses the clip timestamps and analysis.
5. It does not retranscribe or re-run clip discovery.

### 6.8 Delete a project

1. The creator selects **Delete project**.
2. The app explains that associated media and generated assets will be removed.
3. The creator confirms.
4. The system revokes access immediately.
5. Deletion is queued for all stored media and derived assets.
6. Minimal billing/usage/audit records may be retained where operationally necessary, but private content must not remain accessible through the product.

---

## 7. Functional requirements

### 7.1 Authentication and private-beta access

- Authentication uses Google OAuth.
- Request only the minimum OAuth scopes required for sign-in.
- An authenticated identity must also have an active invitation.
- Every creator-owned API request must authorize the creator against the requested project/resource.
- Authentication alone is not sufficient authorization.
- Sessions should use secure, HTTP-only cookies or another equivalent secure browser session mechanism.
- Logout invalidates the active application session.

### 7.2 Input formats and limits

Supported file containers for MVP:

- MP4
- MOV
- WebM

Supported input orientations:

- landscape
- portrait
- square

Recommended hard limits:

| Limit | MVP value |
| --- | ---: |
| Maximum source duration | 3 hours |
| Maximum source file size | 4 GB |
| Minimum useful source duration | 30 seconds |
| Final clip duration | 15–90 seconds |
| Preferred final clip duration | 30–60 seconds |
| Final aspect ratio | 9:16 |
| Export resolutions | 720p, 1080p |
| Default export resolution | 1080p |

- Validation must use actual media probing, not filename extension alone.
- Files with unreadable or unsupported video/audio streams must be rejected with a stable error code.
- Extremely large dimensions, frame rates, or malformed media must be rejected before expensive processing.

### 7.3 Upload behavior

#### Local development

Local file storage is acceptable for initial development.

#### Deployed beta

Large uploads should go **directly from browser to private object storage using short-lived signed upload credentials** rather than streaming multi-gigabyte files through the FastAPI process.

Recommended flow:

1. Client requests an upload session.
2. API verifies quota and creates a project in `draft/uploading` state.
3. API returns a short-lived signed upload target.
4. Browser uploads directly to private object storage.
5. Client notifies API that upload is complete.
6. API verifies object presence and queues media validation.

This keeps the web/API service responsive and avoids unnecessary bandwidth through the application server.

### 7.4 YouTube URL import

- URL import is a beta feature and may be disabled independently of file upload.
- Accept only explicitly supported YouTube hostnames and URL shapes.
- Do not implement a generic remote-file downloader.
- Apply strict network timeouts, file-size limits, media-duration limits, and redirect rules.
- Require the creator to confirm they have the necessary rights/permission.
- URL import failure must not prevent the creator from using file upload.
- No product copy may imply that a public URL automatically grants reuse rights.

### 7.5 Project states

Recommended user-visible project states:

- `draft`
- `uploading`
- `transcribing`
- `analyzing`
- `ready`
- `failed`
- `canceled`
- `expired`
- `deleted`

The UI may collapse internal details into the simpler progress labels previously chosen:

```text
Uploading → Transcribing → Analyzing → Ready
```

### 7.6 Job states

Background jobs should use explicit internal states:

- `queued`
- `running`
- `succeeded`
- `failed_retryable`
- `failed_terminal`
- `cancel_requested`
- `canceled`

Jobs must have:

- unique IDs
- project ID
- job type
- attempt number
- start/end timestamps
- stable failure code
- human-readable diagnostic message for operators
- idempotency key or equivalent duplicate-protection mechanism

### 7.7 Processing cancellation

- A creator may cancel before final rendering begins.
- Cancellation is best-effort for currently running external processes.
- The system must mark the job as cancel-requested immediately.
- Workers should check cancellation between expensive stages.
- Usage should include only source minutes actually processed according to the usage-accounting policy.
- Cancellation must not leave public or orphaned media objects.

### 7.8 Transcription

The system must:

- auto-detect spoken language
- generate segment timestamps
- generate word-level timestamps where supported
- store a plain-text representation
- store a structured timestamped representation
- expose transcript editing before clip analysis
- generate captions/metadata in the detected source language

Recommended implementation:

- `faster-whisper` behind a transcription-service interface

The transcription provider must be replaceable without changing the project API.

### 7.9 Transcript editing

- Creators can correct text and punctuation.
- MVP editing does not support changing word timings.
- The system preserves a mapping between edited text and original timing data.
- If an edit cannot be safely aligned to word timing, the system should use segment-level timing rather than invent timing.
- Saving transcript edits invalidates only downstream analysis artifacts that depend on transcript text; it should not force re-upload or re-transcription.

### 7.10 Silence, filler, and false-start cleanup

The MVP should be conservative.

Automatically:

- identify long silence/dead air
- exclude long silence from candidate selection
- trim long silence from final renders
- remove isolated filler words when edit boundaries are safe
- remove obvious isolated false starts/repetitions when cuts are safe

Fallback:

- if a filler cut would create an unnatural jump, audible click, or meaning change, preserve the source audio/video instead

The app should never silently rewrite the semantic content of the speaker.

### 7.11 Candidate generation

Candidate generation should use:

- transcript semantics
- sentence/segment boundaries
- silence boundaries
- face presence
- basic scene-change information
- speaker activity where reliably detectable
- visual stability/quality signals

The MVP does not require deep video-semantic understanding.

### 7.12 Number of suggestions

Recommended default:

| Source duration | Suggested clips |
| --- | ---: |
| < 10 min | 3 |
| 10–20 min | 4–5 |
| 20–40 min | 5–7 |
| 40–60 min | 7–10 |

The system may return fewer suggestions if it cannot find enough candidates above a minimum quality threshold. It should prefer fewer coherent clips over padding the list with weak moments.

### 7.13 Clip-boundary rules

Suggested clips must:

- start on or near a natural thought boundary
- avoid starting mid-word or mid-sentence
- avoid ending mid-thought
- remain one continuous interval from the source video
- avoid long intros before the key idea
- avoid trailing dead air
- add limited context before/after when necessary for comprehension
- be between 15 and 90 seconds
- target 30–60 seconds when possible

### 7.14 Analysis goals

#### Educational

Prioritize:

- useful explanations
- frameworks
- lessons
- tips
- actionable takeaways
- clear examples

#### Entertaining

Prioritize:

- humor
- personality
- stories
- surprising reactions
- memorable moments

#### Viral

Prioritize:

- immediate hooks
- curiosity
- surprise
- strong contrast
- emotionally engaging statements
- debate-worthy ideas present in the source

The system must not fabricate provocative claims that the source speaker did not make.

### 7.15 Custom guidance

A creator may provide one short instruction such as:

> Find clips about career advice.

The instruction affects:

- clip candidate selection
- hook generation
- descriptions/captions
- hashtags

A creator may also provide topics to avoid.

Input length should be bounded to prevent prompt abuse and unpredictable cost.

### 7.16 Clip ranking

Each suggestion exposes four scores from 0–100:

- Hook
- Value
- Standalone clarity
- Visual quality

Recommended internal default weighting:

| Score | Weight |
| --- | ---: |
| Hook | 30% |
| Value | 30% |
| Standalone clarity | 25% |
| Visual quality | 15% |

The weighting should be configuration, not hard-coded product logic.

Each suggestion should also contain a concise explanation of why it was selected.

### 7.17 Regeneration

Creators can regenerate suggestions with another analysis goal or updated guidance.

Regeneration should reuse:

- source metadata
- transcript
- timestamps
- scene/face analysis
- silence analysis

Regeneration should not re-upload, re-transcribe, or re-run expensive visual preprocessing unless an upstream artifact changed.

### 7.18 Preview

The MVP provides a lightweight preview before final rendering.

Preview must communicate:

- selected clip content
- approximate vertical framing
- hook text
- caption preset
- watermark

Preview need not provide frame-perfect output identical to the final encode if doing so would make previews as expensive as final renders.

### 7.19 Caption presets

The MVP includes exactly three caption presets.

Working names:

1. **Clean Bold**
2. **Creator Pop**
3. **Minimal Highlight**

Each preset defines:

- font family from app-controlled fonts
- size rules
- safe-area placement
- line wrapping
- background/outline treatment
- active-word emphasis
- hook style

Creators may switch among presets after preview.

### 7.20 Caption behavior

- Captions use word-level or phrase-level highlighting derived from word timestamps.
- Captions should be grouped into short readable phrases rather than showing one isolated word at a time when that harms readability.
- Caption positioning must avoid common lower-screen platform UI areas.
- Creators may correct caption text.
- Creators may not manually adjust timing in the MVP.

Exports:

- burned-in captions in final MP4
- SRT
- ASS

### 7.21 Hook/title overlay

- AI generates an initial hook for each suggestion.
- The creator may edit the hook before rendering.
- The hook appears at the beginning of the rendered Short.
- Recommended display duration: 2–5 seconds depending on text length.
- It then disappears.
- Hook style automatically follows the selected caption preset.
- Hook text must fit within a defined safe area and length limit.

### 7.22 Vertical framing

All final outputs are 9:16.

#### Face-aware case

- Detect and track the primary visible face.
- Smooth crop movement to avoid jitter.
- Keep the face inside a safe composition area rather than mechanically centering every frame.

#### Multiple-face case

- Prefer the active speaker when confidence is sufficiently high.
- If active-speaker confidence is low, use the most stable framing that keeps relevant faces visible.
- Do not create automatic split screen in MVP.

#### No-face case

- Use a smart center crop.
- Avoid rapid crop changes.
- Favor visual stability over speculative subject tracking.

### 7.23 Video rendering

Final output requirements:

- MP4
- H.264 video unless platform/deployment requirements justify another default
- AAC audio
- 9:16
- 720p or 1080p
- 1080p default
- balanced quality/speed encoder settings

Recommended render sizes:

- 1080 × 1920
- 720 × 1280

The application should analyze lower-resolution proxy frames where possible but render final output from the best available source media.

### 7.24 Audio

Perform basic enhancement:

- normalize output loudness/volume to a consistent target
- apply conservative background-noise reduction when it improves intelligibility
- preserve natural speech

Do not expose audio mastering controls in the MVP.

### 7.25 Watermark

Creators may optionally add:

- creator name, or
- creator handle

The watermark has product-controlled placement and styling.

Creators can save a default watermark.

### 7.26 Cover frame

- The system chooses a recommended frame from the selected clip.
- Prefer a frame with clear subject visibility and minimal motion blur.
- The creator may choose another frame sampled from the same clip.
- The creator cannot upload a separate custom image in MVP.
- The cover is downloadable as an image.

### 7.27 Platform metadata

Generate separate metadata for:

#### YouTube Shorts

- title
- description
- hashtags

#### TikTok

- caption
- hashtags

#### Instagram Reels

- caption
- hashtags

Metadata must consider:

- transcript content
- selected analysis goal
- custom instruction
- topics to avoid

Generated text must not invent factual claims not supported by the clip/source transcript.

### 7.28 Download package

Each completed render provides:

- final MP4
- cover image
- SRT
- ASS

At project level, provide:

- plain-text transcript
- timestamped transcript JSON

### 7.29 Project history

The dashboard lists recent projects with:

- title or source filename
- source type
- creation date
- source duration
- project state
- number of clip suggestions
- number of completed renders
- asset-expiration status

Search and advanced filtering are post-MVP.

### 7.30 Usage allowance

Private-beta allowance:

- 60 processed source minutes per creator per calendar month

Display simply:

> 34 of 60 processing minutes used this month

Usage should be recorded through an immutable or append-only usage ledger rather than relying only on a mutable counter.

A ledger entry should include:

- creator ID
- project ID
- job ID
- billable source seconds/minutes
- reason/type
- adjustment/reversal when needed
- created timestamp

### 7.31 Concurrent work limit

A creator may have one compute-intensive job running at a time.

Compute-intensive jobs include:

- transcription
- analysis
- preview generation when rendered server-side
- final rendering

Lightweight API reads/edits remain available while a job runs.

### 7.32 Retention and expiry

Recommended beta defaults:

| Asset | Retention |
| --- | --- |
| Original source media | 24 hours after analysis is complete or canceled |
| Temporary/proxy/intermediate media | <= 24 hours |
| Rendered Shorts | 7 days |
| Cover images and caption exports | 7 days |
| Transcript and analysis artifacts | 30 days |
| Project metadata/history shell | 90 days |
| Usage ledger / security audit records | Longer operational retention as required |

Creators may delete a project before automatic expiry.

Retention values should be configuration, not scattered constants.

### 7.33 Notifications

MVP uses in-app status/notification only.

No processing-complete email is required.

---

## 8. UX structure

### 8.1 Login

Show:

- product name
- short value proposition
- Google sign-in button
- private-beta notice

### 8.2 Dashboard

Show:

- **New project** action
- monthly processing usage
- active job/status if present
- recent projects
- asset-expiry indicator

### 8.3 New project

Show:

- Upload Video tab
- YouTube URL tab when enabled
- goal selector
- custom instruction
- topics-to-avoid field
- detected duration after source validation
- processing estimate
- remaining monthly allowance
- clear Start button

### 8.4 Processing view

Show:

- source name
- simple stage indicator
- estimated progress/status text
- cancel action when permitted
- errors with human-readable recovery action

Do not expose raw worker logs to creators.

### 8.5 Transcript view

Show:

- editable timestamped transcript
- Save edits
- Download TXT
- Download JSON
- Continue to suggestions

### 8.6 Suggestions view

Each clip card shows:

- cover/preview frame
- duration
- time range
- proposed hook
- four scores
- reason
- Preview
- Select

Also show:

- current analysis goal
- Regenerate action

### 8.7 Clip preview

Desktop recommendation:

- **Left:** vertical preview
- **Right:** hook text, caption preset, caption corrections, watermark, resolution
- **Bottom/primary action:** Render Short

Do not build a timeline.

### 8.8 Final result

Show:

- rendered Short
- selected cover
- alternate cover frames
- YouTube metadata
- TikTok metadata
- Instagram metadata
- download buttons
- Re-render action
- expiry date

### 8.9 Settings

Show only:

- default caption preset
- default watermark

Do not build a broad preference center in MVP.

---

## 9. Accessibility and compatibility

- Target WCAG 2.2 AA for standard application UI.
- All controls must be keyboard reachable.
- Controls require visible focus states and accessible names.
- Color cannot be the only indicator of score/status/error.
- Caption presets should maintain readable contrast.
- Video preview controls should use native-accessible patterns where practical.
- Support the latest two major versions of Chrome, Edge, Firefox, and Safari on desktop.
- Mobile viewing may be supported, but project creation/editing is best-effort in MVP.
- Desktop is the primary creation experience.

---

## 10. Permissions matrix

| Capability | Creator | Beta operator |
| --- | ---: | ---: |
| Sign in | Yes | Yes |
| Create own project | Yes | Optional |
| View own project | Yes | Yes, for own projects |
| View another creator's private content | No | No by default |
| Edit own transcript | Yes | No by default |
| Render own clip | Yes | No by default |
| Delete own project | Yes | No by default |
| View own usage | Yes | Yes |
| Create/revoke beta invite | No | Yes |
| Adjust beta allowance | No | Yes |
| View job diagnostics | No | Yes |
| Retry safe failed job | No | Yes |
| View platform-wide cost/reliability telemetry | No | Yes |
| Disable beta account | No | Yes |

Support access to creator content, if ever needed, must be an explicit audited workflow rather than an implicit operator permission.

---

## 11. Recommended architecture

### 11.1 High-level architecture

```text
                 ┌───────────────────┐
                 │ Next.js Web App  │
                 └─────────┬─────────┘
                           │ HTTPS
                           v
                 ┌───────────────────┐
                 │    FastAPI API    │
                 └──────┬─────┬──────┘
                        │     │
              metadata │     │ enqueue
                        v     v
               ┌──────────┐  ┌───────────┐
               │PostgreSQL│  │ Redis/Queue│
               └──────────┘  └─────┬─────┘
                                   │
                                   v
                            ┌──────────────┐
                            │ Media Worker │
                            └───┬───┬───┬──┘
                                │   │   │
                      ┌─────────┘   │   └─────────┐
                      v             v             v
                   FFmpeg     Transcription      LLM
                      │             │             │
                      └──────┬──────┴──────┬──────┘
                             │             │
                             v             v
                       Object Storage   Derived metadata
```

### 11.2 Client

Recommended:

- Next.js
- TypeScript
- Tailwind CSS

Responsibilities:

- authentication UX
- project dashboard
- direct upload orchestration
- transcript editor
- clip suggestion UI
- preview configuration
- download links
- in-app status updates

Polling is acceptable for MVP job status. WebSockets are not required.

### 11.3 API service

Recommended:

- Python
- FastAPI

Responsibilities:

- authentication/session validation
- authorization
- invitations
- project metadata
- signed upload/download URLs
- job orchestration
- transcript edits
- clip-suggestion APIs
- render configuration
- usage enforcement
- retention/deletion requests

The API process must not perform long-running FFmpeg or transcription work inside request handlers.

### 11.4 Background queue

Recommended initial stack:

- Redis
- Celery

Queue responsibilities:

- media validation
- transcription
- visual preprocessing
- clip analysis
- preview generation
- rendering
- thumbnail generation
- metadata generation
- cleanup/expiry

Each job type should be independently retryable where safe.

### 11.5 Media worker

Worker image/container contains:

- FFmpeg
- ffprobe
- transcription runtime
- OpenCV and/or MediaPipe
- caption rendering dependencies

The worker should run as a separate process/service from the API.

### 11.6 Database

Recommended:

- PostgreSQL

Store:

- users
- invitations
- projects
- job state
- transcript metadata
- clip suggestions
- render metadata
- preferences
- usage ledger
- audit events

Large media files do not belong in PostgreSQL.

Large transcript/analysis blobs may be stored in object storage with references in PostgreSQL if JSON size becomes operationally inconvenient.

### 11.7 Object storage

Development:

- local filesystem behind a storage interface

Deployed beta:

- private S3-compatible object storage
- Cloudflare R2 is a reasonable candidate

Requirements:

- bucket/object access is private
- creator downloads use short-lived signed URLs or authenticated proxying
- source/derived objects use opaque keys
- cleanup uses lifecycle jobs plus application-level verification

### 11.8 Transcription service

Recommended initial implementation:

- `faster-whisper`

Keep an interface such as:

```text
TranscriptionProvider.transcribe(media) -> TranscriptResult
```

so a managed transcription API can replace it later if economics/reliability warrant.

### 11.9 AI analysis service

Use an LLM that supports structured JSON output.

The application should send:

- transcript windows
- timing information
- compact derived visual signals
- creator goal/guidance

Avoid sending raw video frames to the LLM unless a future feature clearly requires multimodal analysis.

### 11.10 Face/visual analysis

Recommended:

- MediaPipe and/or OpenCV

Analyze low-resolution proxy frames rather than full-resolution video where possible.

Persist compact tracking data such as:

- frame/time
- face bounding boxes
- confidence
- scene-change markers

### 11.11 Rendering

FFmpeg is the primary final renderer.

Prefer a single or small number of encode passes.

Use intermediate artifacts only when they materially simplify correctness.

### 11.12 Deployment recommendation

For private beta, avoid Kubernetes.

A practical shape is:

- static/server-rendered Next.js deployment
- one FastAPI web service
- one or more worker services
- managed PostgreSQL
- managed Redis or equivalent
- private object storage

Scale workers horizontally before introducing orchestration complexity.

---

## 12. Processing pipeline

```text
CREATE PROJECT
     │
     v
UPLOAD / IMPORT SOURCE
     │
     v
VALIDATE MEDIA
     │
     ├── invalid → FAILED
     │
     v
EXTRACT METADATA
     │
     v
CREATE ANALYSIS PROXY / AUDIO
     │
     v
TRANSCRIBE + WORD TIMESTAMPS
     │
     v
SILENCE ANALYSIS
     │
     v
FACE / SCENE ANALYSIS
     │
     v
BUILD CANDIDATE WINDOWS
     │
     v
LLM SCORE + SELECT CANDIDATES
     │
     v
VALIDATE AI OUTPUT
     │
     v
READY
     │
     v
CREATOR SELECTS CLIP
     │
     v
PREVIEW
     │
     v
CREATOR EDITS HOOK/CAPTIONS/PRESET/WATERMARK
     │
     v
RENDER
     │
     ├─ safe silence/filler cuts
     ├─ smart 9:16 framing
     ├─ audio normalization
     ├─ hook overlay
     ├─ animated captions
     └─ watermark
     │
     v
SELECT COVER FRAME
     │
     v
GENERATE PLATFORM METADATA
     │
     v
COMPLETED DOWNLOAD PACKAGE
```

---

## 13. Data model

### 13.1 User

- `id`
- `google_subject`
- `email`
- `display_name`
- `account_state`
- `processing_minutes_limit`
- `created_at`
- `updated_at`

Do not use email alone as the immutable identity key when the OAuth provider supplies a stable subject identifier.

### 13.2 BetaInvite

- `id`
- `email` or invited identity
- `invited_by_user_id`
- `status`
- `expires_at` (nullable)
- `accepted_at` (nullable)
- `revoked_at` (nullable)
- `created_at`

### 13.3 UserPreference

- `user_id`
- `default_caption_preset`
- `default_watermark_text`
- `updated_at`

### 13.4 Project

- `id`
- `user_id`
- `source_type` (`upload`, `youtube_url`)
- `source_display_name`
- `source_url` (nullable; sanitized/normalized)
- `status`
- `duration_seconds`
- `width`
- `height`
- `fps`
- `source_language`
- `analysis_goal`
- `custom_instruction`
- `excluded_topics`
- `source_asset_id`
- `created_at`
- `updated_at`
- `source_expires_at`
- `analysis_expires_at`
- `history_expires_at`

### 13.5 MediaAsset

- `id`
- `project_id`
- `owner_user_id`
- `asset_type`
- `storage_key`
- `mime_type`
- `size_bytes`
- `checksum` (recommended)
- `created_at`
- `expires_at`
- `deleted_at` (nullable)

Asset types may include:

- source
- audio_proxy
- video_proxy
- render
- thumbnail
- srt
- ass
- transcript_json
- transcript_txt

### 13.6 Transcript

- `id`
- `project_id`
- `language`
- `plain_text_storage_key` or text column
- `timestamped_json_storage_key` or JSONB column
- `revision_number`
- `user_corrected`
- `created_at`
- `updated_at`

### 13.7 VisualAnalysis

- `id`
- `project_id`
- `analysis_version`
- `storage_key` or compact JSONB
- `created_at`

Contains compact data such as face tracks and scene markers, not full frames.

### 13.8 ClipSuggestion

- `id`
- `project_id`
- `analysis_run_id`
- `start_seconds`
- `end_seconds`
- `duration_seconds`
- `hook_text`
- `hook_score`
- `value_score`
- `standalone_score`
- `visual_score`
- `combined_score`
- `selection_reason`
- `rank`
- `created_at`

### 13.9 AnalysisRun

- `id`
- `project_id`
- `goal`
- `custom_instruction`
- `excluded_topics`
- `prompt_version`
- `model_provider`
- `model_name`
- `status`
- `created_at`

This makes regenerated suggestion sets auditable and reproducible enough for debugging.

### 13.10 Render

- `id`
- `project_id`
- `clip_suggestion_id`
- `user_id`
- `hook_text`
- `caption_preset`
- `watermark_text`
- `resolution`
- `status`
- `output_asset_id`
- `thumbnail_asset_id`
- `srt_asset_id`
- `ass_asset_id`
- `created_at`
- `completed_at` (nullable)
- `expires_at`

### 13.11 PlatformMetadata

- `id`
- `render_id`
- `platform`
- `title` (nullable)
- `description` (nullable)
- `caption` (nullable)
- `hashtags`
- `created_at`

### 13.12 Job

- `id`
- `project_id`
- `render_id` (nullable)
- `user_id`
- `job_type`
- `state`
- `attempt`
- `idempotency_key`
- `progress_stage`
- `failure_code` (nullable)
- `failure_detail` (nullable, operator-safe)
- `queued_at`
- `started_at` (nullable)
- `finished_at` (nullable)

### 13.13 UsageLedgerEntry

- `id`
- `user_id`
- `project_id`
- `job_id`
- `usage_type`
- `processed_seconds`
- `adjustment_seconds`
- `created_at`

### 13.14 AuditEvent

- `id`
- `actor_user_id` (nullable for system events)
- `event_type`
- `target_type`
- `target_id`
- `metadata` (must not contain private video/transcript content)
- `created_at`

---

## 14. Lifecycle and state transitions

### 14.1 Project lifecycle

Recommended transitions:

```text
draft
  ↓
uploading
  ↓
transcribing
  ↓
analyzing
  ↓
ready
  ├──────────────→ expired
  ├──────────────→ deleted
  └─ render jobs do not change project out of ready

Any active processing state
  ├─→ failed
  └─→ canceled
```

A failed project may retry the failed stage without recreating the project if upstream artifacts are valid.

### 14.2 Render lifecycle

```text
queued → rendering → completed
   │          │
   │          ├→ failed
   │          └→ canceled (only where safe)
   └────────────→ canceled

completed → expired
completed → deleted
```

### 14.3 Invalidation rules

- Editing transcript invalidates downstream analysis runs/suggestions created from the old transcript revision.
- Changing analysis goal does not invalidate transcription or visual preprocessing.
- Changing hook/caption preset/watermark/resolution invalidates only the render/preview, not clip analysis.
- Choosing another cover frame does not require video re-render.

---

## 15. API surface

Illustrative versioned HTTP endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/v1/me` | Current user and beta allowance |
| `POST` | `/v1/uploads` | Create direct-upload session |
| `POST` | `/v1/uploads/{id}/complete` | Mark upload complete and verify object |
| `POST` | `/v1/projects` | Create project metadata |
| `POST` | `/v1/projects/youtube` | Create project from supported YouTube URL when enabled |
| `GET` | `/v1/projects` | List creator projects |
| `GET` | `/v1/projects/{id}` | Read project |
| `DELETE` | `/v1/projects/{id}` | Delete project/assets |
| `POST` | `/v1/projects/{id}/process` | Begin transcription/analysis |
| `POST` | `/v1/projects/{id}/cancel` | Request cancellation |
| `GET` | `/v1/projects/{id}/status` | Read user-safe processing status |
| `GET` | `/v1/projects/{id}/transcript` | Read transcript |
| `PATCH` | `/v1/projects/{id}/transcript` | Save transcript corrections |
| `GET` | `/v1/projects/{id}/clips` | Read current clip suggestions |
| `POST` | `/v1/projects/{id}/analysis-runs` | Regenerate suggestions with goal/guidance |
| `POST` | `/v1/clips/{id}/preview` | Create/retrieve lightweight preview |
| `POST` | `/v1/clips/{id}/renders` | Queue final render |
| `GET` | `/v1/renders/{id}` | Read render state/result |
| `POST` | `/v1/renders/{id}/rerender` | Render same clip with new presentation settings |
| `PATCH` | `/v1/renders/{id}/captions` | Save caption-text corrections |
| `GET` | `/v1/renders/{id}/cover-frames` | Return selectable frames |
| `PATCH` | `/v1/renders/{id}/cover` | Choose cover frame |
| `GET` | `/v1/renders/{id}/downloads` | Obtain authenticated/signed asset links |
| `GET` | `/v1/usage` | Read monthly usage summary |
| `GET` | `/v1/preferences` | Read saved defaults |
| `PATCH` | `/v1/preferences` | Update saved defaults |

### 15.1 API rules

- Use opaque project/render identifiers.
- Never rely on opaque IDs as authorization.
- Validate creator ownership on every resource action.
- Long-running endpoints should queue work and return promptly.
- Use stable machine-readable error codes.
- Support an idempotency key for create/process/render requests where duplicate browser retries could trigger duplicate compute.

---

## 16. AI contracts

### 16.1 Structured clip-analysis response

The LLM must return schema-validated structured output.

Illustrative contract:

```json
{
  "clips": [
    {
      "start": 125.4,
      "end": 169.2,
      "hook": "Most developers are learning AI backwards",
      "scores": {
        "hook": 94,
        "value": 89,
        "standalone": 93,
        "visual": 82
      },
      "reason": "A complete contrarian argument with an immediate hook and clear takeaway."
    }
  ]
}
```

### 16.2 AI output validation

Reject or repair AI output when:

- start < 0
- end > source duration
- end <= start
- duration is outside configured min/max
- score is outside 0–100
- required fields are missing
- suggested windows overlap excessively when diversity is expected
- hook is empty or exceeds configured length
- reason exceeds configured length

Do not pass invalid timestamps directly into FFmpeg.

### 16.3 Prompt/version tracking

Every analysis run should record:

- prompt version
- model provider
- model name
- analysis goal
- creator guidance

This is required to debug why suggestion quality changes over time.

### 16.4 AI privacy minimization

Prefer sending:

- transcript text
- timestamps
- derived visual signals

rather than raw source video.

Do not include creator email, Google identity, signed asset URLs, or unrelated project metadata in prompts.

---

## 17. Security, privacy, and content-rights requirements

### 17.1 Authorization

- Authorize creator ownership for every project, transcript, suggestion, render, and download request.
- Do not trust IDs supplied by the browser.
- Beta-operator actions require separate privileged authorization.

### 17.2 Object storage

- No public media buckets.
- Use short-lived signed upload/download access.
- Keep object keys opaque and non-guessable.
- Do not put raw private asset URLs in analytics events.

### 17.3 File validation and processing isolation

- Inspect media with ffprobe before deeper processing.
- Enforce file-size, duration, stream-count, dimension, and codec/container constraints.
- Run FFmpeg and media parsers with process/resource limits.
- Keep media-processing dependencies patched.
- Do not trust MIME type or extension alone.

### 17.4 YouTube import safety

- Restrict network access to supported hosts and paths.
- Reject arbitrary remote URLs.
- Apply redirect limits and timeouts.
- Do not make the worker a generic SSRF-capable fetch service.
- Require rights/permission confirmation.

### 17.5 OAuth

- Request minimal scopes.
- Store only application-required identity information.
- Do not store Google access tokens unless a feature truly needs them.

### 17.6 Private content in logs

Application logs and analytics must not contain:

- full transcripts
- captions/content text unless explicitly sampled in a protected debugging workflow
- Google access tokens
- signed storage URLs
- raw video frames
- source video files
- private custom instructions where avoidable

Use opaque IDs in telemetry.

### 17.7 Encryption

- Encrypt data in transit.
- Use storage/database encryption at rest provided by the deployment platform.

### 17.8 Retention and deletion

- Automatic cleanup must be enforced by jobs and verified by telemetry.
- Manual delete immediately revokes product access and queues physical deletion.
- Failed cleanup attempts must be retried and visible to operators.

### 17.9 Content rights notice

For URL imports and uploads, product copy should make clear:

- the creator is responsible for having the necessary rights/permission
- public availability does not itself grant reuse rights
- the application does not make a legal determination about fair use or ownership

---

## 18. Performance and reliability targets

These are engineering targets for private beta, not contractual SLAs.

| Metric | MVP target |
| --- | --- |
| Normal API read/write latency | p95 < 500 ms excluding upload/download and queued compute |
| Dashboard first usable state | p95 < 3 seconds on broadband for normal account history |
| Job status freshness | <= 5 seconds with polling |
| Upload path | Direct-to-object-storage in deployed beta |
| Analysis job start after queueing | p95 < 60 seconds under planned beta load |
| Transcript + clip suggestions for a 30-min source | Target <= 10 minutes on chosen production worker class; measure and revise after benchmark |
| 60-sec 1080p final render | Target <= 3 minutes on chosen production worker class |
| Retry safety | Retrying a job must not create duplicate usage charges or duplicate final records |
| Failed-worker recovery | Retryable job can resume/retry without requiring creator re-upload when source still exists |
| Media cleanup | Expired assets removed within 24 hours of configured expiry under normal operation |
| Private-beta availability | Target 99% monthly; no external SLA |

### 18.1 Benchmark requirement

Before public beta, benchmark at least:

- 10-minute talking-head 1080p source
- 30-minute podcast 1080p source
- 60-minute interview 1080p source
- portrait source
- screen-recording/no-face source
- multi-speaker source

Track stage-level time and peak memory/CPU/GPU usage.

---

## 19. Reliability and failure handling

### 19.1 General principles

- Each expensive stage must be retryable independently where safe.
- Completed upstream artifacts should be reused after downstream failure.
- Job retries must be idempotent.
- A creator should not need to re-upload because metadata generation failed.
- A metadata-generation failure should not invalidate a successfully rendered MP4.

### 19.2 Stable creator-facing errors

Examples:

- `UNSUPPORTED_MEDIA`
- `SOURCE_TOO_LONG`
- `SOURCE_TOO_LARGE`
- `MEDIA_PROBE_FAILED`
- `TRANSCRIPTION_FAILED`
- `ANALYSIS_FAILED`
- `RENDER_FAILED`
- `IMPORT_UNAVAILABLE`
- `MONTHLY_LIMIT_REACHED`
- `ACTIVE_JOB_EXISTS`
- `ASSET_EXPIRED`

Creator-facing text should explain the next action without exposing internal stack traces.

### 19.3 Partial success

If final MP4 succeeds but metadata generation fails:

- mark render media as completed
- show the downloadable MP4
- retry metadata separately

If cover generation fails:

- keep the video render
- use a deterministic fallback frame

If face tracking fails:

- use smart/stable center crop

If word-level timestamps are incomplete:

- fall back to phrase/segment caption timing

---

## 20. Observability

Capture stage-level telemetry for:

### Product funnel

- invited user sign-in success/failure
- project creation
- valid upload completion
- analysis started/completed/failed
- suggestions viewed
- suggestion previewed
- render started/completed/failed
- final MP4 downloaded
- re-render started
- project deleted

### Processing

- queue wait time
- media-validation duration
- transcription duration
- visual-analysis duration
- LLM-analysis duration
- render duration
- thumbnail duration
- cleanup duration

### Reliability

- job retry count
- terminal failure rate by stable code
- worker crashes/timeouts
- invalid LLM response rate
- FFmpeg failures by stage
- storage upload/download failure rate
- cleanup failure count

### Cost

- transcription cost or compute time per processed source minute
- LLM cost per analyzed source minute/project
- worker compute cost per processed minute
- storage bytes by asset type
- estimated total cost per completed downloaded Short

### Quality/product signals

- suggestions per project
- suggestion preview rate
- suggestion selection rate
- project-to-render rate
- project-to-download rate
- regeneration rate
- re-render rate
- time to first downloaded Short

### Privacy rule

Telemetry uses opaque identifiers and excludes raw private content.

---

## 21. Product metrics

### 21.1 Primary MVP metric

> **Project-to-download conversion:** percentage of analyzed projects where the creator downloads at least one rendered Short.

This measures whether the product produces something the creator actually wants to use.

### 21.2 Secondary metrics

- suggestion preview rate
- suggestion selection rate
- render completion rate
- time to first downloaded Short
- regenerate-suggestions rate
- re-render rate
- weekly returning beta creators

### 21.3 Quality guardrails

Track beta feedback on:

- clip starts naturally
- clip ends naturally
- best moment was found
- captions are accurate
- crop feels natural
- output can be posted without another editor

### 21.4 Cost guardrail

Track:

> total infrastructure/API cost per processed source minute and per downloaded Short

Do not set paid pricing until this is measured with real beta usage.

---

## 22. Cost controls

MVP cost controls:

- 60 processed source minutes/month/user
- invitation-only access
- one compute-intensive job/user
- 3-hour source cap
- 4-GB source cap
- low-resolution analysis proxies
- reuse transcript/visual analysis during regeneration
- reuse selected clip analysis during re-render
- short source-media retention
- 7-day render retention
- no automatic batch rendering
- no raw-video multimodal LLM analysis by default

Add operator alerts for abnormal:

- per-project compute duration
- repeated retries
- unusually large output/storage volume
- unusually high API/LLM token consumption

---

## 23. MVP acceptance criteria

The MVP is accepted when all of the following are true.

1. An invited creator can sign in with Google and a non-invited Google user cannot enter the beta application.
2. Creator A cannot access Creator B's project, transcript, media, suggestion, render, or download by changing an identifier.
3. A creator can upload a valid MP4, MOV, or WebM source within the configured size/duration limits.
4. The system rejects a source over 3 hours with a clear stable error.
5. The system rejects malformed/unsupported media without starting expensive analysis.
6. A valid source produces an auto-detected-language transcript with timestamped segments and word timing where supported.
7. A creator can correct transcript text and save a new transcript revision.
8. Analysis produces between 3 and 10 suggestions according to source duration and quality threshold.
9. Every suggestion contains valid source timestamps, a hook, four scores, and a concise reason.
10. Suggested clips respect the configured 15–90 second hard duration range.
11. Suggestions do not begin/end mid-word, and normal test cases do not end mid-thought.
12. A creator can regenerate suggestions using Educational, Entertaining, or Viral without retranscribing an unchanged source.
13. Custom guidance and topics-to-avoid materially influence the regenerated set in controlled test cases.
14. A creator can preview a suggestion before final render.
15. A creator can edit hook text, correct caption text, switch among three caption presets, set a watermark, and select resolution.
16. A rendered output is 9:16 and playable as MP4.
17. A face-present test clip uses stable face-aware framing without excessive jitter.
18. A no-face test clip falls back to a stable center crop.
19. A multi-speaker test clip does not rapidly oscillate framing when active-speaker confidence is low.
20. A rendered Short contains synchronized highlighted captions in the selected preset.
21. SRT and ASS downloads are generated for the rendered Short.
22. Hook text appears near the beginning and disappears after the configured introductory window.
23. Basic audio normalization is applied without obvious clipping in the standard test set.
24. The system selects a cover frame and the creator can choose another sampled frame without re-rendering the video.
25. Separate YouTube, TikTok, and Instagram metadata is generated from the selected clip/source context.
26. The creator can download MP4, cover image, SRT, ASS, transcript TXT, and transcript JSON while those assets are retained.
27. The creator can re-render the same selected clip with a different caption preset/hook/watermark/resolution without re-transcribing or re-analyzing.
28. Monthly usage is visible and is backed by usage-ledger records.
29. Canceling a processing job records only the configured amount of work already consumed; retrying does not double-count the same work.
30. The application prevents a second compute-intensive job from running concurrently for the same creator.
31. Original source media expires according to the 24-hour policy and becomes inaccessible.
32. Rendered assets expire according to the 7-day policy and become inaccessible.
33. Manual project deletion revokes access immediately and queues all associated private media for deletion.
34. A failed downstream stage can retry without forcing a valid source to be uploaded again.
35. Job status and stable error codes are visible to beta operators without exposing private content in ordinary telemetry.
36. The core flows are usable by keyboard and standard UI meets the accessibility target.

---

## 24. Delivery phases

### Phase 0 — Repository and local environment

Build:

- monorepo or clearly separated frontend/backend repository
- Next.js
- FastAPI
- PostgreSQL
- Redis
- Celery worker
- Docker Compose for local dependencies
- FFmpeg/ffprobe availability
- health checks
- environment configuration
- CI for lint/test

Acceptance:

> A developer can clone the repository, configure environment variables, start the stack, and see healthy frontend/API/worker/database/queue services.

### Phase 1 — Local upload, project model, and media validation

Build:

- local upload
- project creation
- ffprobe metadata
- file type/size/duration validation
- project status
- project retrieval
- project deletion
- tests

Do not build AI/transcription yet.

Acceptance:

> Upload a valid video, retrieve correct metadata, reject invalid/too-long media, and delete the project/source.

### Phase 2 — Transcription and transcript editing

Build:

- audio extraction
- faster-whisper integration
- language detection
- word timestamps
- TXT/JSON transcript artifacts
- transcript editor
- transcript revisions

Acceptance:

> A valid video produces an editable timestamped transcript that can be downloaded as TXT and JSON.

### Phase 3 — Basic candidate segmentation and silence analysis

Build:

- silence detection
- sentence/segment candidate windows
- duration rules
- safe boundaries
- candidate debug output

No LLM ranking yet.

Acceptance:

> A transcript produces valid continuous candidate intervals without mid-word cuts or long trailing silence.

### Phase 4 — AI clip discovery

Build:

- analysis goals
- custom guidance
- excluded topics
- LLM structured response
- schema validation
- four scores
- explanation
- suggestion count logic
- regeneration
- prompt/version tracking

Acceptance:

> A 30-minute spoken video returns coherent ranked suggestions with valid timestamps and can regenerate without retranscription.

### Phase 5 — Basic final rendering

Build:

- selected clip extraction
- 9:16 output
- initial stable center crop
- 720p/1080p
- MP4 asset
- render job lifecycle

Acceptance:

> A selected suggestion renders into a playable 9:16 MP4 in both supported resolutions.

### Phase 6 — Smart framing

Build:

- low-resolution visual proxy
- face detection
- face tracking
- smoothing
- stable fallback crop
- multi-face conservative behavior

Acceptance:

> Talking-head, no-face, and multi-face fixtures produce stable framing without obvious jitter.

### Phase 7 — Captions and hook

Build:

- phrase grouping
- word highlighting
- ASS generation
- SRT generation
- three caption presets
- hook overlay
- caption corrections

Acceptance:

> Final output contains readable synchronized captions and the editable hook appears only during the intro window.

### Phase 8 — Conservative pacing and audio cleanup

Build:

- silence trimming
- safe filler/false-start cuts
- audio normalization
- conservative denoise
- fallback behavior

Acceptance:

> Standard fixtures contain no obvious long dead air and safe cuts do not materially distort speech.

### Phase 9 — Preview and re-render

Build:

- lightweight preview
- caption preset switching
- watermark
- resolution selection
- re-render using existing clip analysis

Acceptance:

> The creator can adjust presentation and re-render without retranscription/re-analysis.

### Phase 10 — Cover frames and platform metadata

Build:

- cover selection heuristic
- alternate sampled frames
- YouTube metadata
- TikTok metadata
- Instagram metadata

Acceptance:

> Every successful render exposes a downloadable cover and separate copy for all three platforms.

### Phase 11 — Authentication, invitations, authorization, and project history

Build:

- Google OAuth
- beta invitation checks
- user ownership authorization
- basic history
- saved defaults
- cross-user security tests

Acceptance:

> Two invited creators can sign in and cannot access each other's resources.

### Phase 12 — Cloud upload/storage and retention

Build:

- storage abstraction
- private object storage
- direct signed uploads
- signed downloads
- expiry timestamps
- cleanup worker
- deletion verification

Acceptance:

> Multi-gigabyte-capable uploads bypass the API process and expired/deleted assets become inaccessible and are removed.

### Phase 13 — Usage ledger and operator tooling

Build:

- 60-minute monthly allowance
- usage ledger
- partial-cancel handling
- one-active-job enforcement
- minimal operator invitation/job diagnostics

Acceptance:

> Duplicate retries do not double-count usage and operators can diagnose jobs without ordinary access to private content.

### Phase 14 — Production readiness

Build/test:

- observability dashboards
- failure alerts
- benchmark suite
- retry/failure injection tests
- browser/accessibility checks
- rate limits
- dependency/container patching
- retention verification
- cost dashboard

Acceptance:

> The private beta can be operated, measured, and debugged without manually inspecting the database for routine failures.

### Phase 15 — Optional URL-import beta

Only after file upload is reliable:

- enable supported YouTube URL import behind a feature flag
- rights notice/confirmation
- strict supported-host validation
- failure fallback to file upload
- dedicated observability

Acceptance:

> Supported imports enter the same validated media pipeline; failures do not destabilize the upload-first product.

---

## 25. Recommended repository structure

Start simple and grow modules only when the corresponding milestone exists.

```text
ai-shorts-generator/
├── apps/
│   ├── web/                         # Next.js
│   └── api/                         # FastAPI
│       └── app/
│           ├── api/
│           ├── auth/
│           ├── models/
│           ├── schemas/
│           ├── services/
│           │   ├── storage/
│           │   ├── media/
│           │   ├── transcription/
│           │   ├── analysis/
│           │   ├── framing/
│           │   ├── captions/
│           │   ├── rendering/
│           │   └── metadata/
│           ├── jobs/
│           └── main.py
├── packages/
│   └── shared-types/                # optional; add only if useful
├── infra/
│   └── docker-compose.yml
├── tests/
├── .env.example
├── README.md
└── AGENTS.md                         # coding-agent rules/context
```

Avoid creating every empty service directory on day one. Let the structure grow with delivery phases.

---

## 26. Vibe-coding operating rules

### Rule 1 — One milestone per coding session

Do not prompt:

> Build my complete AI Shorts SaaS.

Instead, implement one accepted milestone at a time.

### Rule 2 — Every coding prompt contains boundaries

Include:

- current milestone
- existing architecture to preserve
- exact requirements
- explicit non-goals
- files likely to change
- acceptance criteria
- tests to run

### Rule 3 — Inspect before editing

Require the coding agent to inspect:

- repository tree
- relevant existing code
- migrations/models
- tests
- current README/AGENTS.md

before proposing architectural changes.

### Rule 4 — Preserve working code

Do not allow broad rewrites merely because the agent prefers a different library.

### Rule 5 — Tests are part of the feature

Each milestone adds the tests needed to prove its acceptance criteria.

### Rule 6 — Keep commits small

Create a stable commit after each working milestone.

Example:

```bash
git add .
git commit -m "feat: add timestamped transcription"
```

### Rule 7 — AI output is untrusted input

Schema-validate model responses exactly as external API responses are validated.

### Rule 8 — Separate content processing from web requests

No FFmpeg, Whisper, or long LLM workflow should run synchronously inside an HTTP request handler.

### Rule 9 — Measure before optimizing

Do not add GPUs, Kubernetes, distributed queues, or complex caching before stage-level benchmarks show they are necessary.

### Rule 10 — Maintain a decision log

When the coding agent makes a non-trivial product/architecture decision, record it briefly in `docs/decisions/` or the project README rather than leaving it only in chat history.

---

## 27. First coding-agent prompt

Use this after the repository has been created.

```text
You are helping me build AI Shorts Generator, a creator-first application that
turns long-form videos into short vertical clips.

We are implementing only Phase 1: local upload, project persistence, and media
validation.

Current stack:
- Next.js + TypeScript + Tailwind frontend
- FastAPI backend
- PostgreSQL
- Redis/Celery will be used later, but do not add background media processing yet
- FFmpeg/ffprobe

Requirements:
1. Inspect the repository before modifying anything.
2. Create or preserve a clean FastAPI application structure.
3. Add a Project database model with the fields needed for Phase 1 only.
4. Implement POST /v1/projects for an upload-based project flow suitable for local development.
5. Accept MP4, MOV, and WebM.
6. Save uploaded media under a configurable local storage directory.
7. Use ffprobe to validate the actual media and extract:
   - duration
   - width
   - height
   - frame rate
8. Reject:
   - unreadable media
   - unsupported containers/streams
   - videos longer than 3 hours
   - files larger than the configured 4 GB limit
9. Generate an opaque project ID.
10. Implement GET /v1/projects/{project_id}.
11. Implement DELETE /v1/projects/{project_id}.
12. Deleting a project must remove its local source media and database record for this phase.
13. Use stable machine-readable error codes.
14. Add tests for success and all validation failures that can be exercised without huge fixture files.
15. Update README with exact local run/test commands.

Do not implement:
- transcription
- LLM integration
- clip selection
- rendering
- captions
- Google OAuth
- invitation system
- cloud/object storage
- YouTube URL import
- billing
- production queue orchestration

Before writing code:
1. show the relevant repository tree
2. summarize the existing architecture
3. list the files you plan to change and why
4. identify any assumption you must make

Then implement the smallest correct change.

After implementation:
1. run formatting/linting/tests available in the repo
2. report exactly what passed/failed
3. show the new endpoints
4. list known limitations

Acceptance criteria:
- I can start the local backend and database.
- I can upload a valid small MP4 and receive a project ID.
- GET returns accurate ffprobe metadata.
- Unsupported/malformed media is rejected with a stable error.
- A video whose probed duration exceeds 3 hours is rejected.
- DELETE removes both the local file and project record.
- Tests pass.
```

---

## 28. Private-beta test questions

Ask beta creators:

1. Did the system find moments you would actually post?
2. Did it miss the best moment in your source video?
3. Did suggested clips begin naturally?
4. Did suggested clips end naturally?
5. Were the four scores useful or distracting?
6. Were captions accurate enough?
7. Did framing look natural?
8. Did silence/filler cleanup create any awkward cuts?
9. Did the hook feel accurate to the clip?
10. Would you publish the output without opening another editor?
11. What did you still have to fix manually?
12. Did the processing time feel acceptable?
13. Would this save you enough time to use every week?
14. What single missing feature would make it meaningfully more valuable?
15. If you had to pay for processing minutes, what usage pattern would you expect each month?

---

## 29. Product decisions still required before public beta

The private-beta spec can proceed with the defaults above. Before a broader launch, decide:

1. Which cloud compute provider/worker class gives the best cost per processed minute?
2. Whether transcription remains self-hosted or moves to a managed provider.
3. Whether YouTube URL import can be supported reliably and compliantly enough to graduate from feature flag.
4. Whether the 4-GB upload limit should increase/decrease based on real source files.
5. Whether 60 free processing minutes is economically sustainable.
6. Which clip-score weighting performs best with creator feedback.
7. Whether transcript retention should remain 30 days or be shorter by default.
8. Whether basic mobile project creation is important enough to support officially.
9. Which one or two creator niches show the strongest repeated use.
10. What paid packaging makes sense after real cost and retention data exist.

These are intentionally deferred because real beta data should answer them better than speculation.

---

## 30. Post-MVP roadmap

### High-priority candidates

Only after core validation:

- manual clip-start/end adjustment
- batch rendering of selected suggestions
- direct YouTube publishing
- creator/channel presets
- additional caption presets
- basic content performance tracking
- paid plans
- configurable clip-length targets

### Later candidates

- TikTok/Instagram publishing integrations
- publishing calendar
- B-roll suggestions
- automatic B-roll
- stock-media integrations
- automatic translation
- dubbing
- music library
- advanced audio cleanup
- split-screen podcast layouts
- brand kits
- teams/workspaces
- agency accounts
- collaborative review
- performance-informed clip ranking

Do not implement roadmap features merely because an AI coding agent can generate them quickly.

---

## 31. Recommended next artifacts

Before substantial implementation, create these three lightweight project artifacts:

1. **Clickable UX prototype** covering:
   - login
   - dashboard
   - new project
   - processing
   - transcript
   - suggestions
   - clip preview
   - final result

2. **Engineering spike** proving:
   - faster-whisper word timestamps on representative videos
   - FFmpeg 9:16 render quality/speed
   - face tracking stability
   - ASS word-highlighting captions
   - safe silence trimming

3. **AGENTS.md** for the coding agent containing:
   - product summary
   - stack
   - architectural rules
   - commands
   - test expectations
   - "do not overbuild" constraints

The engineering spike should happen before building polished UI because transcription, framing, and rendering quality are the main technical risks.

---

## 32. Definition of a successful MVP

The MVP succeeds when an invited creator can:

1. Sign in.
2. Upload a long-form video.
3. Tell the system what type of moments they want.
4. Receive a small set of understandable, high-quality candidate clips.
5. See why those moments were selected.
6. Preview one candidate.
7. Make only lightweight text/style changes.
8. Render a polished vertical Short.
9. Download the Short and supporting assets.
10. Post it without needing another video editor for the majority of successful test cases.

The strongest product signal is not that processing finished. It is that the creator **downloads and is willing to publish the result**.

---

## 33. Final MVP scope in one sentence

> An invite-only creator tool that accepts up to a 3-hour long-form video, transcribes and analyzes it, recommends 3–10 coherent high-potential moments, lets the creator preview and lightly customize one moment at a time, and renders a downloadable 9:16 Short with stable smart framing, highlighted captions, an editable hook, optional watermark, basic audio cleanup, cover image, and platform-specific posting metadata.

---

## 34. Final build recommendation

Build the product in this order:

```text
UPLOAD
  ↓
TRANSCRIPT
  ↓
GOOD CLIP SELECTION
  ↓
BASIC 9:16 RENDER
  ↓
SMART FRAMING
  ↓
CAPTIONS + HOOK
  ↓
PREVIEW / RE-RENDER
  ↓
COVER + METADATA
  ↓
AUTH / PRIVATE BETA
  ↓
CLOUD STORAGE / QUOTAS / OPERATIONS
```

Do **not** start by building authentication, subscriptions, publishing integrations, or a sophisticated editor.

The technical moat, if this product earns one, will come from consistently finding better moments and producing a result creators are comfortable publishing—not from having the largest settings page.


