/* Story Mode UI: start form, stories list and the Script → Voice → Render editor.
   Presentation only; every network/state operation stays in the page controller (v.story). */
(function (root) {
  "use strict";
  const h = (...args) => root.React.createElement(...args);
  const UI = () => root.ShortsUI;
  const seconds = (s) => `${Math.round(s)} s`;
  const pct = (p) => `${Math.round((p || 0) * 100)}%`;

  function Notice({ kind = "", children, action }) {
    return h(
      "div",
      { className: `notice ${kind}`, role: kind === "error" ? "alert" : "status" },
      h("div", { className: "stack tight" }, children, action),
    );
  }

  function Progress({ value, label }) {
    return h(
      "div",
      { className: "stack tight", role: "status" },
      h("p", { className: "small muted" }, label),
      h(
        "div",
        {
          className: "meter",
          role: "progressbar",
          "aria-label": label,
          "aria-valuemin": 0,
          "aria-valuemax": 100,
          "aria-valuenow": value == null ? undefined : Math.round(value * 100),
        },
        h("div", {
          className: `meter-fill ${value == null ? "indeterminate" : ""}`,
          style: { width: value == null ? "40%" : pct(Math.max(0.04, value)) },
        }),
      ),
    );
  }

  /* ---------- New Project → Story tab ---------- */

  function StoryStart({ v }) {
    const { Button, Field, Segments } = UI();
    const s = v.story;
    const f = s.form;
    const genre = s.genres.find((g) => g.id === f.genre);
    const text = f.mode === "topic" ? f.topic : f.story;
    const limit = f.mode === "topic" ? 300 : 6000;
    return h(
      "div",
      { className: "stack" },
      h(Segments, {
        label: "Story source",
        items: [
          { id: "topic", label: "Write from a topic", pressed: String(f.mode === "topic"), pick: () => s.setForm({ mode: "topic" }) },
          { id: "story", label: "Paste a story", pressed: String(f.mode === "story"), pick: () => s.setForm({ mode: "story" }) },
        ],
      }),
      h(
        Field,
        {
          id: "story-text",
          label: f.mode === "topic" ? "What is the story about?" : "Your story",
          hint:
            f.mode === "topic"
              ? `Claude writes an original narrated story. ${text.length}/${limit}`
              : `Claude tightens it to fit the length without adding events. ${text.length}/${limit}`,
        },
        h("textarea", {
          id: "story-text",
          value: text,
          maxLength: limit,
          rows: f.mode === "topic" ? 3 : 10,
          "aria-describedby": "story-text-hint",
          placeholder:
            f.mode === "topic"
              ? "A lighthouse keeper who finds a message in a bottle"
              : "Paste the story you want narrated…",
          onChange: (e) => s.setForm({ [f.mode]: e.target.value }),
        }),
      ),
      h(
        "div",
        { className: "field" },
        h("span", { className: "field-label" }, "Genre"),
        s.genres.length
          ? h(Segments, {
              label: "Genre",
              items: s.genres.map((g) => ({
                id: g.id,
                label: g.label,
                pressed: String(f.genre === g.id),
                pick: () => s.setForm({ genre: g.id }),
              })),
            })
          : h("p", { className: "small muted" }, s.optionsError || "Loading genres…"),
      ),
      h(
        "div",
        { className: "field" },
        h("span", { className: "field-label" }, "Length"),
        h(Segments, {
          label: "Target length",
          items: (s.lengths.length ? s.lengths : [30, 45, 60]).map((n) => ({
            id: String(n),
            label: `${n} seconds`,
            pressed: String(f.seconds === n),
            pick: () => s.setForm({ seconds: n }),
          })),
        }),
      ),
      h(
        Field,
        { id: "story-tone", label: "Tone", optional: true },
        h("input", {
          id: "story-tone",
          value: f.tone,
          maxLength: 80,
          placeholder: genre ? genre.defaultTone : "e.g. suspenseful and quiet",
          onChange: (e) => s.setForm({ tone: e.target.value }),
        }),
      ),
      s.createError && h(Notice, { kind: "error" }, s.createError),
      h(
        "div",
        { className: "form-footer" },
        h(
          "p",
          null,
          "You can edit the script before any voice is generated.",
        ),
        h(
          Button,
          { primary: true, icon: "spark", onClick: s.create, disabled: !s.canCreate },
          s.creating ? "Writing script…" : "Write script",
        ),
      ),
      s.creating &&
        h(Progress, { value: null, label: "Claude is writing your script. This takes about 20 seconds." }),
    );
  }

  /* ---------- Stories list ---------- */

  const STATUS = {
    draft: ["Script ready", ""],
    voicing: ["Generating voice", "processing"],
    voiced: ["Voice ready", "accent"],
    voice_failed: ["Voice failed", "failed"],
    rendering: ["Rendering", "processing"],
    rendered: ["Short ready", "ready"],
    render_failed: ["Render failed", "failed"],
  };

  function Badge({ status }) {
    const [label, kind] = STATUS[status] || [status, ""];
    return h("span", { className: `badge ${kind}` }, label);
  }

  function StoryList({ v }) {
    const { Button, PageHeader, ErrorState, EmptyState } = UI();
    const s = v.story;
    return h(
      "div",
      { className: "narrow stack" },
      h(
        PageHeader,
        { title: "Stories", description: "Narrated Shorts written from a topic or your own story." },
        h(Button, { primary: true, icon: "plus", onClick: s.newStory }, "New story"),
      ),
      s.listError && h(ErrorState, { title: "We couldn’t load your stories.", message: s.listError, retry: s.reloadList }),
      s.listLoading && !s.list.length && h("p", { role: "status", className: "muted" }, "Loading stories…"),
      !s.listLoading && !s.listError && !s.list.length &&
        h(EmptyState, {
          title: "No stories yet",
          description: "Give Claude a topic or paste a story, and get a narrated Short with captions.",
          action: s.newStory,
          label: "Write your first story",
          icon: "spark",
        }),
      s.list.map((item) =>
        h(
          "button",
          { key: item.id, className: "panel pad row between story-row", onClick: () => s.open(item.id) },
          h(
            "div",
            { className: "stack tight grow" },
            h("strong", { className: "truncate" }, item.title),
            h("span", { className: "small muted" }, `${item.genreLabel} · ${item.targetSeconds} s target · ${item.updatedLabel}`),
          ),
          h(Badge, { status: item.status }),
        ),
      ),
    );
  }

  /* ---------- Story editor ---------- */

  function Steps({ steps }) {
    return h(
      "ol",
      { className: "story-steps", "aria-label": "Story progress" },
      steps.map((step, i) =>
        h(
          "li",
          { key: step.label, className: `story-step ${step.state}`, "aria-current": step.state === "current" ? "step" : undefined },
          h("span", { className: "step-number" }, step.state === "done" ? "✓" : String(i + 1)),
          h("span", null, h("strong", null, step.label), h("span", { className: "small muted" }, step.detail)),
        ),
      ),
    );
  }

  function ScriptStep({ s }) {
    const { Button, Field } = UI();
    const d = s.draft;
    const story = s.current;
    const locked = s.jobRunning;
    return h(
      "section",
      { className: "panel pad stack", "aria-labelledby": "script-heading" },
      h(
        "div",
        { className: "row between wrap" },
        h("h2", { id: "script-heading" }, "1. Script"),
        h("span", { className: "small muted" }, s.wordLine),
      ),
      s.warnings.length > 0 &&
        h(
          Notice,
          {
            kind: "error",
            action:
              s.canShorten &&
              h(Button, { small: true, onClick: s.shorten, disabled: !!s.busy || locked }, s.busy === "shorten" ? "Shortening…" : "Shorten script"),
          },
          s.warnings.map((w) => h("span", { key: w }, w)),
        ),
      h(
        "div",
        { className: "row wrap story-fields" },
        h(Field, { id: "story-title", label: "Title" }, h("input", { id: "story-title", value: d.title, maxLength: 100, disabled: locked, onChange: (e) => s.editField("title", e.target.value) })),
        h(Field, { id: "story-hook", label: "Hook (the first sentence, at most 7 words)" }, h("input", { id: "story-hook", value: d.hook, maxLength: 200, disabled: locked, onChange: (e) => s.editField("hook", e.target.value) })),
      ),
      d.scenes.map((scene, i) =>
        h(
          "div",
          { key: i, className: "scene-card stack tight" },
          h(
            "div",
            { className: "row between" },
            h("strong", null, `Scene ${i + 1}`),
            h("span", { className: "small muted" }, s.sceneWords[i]),
          ),
          h("label", { className: "sr-only", htmlFor: `scene-${i}` }, `Scene ${i + 1} narration`),
          h("textarea", {
            id: `scene-${i}`,
            value: scene.narration,
            rows: 3,
            maxLength: 1200,
            disabled: locked,
            onChange: (e) => s.editScene(i, "narration", e.target.value),
          }),
          h(
            "details",
            null,
            h("summary", { className: "small muted" }, `Visual: ${scene.visual_description || "—"} · Mood: ${scene.mood || "—"}`),
            h(
              "div",
              { className: "row wrap mt-2" },
              h(Field, { id: `visual-${i}`, label: "What we see" }, h("input", { id: `visual-${i}`, value: scene.visual_description, maxLength: 500, disabled: locked, onChange: (e) => s.editScene(i, "visual_description", e.target.value) })),
              h(Field, { id: `mood-${i}`, label: "Mood" }, h("input", { id: `mood-${i}`, value: scene.mood, maxLength: 40, disabled: locked, onChange: (e) => s.editScene(i, "mood", e.target.value) })),
            ),
          ),
        ),
      ),
      s.scriptError && h(Notice, { kind: "error" }, s.scriptError),
      h(
        "div",
        { className: "row wrap end" },
        s.dirty && h(Button, { ghost: true, onClick: s.discard, disabled: !!s.busy }, "Discard changes"),
        h(Button, { onClick: s.regenerate, disabled: !!s.busy || locked || s.dirty, icon: "spark" }, s.busy === "regenerate" ? "Writing…" : "Write a new version"),
        h(Button, { primary: true, onClick: s.saveScript, disabled: !s.dirty || !!s.busy || locked }, s.busy === "save" ? "Saving…" : s.dirty ? "Save script" : "Saved"),
      ),
      (s.busy === "regenerate" || s.busy === "shorten") &&
        h(Progress, { value: null, label: "Claude is rewriting the script…" }),
      story.scriptSource === "edited" && !s.dirty && h("p", { className: "small muted" }, "You edited this script."),
    );
  }

  function VoiceStep({ s }) {
    const { Button, Field, Segments } = UI();
    const c = s.voiceChoice;
    const provider = s.providers.find((p) => p.id === c.provider);
    const voice = s.current.voice;
    const job = s.current.voiceJob;
    const voicing = s.current.status === "voicing";
    return h(
      "section",
      { className: "panel pad stack", "aria-labelledby": "voice-heading" },
      h("h2", { id: "voice-heading" }, "2. Voice"),
      s.dirty && h(Notice, null, "Save the script before generating the voice."),
      h(
        "div",
        { className: "field" },
        h("span", { className: "field-label" }, "Voice engine"),
        s.providers.length
          ? h(Segments, {
              label: "Voice engine",
              items: s.providers.map((p) => ({
                id: p.id,
                label: p.label,
                pressed: String(c.provider === p.id),
                disabled: !p.available,
                pick: () => s.setVoice({ provider: p.id }),
              })),
            })
          : h("p", { className: "small muted" }, s.optionsError || "Loading voices…"),
        s.providers
          .filter((p) => !p.available && p.reason)
          .map((p) => h("p", { key: p.id, className: "small muted" }, `${p.label}: ${p.reason}`)),
      ),
      provider &&
        h(
          "div",
          { className: "row wrap story-fields" },
          h(
            Field,
            { id: "story-voice", label: "Voice" },
            h(
              "select",
              { id: "story-voice", value: c.voice, disabled: voicing, onChange: (e) => s.setVoice({ voice: e.target.value }) },
              provider.voices.map((item) => h("option", { key: item.id, value: item.id }, item.description ? `${item.label} — ${item.description}` : item.label)),
            ),
          ),
          h(
            Field,
            { id: "story-speed", label: `Speed ${Number(c.speed).toFixed(2)}×`, hint: `Default ${provider.defaultSpeed}×` },
            h("input", {
              id: "story-speed",
              type: "range",
              min: provider.speedRange[0],
              max: provider.speedRange[1],
              step: 0.05,
              value: c.speed,
              disabled: voicing,
              "aria-describedby": "story-speed-hint",
              onChange: (e) => s.setVoice({ speed: Number(e.target.value) }),
            }),
          ),
        ),
      voicing &&
        h(Progress, {
          value: job.progress,
          label: `Generating the voice… ${pct(job.progress)} (scenes are spoken one by one)`,
        }),
      job.error && h(Notice, { kind: "error" }, `${job.error.message}`),
      voice &&
        h(
          "div",
          { className: "stack tight" },
          voice.stale &&
            h(Notice, null, "The script changed since this voice was made. Generate it again to match."),
          voice.tooLong &&
            !voice.stale &&
            h(
              Notice,
              {
                kind: "error",
                action: h(Button, { small: true, onClick: s.shorten, disabled: !!s.busy || s.jobRunning }, "Shorten script"),
              },
              `The voiceover runs ${seconds(voice.durationSec)}; Shorts must stay under 60 seconds.`,
            ),
          s.audioUrl &&
            h("audio", { controls: true, src: s.audioUrl, className: "story-audio", "aria-label": "Voiceover preview" }),
          h(
            "p",
            { className: "small muted" },
            `${seconds(voice.durationSec)} · ${voice.voice} at ${voice.speed}× · ${voice.loudness.outputLufs ?? "—"} LUFS`,
            voice.costUsd ? ` · $${voice.costUsd.toFixed(4)}` : "",
          ),
        ),
      h(
        "div",
        { className: "row end" },
        h(
          Button,
          { primary: !voice || voice.stale, onClick: s.generateVoice, disabled: !s.canVoice, icon: "play" },
          voicing ? "Generating…" : voice ? "Generate again" : "Generate voice",
        ),
      ),
    );
  }

  function RenderStep({ s }) {
    const { Button, Segments, VideoPreview } = UI();
    const c = s.renderChoice;
    const rendering = s.current.status === "rendering";
    const latest = s.renders[0];
    return h(
      "section",
      { className: "panel pad stack", "aria-labelledby": "render-heading" },
      h("h2", { id: "render-heading" }, "3. Background & render"),
      !s.voiceReady && h(Notice, null, "Generate a voice that matches the script first."),
      h(
        "div",
        { className: "field" },
        h("span", { className: "field-label" }, "Background"),
        h(
          "div",
          { className: "choice-grid", role: "group", "aria-label": "Background" },
          s.backgrounds.map((b) =>
            h(
              "button",
              {
                key: `${b.kind}:${b.id}`,
                type: "button",
                className: `choice bg-${b.kind === "bundled" ? b.id : b.kind}`,
                "aria-pressed": String(c.background && c.background.kind === b.kind && c.background.id === b.id),
                disabled: rendering,
                onClick: () => s.setRender({ background: { kind: b.kind, id: b.id } }),
              },
              h("strong", null, b.label),
              h("span", { className: "small" }, b.description),
            ),
          ),
        ),
        h("p", { className: "small muted" }, "Add your own loops to the server's backgrounds folder (STORY_BACKGROUNDS_DIR); your uploaded videos appear here too."),
        c.background?.kind === "ai" &&
          h(
            "div",
            { className: "stack tight" },
            h(
              "p",
              { className: "small muted" },
              "Each scene gets its own picture from its “What we see” description, with a slow pan or zoom. Pictures are kept: re-rendering reuses them, and editing one scene’s description redraws only that scene. Local FLUX can take a minute or more per new picture.",
            ),
            h(
              "label",
              { className: "check" },
              h("input", { type: "checkbox", checked: !!c.newImages, disabled: rendering, onChange: (e) => s.setRender({ newImages: e.target.checked }) }),
              "Draw new pictures for every scene",
            ),
          ),
      ),
      h(
        "div",
        { className: "row wrap story-fields" },
        h(
          "div",
          { className: "field" },
          h("label", { htmlFor: "story-music" }, "Music"),
          h(
            "select",
            { id: "story-music", value: c.music || "", disabled: rendering, onChange: (e) => s.setRender({ music: e.target.value || null }) },
            h("option", { value: "" }, "No music"),
            s.music.map((m) => h("option", { key: m.id, value: m.id }, m.label)),
          ),
          !s.music.length && h("p", { className: "small muted" }, "Add royalty-free tracks to the server's music folder (STORY_MUSIC_DIR)."),
        ),
        h(
          "div",
          { className: "field" },
          h("label", { htmlFor: "story-captions" }, "Caption style"),
          h(
            "select",
            { id: "story-captions", value: c.captionPreset, disabled: rendering || !c.captions, onChange: (e) => s.setRender({ captionPreset: e.target.value }) },
            s.captionPresets.map((p) => h("option", { key: p.id, value: p.id }, p.label)),
          ),
          h(
            "label",
            { className: "check" },
            h("input", { type: "checkbox", checked: c.captions, disabled: rendering, onChange: (e) => s.setRender({ captions: e.target.checked }) }),
            "Burn in captions",
          ),
        ),
      ),
      h(
        "div",
        { className: "field" },
        h("span", { className: "field-label" }, "Opening title card (first 2 seconds)"),
        h(Segments, {
          label: "Title card",
          items: [
            ["title", "Title"],
            ["hook", "Hook"],
            ["none", "None"],
          ].map(([id, label]) => ({ id, label, pressed: String(c.titleCard === id), disabled: rendering || !c.captions, pick: () => s.setRender({ titleCard: id }) })),
        }),
      ),
      rendering &&
        h(Progress, {
          value: s.current.renderJob.progress,
          label:
            s.current.renderJob.progress == null
              ? "Rendering your Short… usually under 10 seconds."
              : s.current.renderJob.progress < 0.9
                ? `Drawing scene pictures… ${pct(s.current.renderJob.progress / 0.9)}`
                : "Pictures done. Rendering your Short…",
        }),
      s.current.renderJob.error && h(Notice, { kind: "error" }, s.current.renderJob.error.message),
      h(
        "div",
        { className: "row end" },
        h(Button, { primary: true, icon: "video", onClick: s.render, disabled: !s.canRender }, rendering ? "Rendering…" : latest ? "Render again" : "Render Short"),
      ),
      latest &&
        h(
          "div",
          { className: "story-result stack" },
          h("h3", null, "Final preview"),
          s.imageLinks.length > 0 &&
            h(
              "div",
              { className: "scene-thumbs", "aria-label": "Scene pictures" },
              s.imageLinks.map((link) =>
                h("figure", { key: link.id }, h("img", { src: link.url, alt: `Scene ${link.id} picture`, loading: "lazy" }), h("figcaption", { className: "small muted" }, `Scene ${link.id}`)),
              ),
            ),
          h(VideoPreview, { url: latest.playUrl, label: "Rendered story Short" }),
          h(
            "div",
            { className: "row wrap" },
            h("a", { className: "btn", href: latest.downloadHref, download: latest.filename }, "Download MP4"),
            h(Button, { primary: true, icon: "upload", onClick: () => s.publish(latest) }, "Review & publish"),
            h("span", { className: "small muted" }, `${seconds(latest.durationSec)} · 1080×1920 · kept until ${latest.expiresLabel}`),
          ),
          s.renders.length > 1 &&
            h(
              "details",
              null,
              h("summary", { className: "small" }, `Earlier renders (${s.renders.length - 1})`),
              s.renders.slice(1).map((r) =>
                h("p", { key: r.id, className: "small" }, h("a", { href: r.downloadHref, download: r.filename }, `${r.createdLabel} · ${seconds(r.durationSec)}`)),
              ),
            ),
        ),
    );
  }

  function StoryEditor({ v }) {
    const { Button, ErrorState } = UI();
    const s = v.story;
    if (s.loadError)
      return h("div", { className: "narrow" }, h(ErrorState, { title: "We couldn’t open this story.", message: s.loadError, retry: s.reload }));
    if (!s.current || !s.draft) return h("div", { className: "narrow" }, h("p", { role: "status", className: "muted" }, "Opening story…"));
    return h(
      "div",
      { className: "narrow stack" },
      h(
        "div",
        { className: "row between wrap" },
        h(Button, { ghost: true, icon: "back", onClick: () => v.navigate("stories") }, "All stories"),
        h(Button, { ghost: true, icon: "trash", onClick: s.remove, disabled: s.jobRunning || !!s.busy }, "Delete story"),
      ),
      h(
        "header",
        { className: "row between wrap" },
        h("h1", { id: "page-title", tabIndex: -1 }, s.current.title),
        h(Badge, { status: s.current.status }),
      ),
      h(Steps, { steps: s.steps }),
      s.costLine && h("p", { className: "small muted" }, s.costLine),
      h(ScriptStep, { s }),
      h(VoiceStep, { s }),
      h(RenderStep, { s }),
    );
  }

  root.ShortsStoryUI = { StoryStart, StoryList, StoryEditor };
})(window);
