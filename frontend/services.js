/* AI Shorts Generator — services layer.
   Every backend call in the app goes through an object implementing API_METHODS.
   Two implementations share the contract:
     createHttpApi()  — talks to the FastAPI /v1 surface
     createMockApi()  — in-memory backend with simulated latency, jobs, ledger, expiry
   UI code must only call createApi(...) and the returned methods. */
(function (root, factory) {
  const mod = factory();
  if (typeof module === 'object' && module.exports) module.exports = mod;
  else root.ShortsServices = mod;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const CONFIG = {
    containers: ['mp4', 'mov', 'webm'],
    maxDurationSec: 3600,
    minDurationSec: 30,
    maxBytes: 4 * 1024 ** 3,
    maxDimension: 7680,
    maxFps: 120,
    clipMinSec: 15,
    clipMaxSec: 90,
    monthlyMinutes: 60,
    weights: { hook: 0.3, value: 0.3, standalone: 0.25, visual: 0.15 },
    hookMaxLen: 80,
    reasonMaxLen: 200,
    instructionMaxLen: 200,
    avoidMaxItems: 10,
    avoidItemMaxLen: 40,
    maxOverlap: 0.3,
    goals: ['educational', 'entertaining', 'viral'],
    retention: { sourceHours: 24, analysisDays: 30, historyDays: 90 },
    uploadTtlMin: 15,
  };

  const ERROR_COPY = {
    UNSUPPORTED_MEDIA: 'This file type or stream isn’t supported. Upload an MP4, MOV or WebM.',
    SOURCE_TOO_LONG: 'Sources can be up to 60 minutes. Trim the video and try again.',
    SOURCE_TOO_SHORT: 'Sources need at least 30 seconds of video.',
    SOURCE_TOO_LARGE: 'Files can be up to 4 GB. Export a smaller file and try again.',
    MEDIA_PROBE_FAILED: 'We couldn’t read this video. Re-export it and try again.',
    TRANSCRIPTION_FAILED: 'Transcription failed. Retry — your upload is kept.',
    ANALYSIS_FAILED: 'Finding clips failed. Retry — your transcript is kept.',
    IMPORT_UNAVAILABLE: 'URL import isn’t available right now. Upload the video file instead.',
    UNSUPPORTED_URL: 'Only public youtube.com or youtu.be video links are supported.',
    RIGHTS_NOT_CONFIRMED: 'Confirm you have the rights to reuse this video.',
    MONTHLY_LIMIT_REACHED: 'This would exceed your 60 processing minutes for the month.',
    ACTIVE_JOB_EXISTS: 'You already have a video processing. Wait for it to finish.',
    ASSET_EXPIRED: 'These files have expired under the retention policy.',
    NOT_FOUND: 'We couldn’t find that project.',
    NOT_INVITED: 'This Google account isn’t on the private beta list.',
    ACCOUNT_DISABLED: 'This account has been disabled. Contact the beta team.',
    UNAUTHENTICATED: 'Your session ended. Sign in again.',
    INVALID_INPUT: 'Some of the details aren’t valid.',
    INVALID_STATE: 'That action isn’t available right now.',
    NETWORK_ERROR: 'Can’t reach the server. Check your connection.',
    SERVER_ERROR: 'Something went wrong on our side. Try again.',
  };

  class ApiError extends Error {
    constructor(code, message, status, detail) {
      super(message || ERROR_COPY[code] || code);
      this.name = 'ApiError';
      this.code = code;
      this.status = status || 400;
      this.detail = detail;
    }
  }

  // [method, HTTP verb, path]. {id} = first positional argument.
  const ENDPOINTS = [
    ['signIn', 'POST', '/v1/auth/google'],
    ['signOut', 'POST', '/v1/auth/logout'],
    ['me', 'GET', '/v1/me'],
    ['createUpload', 'POST', '/v1/uploads'],
    ['completeUpload', 'POST', '/v1/uploads/{id}/complete'],
    ['createYoutubeProject', 'POST', '/v1/projects/youtube'],
    ['listProjects', 'GET', '/v1/projects'],
    ['getProject', 'GET', '/v1/projects/{id}'],
    ['deleteProject', 'DELETE', '/v1/projects/{id}'],
    ['processProject', 'POST', '/v1/projects/{id}/process'],
    ['retryProject', 'POST', '/v1/projects/{id}/retry'],
    ['cancelProject', 'POST', '/v1/projects/{id}/cancel'],
    ['getStatus', 'GET', '/v1/projects/{id}/status'],
    ['getTranscript', 'GET', '/v1/projects/{id}/transcript'],
    ['saveTranscript', 'PATCH', '/v1/projects/{id}/transcript'],
    ['getProjectDownloads', 'GET', '/v1/projects/{id}/downloads'],
    ['getClips', 'GET', '/v1/projects/{id}/clips'],
    ['createAnalysisRun', 'POST', '/v1/projects/{id}/analysis-runs'],
    ['getUsage', 'GET', '/v1/usage'],
  ];
  const API_METHODS = ENDPOINTS.map((e) => e[0]);

  /* ---------- pure helpers (shared, tested) ---------- */

  function suggestionCount(durationSec) {
    const m = durationSec / 60;
    if (m < 10) return 3;
    if (m < 20) return 5;
    if (m < 40) return 7;
    return 10;
  }

  function combinedScore(s, w = CONFIG.weights) {
    return Math.round(s.hook * w.hook + s.value * w.value + s.standalone * w.standalone + s.visual * w.visual);
  }

  function overlapRatio(a, b) {
    const ov = Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));
    return ov / Math.min(a.end - a.start, b.end - b.start);
  }

  function checkClip(c, durationSec, cfg) {
    if (!c || typeof c !== 'object') return 'NOT_AN_OBJECT';
    if (!Number.isFinite(c.start) || !Number.isFinite(c.end)) return 'MISSING_TIMESTAMPS';
    if (c.start < 0) return 'START_NEGATIVE';
    if (c.end > durationSec) return 'END_AFTER_SOURCE';
    if (c.end <= c.start) return 'END_BEFORE_START';
    const d = c.end - c.start;
    if (d < cfg.clipMinSec || d > cfg.clipMaxSec) return 'DURATION_OUT_OF_RANGE';
    const s = c.scores || {};
    for (const k of ['hook', 'value', 'standalone', 'visual']) {
      if (!Number.isFinite(s[k]) || s[k] < 0 || s[k] > 100) return 'SCORE_OUT_OF_RANGE';
    }
    if (typeof c.hook !== 'string' || !c.hook.trim() || c.hook.length > cfg.hookMaxLen) return 'INVALID_HOOK';
    if (typeof c.reason !== 'string' || !c.reason.trim() || c.reason.length > cfg.reasonMaxLen) return 'INVALID_REASON';
    return null;
  }

  // LLM output is untrusted input: validate before anything downstream sees it.
  function validateClips(raw, durationSec, cfg = CONFIG) {
    const accepted = [], rejected = [];
    if (!raw || !Array.isArray(raw.clips)) return { accepted, rejected: [{ clip: raw, reason: 'MISSING_CLIPS' }] };
    for (const c of raw.clips) {
      let why = checkClip(c, durationSec, cfg);
      if (!why && accepted.some((a) => overlapRatio(a, c) > cfg.maxOverlap)) why = 'OVERLAP';
      if (why) rejected.push({ clip: c, reason: why });
      else accepted.push(c);
    }
    return { accepted, rejected };
  }

  function validateGuidance(body) {
    const goal = body && body.goal;
    if (!CONFIG.goals.includes(goal)) throw new ApiError('INVALID_INPUT', 'Choose Educational, Entertaining or Viral.', 422);
    const instruction = String((body && body.instruction) || '').trim();
    if (instruction.length > CONFIG.instructionMaxLen) throw new ApiError('INVALID_INPUT', 'Keep the instruction under 200 characters.', 422);
    const avoid = ((body && body.avoid) || []).map((s) => String(s).trim()).filter(Boolean);
    if (avoid.length > CONFIG.avoidMaxItems || avoid.some((s) => s.length > CONFIG.avoidItemMaxLen)) {
      throw new ApiError('INVALID_INPUT', 'Up to 10 topics to avoid, 40 characters each.', 422);
    }
    return { goal, instruction, avoid };
  }

  // Map edited segment text back onto original word timings; never invent timing.
  function alignSegment(seg, newText) {
    const toks = newText.trim().split(/\s+/);
    if (toks.length === seg.words.length) {
      return { words: toks.map((w, i) => ({ w, s: seg.words[i].s, e: seg.words[i].e })), timing: 'word' };
    }
    const d = (seg.end - seg.start) / toks.length;
    return { words: toks.map((w, i) => ({ w, s: r2(seg.start + i * d), e: r2(seg.start + (i + 1) * d) })), timing: 'segment' };
  }

  const r1 = (n) => Math.round(n * 10) / 10;
  const r2 = (n) => Math.round(n * 100) / 100;
  const clone = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));
  const HOUR = 3600e3, DAY = 24 * HOUR;

  /* ---------- HTTP implementation ---------- */

  function createHttpApi({ baseUrl = '', fetch: f, headers = {} } = {}) {
    const doFetch = f || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null);
    const origin = baseUrl.replace(/\/+$/, '');
    // Tokens stay in memory. Refreshing the page requires signing in again.
    let accessToken = null;
    let sessionGeneration = 0;
    const api = {};
    for (const [name, verb, path] of ENDPOINTS) {
      const hasId = path.includes('{id}');
      api[name] = async (...args) => {
        const id = hasId ? args.shift() : null;
        const body = args[0];
        const opts = args[1] || {};
        const url = origin + (hasId ? path.replace('{id}', encodeURIComponent(id)) : path);
        if (name === 'signIn' || name === 'signOut') sessionGeneration++;
        const requestGeneration = sessionGeneration;
        if (name === 'signIn') accessToken = null;
        const h = { Accept: 'application/json', ...headers };
        if (accessToken) h.Authorization = 'Bearer ' + accessToken;
        if (name === 'signOut') accessToken = null;
        const sendBody = body !== undefined && verb !== 'GET';
        if (sendBody) h['Content-Type'] = 'application/json';
        if (opts.idempotencyKey) h['Idempotency-Key'] = opts.idempotencyKey;
        try {
          let res;
          try {
            res = await doFetch(url, { method: verb, headers: h, credentials: 'include', body: sendBody ? JSON.stringify(body) : undefined });
          } catch (e) {
            throw new ApiError('NETWORK_ERROR', null, 0);
          }
          const text = await res.text();
          if (requestGeneration !== sessionGeneration) {
            throw new ApiError('SESSION_CHANGED', 'Your session changed. Please try again.', 409);
          }
          let data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
          if (!res.ok) {
            const err = (data && data.error) || {};
            const fallback = res.status === 401 ? 'UNAUTHENTICATED' : res.status === 404 ? 'NOT_FOUND' : res.status >= 500 ? 'SERVER_ERROR' : 'INVALID_INPUT';
            const code = err.code || fallback;
            if (['UNAUTHENTICATED', 'ACCOUNT_DISABLED', 'NOT_INVITED'].includes(code)) accessToken = null;
            throw new ApiError(code, err.message, res.status);
          }
          if (name === 'signIn') {
            if (!data || typeof data.access_token !== 'string' || !data.access_token) {
              throw new ApiError('SERVER_ERROR', 'The server did not return a sign-in token.', 502);
            }
            accessToken = data.access_token;
          }
          return data;
        } finally {
          // A disconnected server must not leave the browser signed in locally.
          if (name === 'signOut' && requestGeneration === sessionGeneration) accessToken = null;
        }
      };
    }
    return api;
  }

  /* ---------- Sample content for the mock (business interview) ---------- */

  const SAMPLE = {
    title: 'The Operator Hour — Ep. 42: Priya Raman on scaling to 500',
    fileName: 'operator-hour-ep42.mp4',
    durationSec: 1934, width: 1920, height: 1080, fps: 30, sizeBytes: 1.86e9,
    paragraphs: [
      'Welcome back to The Operator Hour. I’m Maya Ortiz, and today I’m sitting down with Priya Raman, who took Fieldline from ten people to five hundred in four years.',
      'Most founders hire backwards. They hire for the company they have today, and six months later every one of those people is underwater. Hire for the company you will be in eighteen months.',
      'Here is the framework I use for every senior hire. Write down the three decisions this person will own, what a great first quarter looks like, and who they will disagree with.',
      'Honestly, my worst hire was my best friend. We had the same instincts, the same blind spots, and for a year nobody in the room was willing to tell us we were wrong.',
      'The lesson was simple. Your leadership team should argue in the meeting and commit after it. If nobody pushes back, you are not getting judgment, you are getting agreement.',
      'People ask whether remote work killed culture. I think the opposite. Remote work exposed which companies never wrote their culture down in the first place.',
      'Pricing is the unglamorous lever nobody wants to touch. We raised prices forty percent in one quarter and lost almost no customers, because we finally charged for the value.',
      'Our first board deck was twelve slides in a shared doc. No design, no animation. Revenue, burn, the one thing that broke, and what we needed from them.',
      'The real cost of growth was not salaries. It was the meetings. At two hundred people, every decision needed six calendars, and we slowed to a crawl without noticing.',
      'My hot take: most companies should never have a head of innovation. Innovation is a job for the people closest to customers, not a department down the hall.',
      'We tried replacing sales calls with a self-serve funnel. Signups tripled, revenue went flat. People loved the free tier and never talked to anyone who could help them buy.',
      'If you remember one thing, fix the boring process first. Onboarding, invoicing, handoffs. Clever strategy on top of a broken process just fails faster.',
      'Writing things down saved us more than once. Every big decision has a one-page memo with the date and the owner, so when something goes wrong we can find out why.',
      'The strangest week I had was when a customer threatened to leave because our invoices were too polite. They thought we were about to go out of business.',
      'For founders starting today: pick one customer, one metric and one quarter. If it does not move the metric, stop, and try the next customer.',
      'That’s the episode. If this was useful, send it to the founder who keeps asking which org chart to copy. The answer is still: start with the work.',
    ],
  };

  // Candidate moments the mock "model" can find, keyed to transcript segments.
  const CLIP_LIBRARY = [
    { key: 'hire-backwards', seg: 1, len: 46, tags: ['hiring', 'career', 'contrarian'], hook: 'Most founders hire backwards', s: { hook: 94, value: 89, standalone: 93, visual: 84 }, aff: { educational: 0.9, entertaining: 0.5, viral: 0.95 }, reason: 'A complete contrarian argument with an immediate hook and a clear takeaway.' },
    { key: 'best-friend', seg: 3, len: 38, tags: ['story', 'hiring', 'failure'], hook: 'My worst hire was my best friend', s: { hook: 96, value: 72, standalone: 88, visual: 82 }, aff: { educational: 0.5, entertaining: 0.98, viral: 0.9 }, reason: 'A personal, surprising story that lands in under 40 seconds.' },
    { key: 'framework', seg: 2, len: 52, tags: ['framework', 'hiring', 'leadership'], hook: 'The 3-question test for every senior hire', s: { hook: 78, value: 95, standalone: 90, visual: 84 }, aff: { educational: 0.98, entertaining: 0.3, viral: 0.6 }, reason: 'A reusable framework stated in three concrete steps.' },
    { key: 'remote', seg: 5, len: 34, tags: ['remote', 'culture', 'debate'], hook: 'Remote work didn’t kill culture — it exposed it', s: { hook: 90, value: 84, standalone: 86, visual: 80 }, aff: { educational: 0.7, entertaining: 0.5, viral: 0.93 }, reason: 'A debate-worthy claim with the speaker’s reasoning attached.' },
    { key: 'pricing', seg: 6, len: 41, tags: ['pricing', 'growth'], hook: 'We raised prices 40% and lost almost no one', s: { hook: 91, value: 90, standalone: 87, visual: 81 }, aff: { educational: 0.88, entertaining: 0.5, viral: 0.85 }, reason: 'A specific, verifiable result from the guest with a clear lesson.' },
    { key: 'innovation', seg: 9, len: 33, tags: ['leadership', 'contrarian'], hook: 'Your company doesn’t need a head of innovation', s: { hook: 88, value: 83, standalone: 91, visual: 83 }, aff: { educational: 0.7, entertaining: 0.55, viral: 0.9 }, reason: 'Short, standalone opinion that invites discussion.' },
    { key: 'self-serve', seg: 10, len: 44, tags: ['story', 'sales', 'growth'], hook: 'Signups tripled. Revenue went flat.', s: { hook: 92, value: 80, standalone: 84, visual: 78 }, aff: { educational: 0.75, entertaining: 0.8, viral: 0.82 }, reason: 'A counter-intuitive result told as a mini story.' },
    { key: 'meetings', seg: 8, len: 36, tags: ['growth', 'operations'], hook: 'The real cost of growth isn’t salaries', s: { hook: 85, value: 86, standalone: 82, visual: 77 }, aff: { educational: 0.85, entertaining: 0.4, viral: 0.7 }, reason: 'Names a hidden cost operators recognise immediately.' },
    { key: 'polite-invoices', seg: 13, len: 30, tags: ['humor', 'story', 'customers'], hook: 'A customer almost left because our invoices were too polite', s: { hook: 87, value: 55, standalone: 80, visual: 76 }, aff: { educational: 0.2, entertaining: 0.95, viral: 0.72 }, reason: 'A funny, self-contained anecdote with a clean ending.' },
    { key: 'one-customer', seg: 14, len: 29, tags: ['advice', 'career', 'framework'], hook: 'One customer, one metric, one quarter', s: { hook: 80, value: 92, standalone: 89, visual: 80 }, aff: { educational: 0.92, entertaining: 0.3, viral: 0.6 }, reason: 'Actionable closing advice that stands alone.' },
  ];

  function buildTranscript(durationSec) {
    const n = SAMPLE.paragraphs.length;
    const segLen = durationSec / n;
    const segments = SAMPLE.paragraphs.map((text, i) => {
      const start = r2(i * segLen);
      const end = r2((i + 1) * segLen - Math.min(1.5, segLen * 0.05));
      const toks = text.split(/\s+/);
      const d = (end - start) / toks.length;
      return { id: 'seg_' + i, start, end, text, timing: 'word', words: toks.map((w, j) => ({ w, s: r2(start + j * d), e: r2(start + (j + 1) * d) })) };
    });
    return { language: 'en', revision: 1, userCorrected: false, segments };
  }

  // Simulates the LLM call: returns raw (unvalidated) structured output.
  function mockModelOutput(transcript, durationSec, params) {
    const segs = transcript.segments;
    const instr = (params.instruction || '').toLowerCase();
    const avoid = (params.avoid || []).map((a) => a.toLowerCase()).filter((a) => a.length >= 3);
    const out = [];
    for (const lib of CLIP_LIBRARY) {
      const seg = segs[lib.seg];
      if (!seg) continue;
      const hay = (lib.tags.join(' ') + ' ' + lib.hook + ' ' + seg.text).toLowerCase();
      if (avoid.some((a) => hay.includes(a))) continue;
      const aff = lib.aff[params.goal];
      const boost = instr && lib.tags.some((t) => instr.includes(t)) ? 1 : 0;
      const scale = (v) => Math.min(99, Math.round(v * (0.82 + 0.2 * aff)) + boost * 4);
      // snap to word boundaries: start on a word start, end on a word end
      // start on the segment's first word; end on the last sentence end inside the target window
      const firstWord = seg.words[0];
      const targetEnd = Math.min(firstWord.s + lib.len, seg.end);
      const minEnd = firstWord.s + CONFIG.clipMinSec;
      const sentenceEnd = (limit) => [...seg.words].reverse().find((w) => /[.!?]["”’]?$/.test(w.w) && w.e <= limit + 0.01 && w.e >= minEnd);
      const lastWord = sentenceEnd(targetEnd) || sentenceEnd(firstWord.s + CONFIG.clipMaxSec) || [...seg.words].reverse().find((w) => w.e <= targetEnd + 0.01) || seg.words[seg.words.length - 1];
      const scores = { hook: scale(lib.s.hook), value: scale(lib.s.value), standalone: lib.s.standalone, visual: lib.s.visual };
      out.push({ start: firstWord.s, end: lastWord.e, hook: lib.hook, scores, reason: lib.reason, _key: lib.key, _tags: lib.tags, _rank: combinedScore(scores) * aff + boost * 60 });
    }
    return { clips: out };
  }

  /* ---------- Mock implementation ---------- */

  function createMockApi(opts = {}) {
    const now = opts.now || (() => Date.now());
    const rand = opts.random || Math.random;
    const timing = () => ({ latency: 0, transcribing: 4000, analyzing: 3000, ...(typeof opts.timing === 'function' ? opts.timing() : opts.timing || {}) });
    const features = () => ({ youtubeImport: false, ...(typeof opts.features === 'function' ? opts.features() : opts.features || {}) });
    const failQueue = [];
    const shouldFail = (stage) => {
      const i = failQueue.indexOf(stage);
      if (i >= 0) { failQueue.splice(i, 1); return true; }
      return opts.shouldFail ? !!opts.shouldFail(stage) : false;
    };
    const oid = (p) => p + '_' + Array.from({ length: 12 }, () => Math.floor(rand() * 16).toString(16)).join('');

    const db = { users: {}, invites: [], projects: {}, uploads: {}, transcripts: {}, runs: {}, clips: {}, jobs: {}, ledger: [], ledgerKeys: new Set(), idem: {}, audit: [] };
    let session = null;

    const addUser = (email, name, role) => {
      const u = { id: oid('usr'), googleSubject: oid('g'), email, displayName: name, role, state: 'active', limitMinutes: CONFIG.monthlyMinutes };
      db.users[u.id] = u;
      return u;
    };
    const invite = (email, status) => db.invites.push({ id: oid('inv'), email, status, createdAt: now() });

    const audit = (actor, type, targetType, targetId) => db.audit.push({ id: oid('aud'), actorUserId: actor, eventType: type, targetType, targetId, createdAt: now() });

    const monthStart = () => { const d = new Date(now()); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1); };
    const usedSeconds = (uid) => db.ledger.filter((e) => e.userId === uid && e.createdAt >= monthStart()).reduce((a, e) => a + e.processedSeconds + e.adjustmentSeconds, 0);
    const usageView = (u) => {
      const used = Math.ceil(usedSeconds(u.id) / 60);
      return { usedMinutes: used, limitMinutes: u.limitMinutes, remainingMinutes: Math.max(0, u.limitMinutes - used) };
    };
    const assertQuota = (u, addSec) => {
      if (Math.ceil((usedSeconds(u.id) + addSec) / 60) > u.limitMinutes) throw new ApiError('MONTHLY_LIMIT_REACHED', null, 402);
    };
    // Append-only; one charge per (project, usage type) so retries never double-count.
    const charge = (p, job, type, seconds, at) => {
      const key = type + ':' + p.id;
      if (db.ledgerKeys.has(key)) return;
      db.ledgerKeys.add(key);
      db.ledger.push({ id: oid('led'), userId: p.userId, projectId: p.id, jobId: job ? job.id : null, usageType: type, processedSeconds: Math.round(seconds), adjustmentSeconds: 0, createdAt: at || now() });
    };

    const requireUser = () => {
      const u = session && db.users[session];
      if (!u) throw new ApiError('UNAUTHENTICATED', null, 401);
      if (u.state !== 'active') throw new ApiError('ACCOUNT_DISABLED', null, 403);
      return u;
    };
    // Ownership check on every resource; foreign IDs look identical to missing ones.
    const ownProject = (u, id) => {
      const p = db.projects[id];
      if (!p || p.userId !== u.id || p.status === 'deleted') throw new ApiError('NOT_FOUND', null, 404);
      return p;
    };
    const activeJobFor = (uid) => Object.values(db.jobs).find((j) => j.userId === uid && j.state === 'running');
    const projectJob = (p) => Object.values(db.jobs).find((j) => j.projectId === p.id && j.state === 'running');

    const newProject = (u, fields) => {
      const p = {
        id: oid('prj'), userId: u.id, sourceType: 'upload', name: fields.name, status: 'uploading', sourceReady: false,
        durationSec: fields.durationSec, width: fields.width, height: fields.height, fps: fields.fps, sizeBytes: fields.sizeBytes || 0,
        language: null, goal: null, instruction: '', avoid: [], createdAt: now(), currentRunId: null, clipsStale: false,
        failureCode: null, failedStage: null, sourceExpiresAt: null, analysisExpiresAt: null, historyExpiresAt: now() + CONFIG.retention.historyDays * DAY,
        stats: { transcriptionRuns: 0, analysisRuns: 0 }, ...fields.extra,
      };
      db.projects[p.id] = p;
      return p;
    };

    const runAnalysis = (p, params) => {
      const t = db.transcripts[p.id];
      const raw = mockModelOutput(t, p.durationSec, params);
      const { accepted, rejected } = validateClips(raw, p.durationSec);
      accepted.sort((a, b) => b._rank - a._rank);
      const chosen = accepted.slice(0, suggestionCount(p.durationSec));
      const run = { id: oid('run'), projectId: p.id, ...params, transcriptRevision: t.revision, promptVersion: 'clips-v3', modelProvider: 'mock', modelName: 'mock-structured-1', rejectedCount: rejected.length, clipIds: [], createdAt: now() };
      chosen.forEach((c, i) => {
        const clip = { id: oid('clp'), projectId: p.id, runId: run.id, rank: i + 1, start: c.start, end: c.end, duration: r1(c.end - c.start), hook: c.hook, scores: c.scores, combined: combinedScore(c.scores), reason: c.reason, tags: c._tags, key: c._key };
        db.clips[clip.id] = clip;
        run.clipIds.push(clip.id);
      });
      db.runs[run.id] = run;
      p.currentRunId = run.id;
      p.goal = params.goal; p.instruction = params.instruction; p.avoid = params.avoid;
      p.clipsStale = false;
      p.stats.analysisRuns++;
    };

    const STAGE_CODES = { transcribing: 'TRANSCRIPTION_FAILED', analyzing: 'ANALYSIS_FAILED' };
    const STAGE_FAILKEY = { transcribing: 'transcription', analyzing: 'analysis' };

    const completeStage = (job, name, at) => {
      const p = db.projects[job.projectId];
      if (name === 'transcribing') {
        db.transcripts[p.id] = buildTranscript(p.durationSec);
        p.language = 'en';
        p.stats.transcriptionRuns++;
        charge(p, job, 'transcription', p.durationSec, at);
      } else if (name === 'analyzing') {
        runAnalysis(p, job.params);
        p.status = 'ready';
        p.failureCode = null; p.failedStage = null;
        if (!p.sourceExpiresAt) p.sourceExpiresAt = at + CONFIG.retention.sourceHours * HOUR;
        p.analysisExpiresAt = at + CONFIG.retention.analysisDays * DAY;
      }
    };

    const advanceJob = (job) => {
      const p = db.projects[job.projectId];
      let t = now() - job.startedAt;
      let at = job.startedAt;
      for (const st of job.stages) {
        if (st.done) { t -= st.ms; at += st.ms; continue; }
        if (t < st.ms) {
          job.progressStage = st.name;
          job.stageProgress = Math.max(0, t / st.ms);
          p.status = st.name;
          return;
        }
        at += st.ms;
        if (shouldFail(STAGE_FAILKEY[st.name])) {
          job.state = 'failed_retryable'; job.failureCode = STAGE_CODES[st.name]; job.finishedAt = at;
          if (job.type === 'analysis' && db.transcripts[p.id] && p.currentRunId) { p.status = 'ready'; p.regenError = 'ANALYSIS_FAILED'; }
          else { p.status = 'failed'; p.failureCode = job.failureCode; p.failedStage = st.name; }
          return;
        }
        completeStage(job, st.name, at);
        st.done = true;
        t -= st.ms;
      }
      job.state = 'succeeded'; job.finishedAt = at; job.stageProgress = 1;
    };

    const tick = () => {
      for (const j of Object.values(db.jobs)) if (j.state === 'running') advanceJob(j);
      const t = now();
      for (const p of Object.values(db.projects)) {
        if (p.status === 'deleted') continue;
        if (p.sourceExpiresAt && t > p.sourceExpiresAt) p.sourceExpired = true;
        if (p.analysisExpiresAt && t > p.analysisExpiresAt) p.status = 'expired';
      }
    };

    const startJob = (u, p, type, stageNames, params, idempotencyKey) => {
      if (activeJobFor(u.id)) throw new ApiError('ACTIVE_JOB_EXISTS', null, 409);
      const tm = timing();
      const job = {
        id: oid('job'), projectId: p.id, userId: u.id, type, state: 'running', attempt: (p.attempts = (p.attempts || 0) + 1),
        idempotencyKey: idempotencyKey || null, params, stages: stageNames.map((n) => ({ name: n, ms: tm[n], done: false })),
        progressStage: stageNames[0], stageProgress: 0, failureCode: null, queuedAt: now(), startedAt: now(), finishedAt: null,
      };
      db.jobs[job.id] = job;
      p.status = stageNames[0];
      p.regenError = null;
      return job;
    };

    const withIdempotency = (u, key, fn) => {
      if (!key) return fn();
      const k = u.id + ':' + key;
      if (db.idem[k]) return db.idem[k]();
      const result = fn();
      db.idem[k] = () => result.replay();
      return result;
    };

    const STAGE_WEIGHTS = { transcribing: [0, 0.6], analyzing: [0.6, 1] };
    const statusView = (p) => {
      const job = projectJob(p) || Object.values(db.jobs).filter((j) => j.projectId === p.id).sort((a, b) => b.queuedAt - a.queuedAt)[0];
      let progress = p.status === 'ready' ? 1 : 0;
      if (job && job.state === 'running') {
        const [a, b] = job.type === 'analysis' ? [0, 1] : STAGE_WEIGHTS[job.progressStage];
        progress = a + (b - a) * job.stageProgress;
      }
      return {
        projectId: p.id, status: p.status, jobId: job ? job.id : null, jobType: job ? job.type : null,
        stage: job && job.state === 'running' ? job.progressStage : null, progress: r2(progress),
        failureCode: p.failureCode, regenError: p.regenError || null,
        canCancel: !!(job && job.state === 'running' && job.type === 'process'),
        canRetry: p.status === 'failed',
      };
    };

    const projectView = (p) => ({
      id: p.id, name: p.name, sourceType: p.sourceType, status: p.status, durationSec: p.durationSec, width: p.width, height: p.height, fps: p.fps,
      sizeBytes: p.sizeBytes, language: p.language, goal: p.goal, instruction: p.instruction, avoid: p.avoid, createdAt: p.createdAt,
      suggestionCount: p.currentRunId ? db.runs[p.currentRunId].clipIds.length : 0, renderCount: p.renderCount || 0,
      sourceExpiresAt: p.sourceExpiresAt, sourceExpired: !!p.sourceExpired, analysisExpiresAt: p.analysisExpiresAt,
      clipsStale: p.clipsStale, failureCode: p.failureCode, transcriptRevision: db.transcripts[p.id] ? db.transcripts[p.id].revision : null,
    });

    const transcriptView = (p) => {
      const t = db.transcripts[p.id];
      return { projectId: p.id, language: t.language, revision: t.revision, userCorrected: t.userCorrected, segments: t.segments.map((s) => ({ id: s.id, start: s.start, end: s.end, text: s.text, timing: s.timing, words: s.words })) };
    };

    const requireTranscript = (p) => {
      if (p.status === 'expired') throw new ApiError('ASSET_EXPIRED', null, 410);
      if (!db.transcripts[p.id]) throw new ApiError('INVALID_STATE', 'The transcript isn’t ready yet.', 409);
      return db.transcripts[p.id];
    };

    const meView = (u) => {
      const j = activeJobFor(u.id);
      return {
        user: { id: u.id, email: u.email, displayName: u.displayName, role: u.role },
        features: features(), usage: usageView(u),
        activeJob: j ? { id: j.id, projectId: j.projectId, type: j.type, stage: j.progressStage } : null,
      };
    };

    async function op(fn) {
      const ms = timing().latency;
      if (ms > 0) await new Promise((r) => setTimeout(r, ms * (0.6 + rand() * 0.8)));
      else await Promise.resolve();
      tick();
      return clone(fn());
    }

    /* seed */
    const maya = addUser('maya@example.com', 'Maya Ortiz', 'creator');
    const dev = addUser('jon@example.com', 'Jon Park', 'creator');
    const ops = addUser('ops@example.com', 'Beta Ops', 'operator');
    [maya, dev, ops].forEach((u) => invite(u.email, 'active'));
    invite('sam@example.com', 'revoked');

    if (opts.seed !== false) {
      const seedReady = (u, name, durationSec, daysAgo, renders) => {
        const at = now() - daysAgo * DAY;
        const p = newProject(u, { name, durationSec, width: 1920, height: 1080, fps: 30, sizeBytes: durationSec * 0.9e6 });
        p.createdAt = at; p.sourceReady = true;
        db.transcripts[p.id] = buildTranscript(durationSec);
        p.language = 'en'; p.stats.transcriptionRuns = 1;
        charge(p, null, 'transcription', durationSec, at);
        runAnalysis(p, { goal: 'educational', instruction: '', avoid: [] });
        p.status = 'ready';
        p.sourceExpiresAt = at + CONFIG.retention.sourceHours * HOUR;
        p.analysisExpiresAt = at + CONFIG.retention.analysisDays * DAY;
        p.renderCount = renders;
        return p;
      };
      seedReady(maya, 'The Operator Hour — Ep. 41: Live Q&A', 1100, 2, 2);
      seedReady(maya, 'The Operator Hour — Ep. 38: Hiring in 2026', 2460, 40, 3);
      seedReady(dev, 'Jon’s private strategy call', 900, 1, 0);
      tick();
    }

    const api = {
      signIn: (body) => op(() => {
        const email = String((body && body.email) || '').toLowerCase();
        const u = Object.values(db.users).find((x) => x.email === email);
        const inv = db.invites.find((i) => i.email === email && i.status === 'active');
        if (!u || !inv) { audit(null, 'signin_rejected', 'invite', null); throw new ApiError('NOT_INVITED', null, 403); }
        if (u.state !== 'active') throw new ApiError('ACCOUNT_DISABLED', null, 403);
        session = u.id;
        audit(u.id, 'signin', 'user', u.id);
        return meView(u);
      }),
      signOut: () => op(() => { session = null; return { ok: true }; }),
      me: () => op(() => meView(requireUser())),

      createUpload: (body) => op(() => {
        const u = requireUser();
        const m = body || {};
        const ext = String(m.fileName || '').split('.').pop().toLowerCase();
        if (!CONFIG.containers.includes(ext) || (m.mimeType && !/^video\//.test(m.mimeType))) throw new ApiError('UNSUPPORTED_MEDIA', null, 415);
        if (m.sizeBytes > CONFIG.maxBytes) throw new ApiError('SOURCE_TOO_LARGE', null, 413);
        if (!m.hasVideo || !(m.durationSec > 0)) throw new ApiError('MEDIA_PROBE_FAILED', null, 422);
        if (m.width > CONFIG.maxDimension || m.height > CONFIG.maxDimension || m.fps > CONFIG.maxFps) throw new ApiError('UNSUPPORTED_MEDIA', null, 415);
        if (m.durationSec > CONFIG.maxDurationSec) throw new ApiError('SOURCE_TOO_LONG', null, 422);
        if (m.durationSec < CONFIG.minDurationSec) throw new ApiError('SOURCE_TOO_SHORT', null, 422);
        assertQuota(u, m.durationSec);
        const p = newProject(u, { name: m.title || String(m.fileName).replace(/\.[^.]+$/, ''), durationSec: r1(m.durationSec), width: m.width, height: m.height, fps: m.fps, sizeBytes: m.sizeBytes });
        const up = { id: oid('upl'), projectId: p.id, userId: u.id, url: 'mock://upload/' + oid('obj'), expiresAt: now() + CONFIG.uploadTtlMin * 60e3 };
        db.uploads[up.id] = up;
        return { uploadId: up.id, projectId: p.id, uploadUrl: up.url, expiresAt: up.expiresAt, project: projectView(p) };
      }),
      completeUpload: (uploadId) => op(() => {
        const u = requireUser();
        const up = db.uploads[uploadId];
        if (!up || up.userId !== u.id) throw new ApiError('NOT_FOUND', null, 404);
        if (now() > up.expiresAt) throw new ApiError('INVALID_STATE', 'The upload link expired. Start again.', 409);
        const p = ownProject(u, up.projectId);
        p.sourceReady = true;
        return projectView(p);
      }),
      createYoutubeProject: (body) => op(() => {
        const u = requireUser();
        if (!features().youtubeImport) throw new ApiError('IMPORT_UNAVAILABLE', null, 503);
        let url;
        try { url = new URL(String(body && body.url)); } catch (e) { throw new ApiError('UNSUPPORTED_URL', null, 422); }
        let vid = null;
        if (url.protocol === 'https:' && ['www.youtube.com', 'youtube.com', 'm.youtube.com'].includes(url.hostname) && url.pathname === '/watch') vid = url.searchParams.get('v');
        if (url.protocol === 'https:' && url.hostname === 'youtu.be') vid = url.pathname.slice(1);
        if (!vid || !/^[\w-]{6,15}$/.test(vid)) throw new ApiError('UNSUPPORTED_URL', null, 422);
        if (!(body && body.rightsConfirmed)) throw new ApiError('RIGHTS_NOT_CONFIRMED', null, 422);
        assertQuota(u, SAMPLE.durationSec);
        const p = newProject(u, { name: 'YouTube · ' + vid, durationSec: SAMPLE.durationSec, width: 1920, height: 1080, fps: 30, extra: { sourceType: 'youtube_url', sourceUrl: 'https://www.youtube.com/watch?v=' + vid, sourceReady: true } });
        return projectView(p);
      }),

      listProjects: () => op(() => {
        const u = requireUser();
        return Object.values(db.projects).filter((p) => p.userId === u.id && p.status !== 'deleted').sort((a, b) => b.createdAt - a.createdAt).map(projectView);
      }),
      getProject: (id) => op(() => projectView(ownProject(requireUser(), id))),
      deleteProject: (id) => op(() => {
        const u = requireUser();
        const p = ownProject(u, id);
        const j = projectJob(p);
        if (j) { j.state = 'canceled'; j.finishedAt = now(); }
        p.status = 'deleted'; p.deletedAt = now(); p.deletionQueued = true;
        audit(u.id, 'project_deleted', 'project', p.id);
        return { ok: true, deletionQueued: true };
      }),

      processProject: (id, body, o) => op(() => {
        const u = requireUser();
        const p = ownProject(u, id);
        return withIdempotency(u, o && o.idempotencyKey, () => {
          if (p.status !== 'uploading' || !p.sourceReady) throw new ApiError('INVALID_STATE', 'Finish the upload first.', 409);
          const params = validateGuidance(body);
          assertQuota(u, p.durationSec);
          startJob(u, p, 'process', ['transcribing', 'analyzing'], params, o && o.idempotencyKey);
          return { replay: () => statusView(p), ...statusView(p) };
        });
      }).then(stripReplay),
      retryProject: (id, body, o) => op(() => {
        const u = requireUser();
        const p = ownProject(u, id);
        return withIdempotency(u, o && o.idempotencyKey, () => {
          if (p.status !== 'failed') throw new ApiError('INVALID_STATE', 'Only failed projects can be retried.', 409);
          const prev = Object.values(db.jobs).filter((j) => j.projectId === p.id).sort((a, b) => b.queuedAt - a.queuedAt)[0];
          const stages = db.transcripts[p.id] ? ['analyzing'] : ['transcribing', 'analyzing'];
          startJob(u, p, 'process', stages, prev.params, o && o.idempotencyKey);
          p.failureCode = null;
          return { replay: () => statusView(p), ...statusView(p) };
        });
      }).then(stripReplay),
      cancelProject: (id) => op(() => {
        const u = requireUser();
        const p = ownProject(u, id);
        const j = projectJob(p);
        if (!j || j.type !== 'process') throw new ApiError('INVALID_STATE', 'Nothing to cancel.', 409);
        if (j.progressStage === 'transcribing') charge(p, j, 'transcription', p.durationSec * j.stageProgress);
        j.state = 'canceled'; j.finishedAt = now();
        p.status = 'canceled';
        p.sourceExpiresAt = now() + CONFIG.retention.sourceHours * HOUR;
        return statusView(p);
      }),
      getStatus: (id) => op(() => statusView(ownProject(requireUser(), id))),

      getTranscript: (id) => op(() => {
        const p = ownProject(requireUser(), id);
        requireTranscript(p);
        return transcriptView(p);
      }),
      saveTranscript: (id, body) => op(() => {
        const p = ownProject(requireUser(), id);
        const t = requireTranscript(p);
        if (projectJob(p)) throw new ApiError('ACTIVE_JOB_EXISTS', 'Wait for processing to finish before editing.', 409);
        const edits = (body && body.segments) || [];
        let changed = 0;
        for (const e of edits) {
          const seg = t.segments.find((s) => s.id === e.id);
          if (!seg) throw new ApiError('INVALID_INPUT', 'Unknown segment.', 422);
          const text = String(e.text || '').trim();
          if (!text || text.length > 2000) throw new ApiError('INVALID_INPUT', 'Segments can’t be empty.', 422);
          if (text === seg.text) continue;
          const a = alignSegment(seg, text);
          seg.text = text; seg.words = a.words; seg.timing = a.timing;
          changed++;
        }
        if (changed) {
          t.revision++; t.userCorrected = true;
          if (p.currentRunId && db.runs[p.currentRunId].transcriptRevision < t.revision) p.clipsStale = true;
        }
        return transcriptView(p);
      }),
      getProjectDownloads: (id) => op(() => {
        const p = ownProject(requireUser(), id);
        const t = requireTranscript(p);
        const txt = t.segments.map((s) => s.text).join('\n\n');
        const json = JSON.stringify({ language: t.language, revision: t.revision, segments: t.segments }, null, 2);
        const base = p.name.replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
        const toUrl = (mime, s) => 'data:' + mime + ';charset=utf-8,' + encodeURIComponent(s);
        return [
          { kind: 'txt', filename: base + '-transcript.txt', url: toUrl('text/plain', txt), bytes: txt.length },
          { kind: 'json', filename: base + '-transcript.json', url: toUrl('application/json', json), bytes: json.length },
        ];
      }),

      getClips: (id) => op(() => {
        const p = ownProject(requireUser(), id);
        if (p.status === 'expired') throw new ApiError('ASSET_EXPIRED', null, 410);
        if (!p.currentRunId) return { runId: null, goal: null, stale: false, clips: [] };
        const run = db.runs[p.currentRunId];
        return {
          runId: run.id, goal: run.goal, instruction: run.instruction, avoid: run.avoid, stale: p.clipsStale,
          promptVersion: run.promptVersion, modelName: run.modelName,
          clips: run.clipIds.map((cid) => db.clips[cid]).map(({ key, ...c }) => c),
        };
      }),
      createAnalysisRun: (id, body, o) => op(() => {
        const u = requireUser();
        const p = ownProject(u, id);
        return withIdempotency(u, o && o.idempotencyKey, () => {
          requireTranscript(p);
          if (p.status !== 'ready') throw new ApiError('INVALID_STATE', 'Wait for the current step to finish.', 409);
          const params = validateGuidance(body);
          startJob(u, p, 'analysis', ['analyzing'], params, o && o.idempotencyKey);
          return { replay: () => statusView(p), ...statusView(p) };
        });
      }).then(stripReplay),

      getUsage: () => op(() => {
        const u = requireUser();
        return { ...usageView(u), entries: db.ledger.filter((e) => e.userId === u.id && e.createdAt >= monthStart()) };
      }),
    };

    // Test/demo hooks — deliberately outside the API contract.
    Object.defineProperty(api, '_mock', {
      enumerable: false,
      value: {
        failNext: (stage) => failQueue.push(stage),
        inspect: (projectId) => clone(db.projects[projectId] && db.projects[projectId].stats),
        ledger: () => clone(db.ledger),
        accounts: () => [
          { email: maya.email, name: maya.displayName, note: 'Invited creator' },
          { email: dev.email, name: dev.displayName, note: 'Invited creator' },
          { email: 'sam@example.com', name: 'Sam Lee', note: 'Invite revoked' },
        ],
        sample: () => ({ fileName: SAMPLE.fileName, title: SAMPLE.title, mimeType: 'video/mp4', sizeBytes: SAMPLE.sizeBytes, durationSec: SAMPLE.durationSec, width: SAMPLE.width, height: SAMPLE.height, fps: SAMPLE.fps, hasVideo: true, hasAudio: true }),
      },
    });
    return api;
  }

  function stripReplay(x) { if (x && typeof x === 'object') delete x.replay; return x; }

  // Single entry point for the app. Adds opaque-ID telemetry and nothing else.
  function createApi({ mode = 'mock', onCall, ...rest } = {}) {
    const impl = mode === 'http' ? createHttpApi(rest) : createMockApi(rest);
    const api = {};
    for (const name of API_METHODS) {
      api[name] = async (...args) => {
        const t0 = Date.now();
        try {
          const out = await impl[name](...args);
          onCall && onCall({ method: name, ms: Date.now() - t0, ok: true });
          return out;
        } catch (e) {
          const err = e instanceof ApiError ? e : new ApiError('SERVER_ERROR', null, 500);
          onCall && onCall({ method: name, ms: Date.now() - t0, ok: false, code: err.code });
          throw err;
        }
      };
    }
    if (impl._mock) Object.defineProperty(api, '_mock', { enumerable: false, value: impl._mock });
    return api;
  }

  return { CONFIG, ERROR_COPY, ApiError, ENDPOINTS, API_METHODS, createApi, createHttpApi, createMockApi, validateClips, combinedScore, suggestionCount, alignSegment, validateGuidance };
});
