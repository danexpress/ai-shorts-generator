/* Service-layer test suite. Runs in the browser (in-app runner + Tests page)
   or in Node: `node tests.js`. No dependencies. */
(function (root, factory) {
  const S = root.ShortsServices || (typeof require === 'function' ? require('./services.js') : null);
  const mod = factory(S);
  if (typeof module === 'object' && module.exports) {
    module.exports = mod;
    if (require.main === module) mod.run().then((rs) => {
      rs.forEach((r) => console.log((r.pass ? 'PASS ' : 'FAIL ') + r.group + ' › ' + r.name + (r.pass ? '' : '\n     ' + r.error)));
      const failed = rs.filter((r) => !r.pass).length;
      console.log(`\n${rs.length - failed} passed, ${failed} failed`);
      process.exit(failed ? 1 : 0);
    });
  } else root.ShortsTests = mod;
})(typeof self !== 'undefined' ? self : this, function (S) {
  const tests = [];
  const test = (group, name, fn) => tests.push({ group, name, fn });

  const assert = {
    ok(v, m) { if (!v) throw new Error(m || 'expected truthy'); },
    eq(a, b, m) { if (a !== b) throw new Error((m ? m + ': ' : '') + 'expected ' + JSON.stringify(b) + ', got ' + JSON.stringify(a)); },
    async rejects(p, code) {
      try { await p; } catch (e) {
        if (code && e.code !== code) throw new Error('expected ' + code + ', got ' + (e.code || e.message));
        return e;
      }
      throw new Error('expected rejection with ' + code);
    },
  };

  const START = Date.UTC(2026, 8, 15, 12);
  function env(o = {}) {
    let t = START, seed = 42;
    const clock = { now: () => t, advance: (ms) => { t += ms; } };
    const random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
    const api = S.createApi({ mode: 'mock', now: clock.now, random, timing: { latency: 0, transcribing: 1000, analyzing: 1000 }, ...o });
    return { api, clock };
  }
  const SRC = { fileName: 'ep42.mp4', mimeType: 'video/mp4', sizeBytes: 1.8e9, durationSec: 1934, width: 1920, height: 1080, fps: 30, hasVideo: true, hasAudio: true };

  async function uploaded(api, over = {}) {
    const up = await api.createUpload({ ...SRC, ...over });
    await api.completeUpload(up.uploadId);
    return up.projectId;
  }
  async function ready(api, clock, body = { goal: 'educational' }, over) {
    const id = await uploaded(api, over);
    await api.processProject(id, body);
    clock.advance(2100);
    const st = await api.getStatus(id);
    assert.eq(st.status, 'ready', 'pipeline status');
    return id;
  }
  const signIn = (api, email = 'maya@example.com') => api.signIn({ email });

  /* ---- Contract ---- */
  test('Contract', 'mock and HTTP implementations expose exactly the API contract', async () => {
    const m = S.createMockApi({ timing: { latency: 0 } });
    const h = S.createHttpApi({ fetch: () => null });
    for (const n of S.API_METHODS) {
      assert.eq(typeof m[n], 'function', 'mock.' + n);
      assert.eq(typeof h[n], 'function', 'http.' + n);
    }
    assert.eq(Object.keys(m).filter((k) => !S.API_METHODS.includes(k)).join(','), '', 'mock has no extra public methods');
    assert.eq(Object.keys(h).length, S.API_METHODS.length, 'http method count');
  });
  test('Contract', 'createApi wraps every method and normalizes unknown errors', async () => {
    const calls = [];
    const api = S.createApi({ mode: 'http', fetch: async () => { throw new TypeError('boom'); }, onCall: (c) => calls.push(c) });
    const e = await assert.rejects(api.me(), 'NETWORK_ERROR');
    assert.ok(e instanceof S.ApiError, 'ApiError instance');
    assert.eq(calls[0].method, 'me'); assert.eq(calls[0].ok, false);
    assert.ok(!('args' in calls[0]), 'telemetry carries no arguments');
  });
  test('Contract', 'HTTP maps routes, bodies and Idempotency-Key header', async () => {
    const seen = [];
    const fetch = async (url, init) => { seen.push({ url, init }); return { ok: true, status: 200, text: async () => '{"ok":true}' }; };
    const api = S.createHttpApi({ baseUrl: 'https://api.test', fetch });
    await api.processProject('prj 1', { goal: 'viral' }, { idempotencyKey: 'k-1' });
    await api.getClips('prj_2');
    await api.deleteProject('prj_3');
    assert.eq(seen[0].url, 'https://api.test/v1/projects/prj%201/process');
    assert.eq(seen[0].init.method, 'POST');
    assert.eq(seen[0].init.headers['Idempotency-Key'], 'k-1');
    assert.eq(JSON.parse(seen[0].init.body).goal, 'viral');
    assert.eq(seen[1].init.method, 'GET'); assert.eq(seen[1].init.body, undefined);
    assert.eq(seen[2].url, 'https://api.test/v1/projects/prj_3'); assert.eq(seen[2].init.method, 'DELETE');
  });
  test('Contract', 'HTTP surfaces stable error codes from the server', async () => {
    const fetch = async () => ({ ok: false, status: 409, text: async () => JSON.stringify({ error: { code: 'ACTIVE_JOB_EXISTS', message: 'busy' } }) });
    const e = await assert.rejects(S.createHttpApi({ fetch }).processProject('p', { goal: 'viral' }), 'ACTIVE_JOB_EXISTS');
    assert.eq(e.status, 409);
    const f404 = async () => ({ ok: false, status: 404, text: async () => '' });
    await assert.rejects(S.createHttpApi({ fetch: f404 }).getProject('x'), 'NOT_FOUND');
  });
  test('Contract', 'every error code has creator-facing copy', async () => {
    const used = ['UNSUPPORTED_MEDIA', 'SOURCE_TOO_LONG', 'SOURCE_TOO_SHORT', 'SOURCE_TOO_LARGE', 'MEDIA_PROBE_FAILED', 'TRANSCRIPTION_FAILED', 'ANALYSIS_FAILED', 'IMPORT_UNAVAILABLE', 'MONTHLY_LIMIT_REACHED', 'ACTIVE_JOB_EXISTS', 'ASSET_EXPIRED', 'NOT_FOUND', 'NOT_INVITED', 'UNAUTHENTICATED'];
    used.forEach((c) => assert.ok(S.ERROR_COPY[c], 'copy for ' + c));
  });
  test('Contract', 'rejects invalid media before creating a project', async () => {
    const { api } = env(); await signIn(api);
    const before = (await api.listProjects()).length;
    await assert.rejects(api.createUpload({ ...SRC, durationSec: 10801 }), 'SOURCE_TOO_LONG');
    await assert.rejects(api.createUpload({ ...SRC, sizeBytes: 5 * 1024 ** 3 }), 'SOURCE_TOO_LARGE');
    await assert.rejects(api.createUpload({ ...SRC, fileName: 'clip.avi' }), 'UNSUPPORTED_MEDIA');
    await assert.rejects(api.createUpload({ ...SRC, hasVideo: false }), 'MEDIA_PROBE_FAILED');
    await assert.rejects(api.createUpload({ ...SRC, durationSec: 20 }), 'SOURCE_TOO_SHORT');
    await assert.rejects(api.createUpload({ ...SRC, width: 12000 }), 'UNSUPPORTED_MEDIA');
    assert.eq((await api.listProjects()).length, before, 'no projects created');
  });
  test('Contract', 'accepts a three-hour source with sufficient processing allowance', async () => {
    const originalLimit = S.CONFIG.monthlyMinutes;
    try {
      S.CONFIG.monthlyMinutes = 240;
      const { api, clock } = env(); await signIn(api);
      const id = await ready(api, clock, { goal: 'educational' }, { durationSec: 10800 });
      assert.eq((await api.getProject(id)).durationSec, 10800);
      assert.eq((await api.getClips(id)).clips.length, 10);
      const entry = (await api.getUsage()).entries.find(e => e.projectId === id);
      assert.eq(entry.processedSeconds, 10800);
    } finally { S.CONFIG.monthlyMinutes = originalLimit; }
  });
  test('Contract', 'full pipeline moves uploading → transcribing → analyzing → ready', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await uploaded(api);
    assert.eq((await api.getProject(id)).status, 'uploading');
    await api.processProject(id, { goal: 'educational' });
    clock.advance(500); assert.eq((await api.getStatus(id)).status, 'transcribing');
    clock.advance(1000); const mid = await api.getStatus(id);
    assert.eq(mid.status, 'analyzing'); assert.ok(mid.progress > 0.6 && mid.progress < 1, 'progress ' + mid.progress);
    clock.advance(600); assert.eq((await api.getStatus(id)).status, 'ready');
    const t = await api.getTranscript(id);
    assert.eq(t.language, 'en'); assert.ok(t.segments[0].words.length > 5, 'word timestamps');
  });
  test('Contract', 'transcript edits create a revision and mark suggestions stale', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await ready(api, clock);
    const t = await api.getTranscript(id);
    const s0 = t.segments[0], s1 = t.segments[1];
    const sameCount = s0.text.replace('Welcome', 'Hello');
    const t2 = await api.saveTranscript(id, { segments: [{ id: s0.id, text: sameCount }, { id: s1.id, text: 'Founders hire backwards.' }] });
    assert.eq(t2.revision, 2); assert.eq(t2.userCorrected, true);
    assert.eq(t2.segments[0].timing, 'word', 'same word count keeps word timing');
    assert.eq(t2.segments[0].words[0].s, s0.words[0].s, 'original timing preserved');
    assert.eq(t2.segments[1].timing, 'segment', 'changed word count falls back to segment timing');
    assert.eq((await api.getClips(id)).stale, true);
    await assert.rejects(api.saveTranscript(id, { segments: [{ id: s0.id, text: '   ' }] }), 'INVALID_INPUT');
  });
  test('Contract', 'YouTube import is feature-flagged and host-restricted', async () => {
    const off = env(); await signIn(off.api);
    await assert.rejects(off.api.createYoutubeProject({ url: 'https://youtu.be/abcdef123', rightsConfirmed: true }), 'IMPORT_UNAVAILABLE');
    const on = env({ features: { youtubeImport: true } }); await signIn(on.api);
    for (const url of ['https://evil.com/watch?v=abcdef123', 'https://youtube.com.evil.com/watch?v=abcdef123', 'http://youtu.be/abcdef123', 'file:///etc/passwd']) {
      await assert.rejects(on.api.createYoutubeProject({ url, rightsConfirmed: true }), 'UNSUPPORTED_URL');
    }
    await assert.rejects(on.api.createYoutubeProject({ url: 'https://www.youtube.com/watch?v=abcdef123' }), 'RIGHTS_NOT_CONFIRMED');
    const p = await on.api.createYoutubeProject({ url: 'https://www.youtube.com/watch?v=abcdef123', rightsConfirmed: true });
    assert.eq(p.sourceType, 'youtube_url');
  });

  /* ---- Authorization ---- */
  test('Authorization', 'non-invited and revoked accounts cannot sign in', async () => {
    const { api } = env();
    await assert.rejects(signIn(api, 'stranger@example.com'), 'NOT_INVITED');
    await assert.rejects(signIn(api, 'sam@example.com'), 'NOT_INVITED');
    await assert.rejects(api.me(), 'UNAUTHENTICATED');
    await assert.rejects(api.listProjects(), 'UNAUTHENTICATED');
  });
  test('Authorization', 'signing out ends the session', async () => {
    const { api } = env(); await signIn(api);
    assert.eq((await api.me()).user.email, 'maya@example.com');
    await api.signOut();
    await assert.rejects(api.me(), 'UNAUTHENTICATED');
  });
  test('Authorization', 'creator B cannot read or change creator A’s project by ID', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await ready(api, clock);
    await signIn(api, 'jon@example.com');
    assert.ok(!(await api.listProjects()).some((p) => p.id === id), 'not listed for B');
    await assert.rejects(api.getProject(id), 'NOT_FOUND');
    await assert.rejects(api.getStatus(id), 'NOT_FOUND');
    await assert.rejects(api.getTranscript(id), 'NOT_FOUND');
    await assert.rejects(api.saveTranscript(id, { segments: [] }), 'NOT_FOUND');
    await assert.rejects(api.getClips(id), 'NOT_FOUND');
    await assert.rejects(api.getProjectDownloads(id), 'NOT_FOUND');
    await assert.rejects(api.createAnalysisRun(id, { goal: 'viral' }), 'NOT_FOUND');
    await assert.rejects(api.cancelProject(id), 'NOT_FOUND');
    await assert.rejects(api.deleteProject(id), 'NOT_FOUND');
    await signIn(api);
    assert.eq((await api.getProject(id)).status, 'ready', 'A’s project untouched');
  });
  test('Authorization', 'foreign upload sessions cannot be completed', async () => {
    const { api } = env(); await signIn(api);
    const up = await api.createUpload(SRC);
    await signIn(api, 'jon@example.com');
    await assert.rejects(api.completeUpload(up.uploadId), 'NOT_FOUND');
  });
  test('Authorization', 'deleted projects are immediately inaccessible', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await ready(api, clock);
    const r = await api.deleteProject(id);
    assert.eq(r.deletionQueued, true);
    await assert.rejects(api.getProject(id), 'NOT_FOUND');
    await assert.rejects(api.getProjectDownloads(id), 'NOT_FOUND');
    assert.ok(!(await api.listProjects()).some((p) => p.id === id));
  });

  /* ---- Usage ledger & idempotency ---- */
  test('Usage', 'usage is ledger-backed and counts only this month', async () => {
    const { api, clock } = env(); await signIn(api);
    const u0 = await api.getUsage();
    assert.eq(u0.usedMinutes, 19, 'seeded Ep. 41 = 1100 s');
    assert.eq(u0.entries.length, 1, 'last month’s project excluded');
    await ready(api, clock);
    const u1 = await api.getUsage();
    assert.eq(u1.usedMinutes, Math.ceil((1100 + 1934) / 60));
    assert.eq(u1.entries.reduce((a, e) => a + e.processedSeconds, 0), 1100 + 1934);
  });
  test('Usage', 'duplicate process requests with one idempotency key run once', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await uploaded(api);
    const a = await api.processProject(id, { goal: 'viral' }, { idempotencyKey: 'k1' });
    const b = await api.processProject(id, { goal: 'viral' }, { idempotencyKey: 'k1' });
    assert.eq(a.jobId, b.jobId, 'same job');
    await assert.rejects(api.processProject(id, { goal: 'viral' }, { idempotencyKey: 'k2' }), 'INVALID_STATE');
    clock.advance(2100);
    await api.processProject(id, { goal: 'viral' }, { idempotencyKey: 'k1' });
    assert.eq((await api.getUsage()).entries.length, 2, 'one charge for the new project');
  });
  test('Usage', 'retry after analysis failure does not re-transcribe or double-charge', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await uploaded(api);
    api._mock.failNext('analysis');
    await api.processProject(id, { goal: 'educational' });
    clock.advance(2100);
    const st = await api.getStatus(id);
    assert.eq(st.status, 'failed'); assert.eq(st.failureCode, 'ANALYSIS_FAILED'); assert.eq(st.canRetry, true);
    const used = (await api.getUsage()).usedMinutes;
    await api.retryProject(id);
    clock.advance(1100);
    assert.eq((await api.getStatus(id)).status, 'ready');
    assert.eq(api._mock.inspect(id).transcriptionRuns, 1, 'transcribed once');
    assert.eq((await api.getUsage()).usedMinutes, used, 'no extra usage');
  });
  test('Usage', 'retry after transcription failure charges exactly once', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await uploaded(api);
    api._mock.failNext('transcription');
    await api.processProject(id, { goal: 'educational' });
    clock.advance(1100);
    assert.eq((await api.getStatus(id)).failureCode, 'TRANSCRIPTION_FAILED');
    assert.eq((await api.getUsage()).entries.length, 1, 'failed transcription not charged');
    await api.retryProject(id); clock.advance(2100);
    assert.eq((await api.getStatus(id)).status, 'ready');
    assert.eq((await api.getUsage()).entries.length, 2);
  });
  test('Usage', 'cancel mid-transcription charges only processed seconds', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await uploaded(api);
    await api.processProject(id, { goal: 'educational' });
    clock.advance(500);
    const st = await api.cancelProject(id);
    assert.eq(st.status, 'canceled');
    clock.advance(5000);
    const entry = (await api.getUsage()).entries.find((e) => e.projectId === id);
    assert.eq(entry.processedSeconds, Math.round(1934 * 0.5));
    assert.eq((await api.getUsage()).entries.length, 2, 'no later charge');
    await assert.rejects(api.cancelProject(id), 'INVALID_STATE');
  });
  test('Usage', 'monthly allowance is enforced before processing', async () => {
    const { api, clock } = env(); await signIn(api);
    await ready(api, clock); // 19 + 33 = 52 min
    await assert.rejects(api.createUpload(SRC), 'MONTHLY_LIMIT_REACHED');
    await uploaded(api, { durationSec: 300 }); // 5 more fits
  });
  test('Usage', 'only one compute-intensive job per creator', async () => {
    const { api } = env(); await signIn(api);
    const a = await uploaded(api, { durationSec: 300 });
    const b = await uploaded(api, { durationSec: 300 });
    await api.processProject(a, { goal: 'viral' });
    await assert.rejects(api.processProject(b, { goal: 'viral' }), 'ACTIVE_JOB_EXISTS');
    assert.ok((await api.me()).activeJob, 'active job visible');
    await signIn(api, 'jon@example.com');
    const c = await uploaded(api, { durationSec: 300 });
    await api.processProject(c, { goal: 'viral' }); // other creators unaffected
  });
  test('Usage', 'regeneration reuses the transcript and costs no minutes', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await ready(api, clock);
    const before = await api.getClips(id);
    const used = (await api.getUsage()).usedMinutes;
    await api.createAnalysisRun(id, { goal: 'entertaining', avoid: ['best friend'] });
    clock.advance(1100);
    const after = await api.getClips(id);
    assert.ok(after.runId !== before.runId, 'new analysis run');
    assert.eq(after.goal, 'entertaining');
    assert.ok(!after.clips.some((c) => /best friend/i.test(c.hook)), 'avoided topic excluded');
    assert.ok(after.clips[0].hook !== before.clips[0].hook, 'goal changes ranking');
    assert.eq(api._mock.inspect(id).transcriptionRuns, 1);
    assert.eq((await api.getUsage()).usedMinutes, used);
  });

  /* ---- AI output validation ---- */
  const good = { start: 10, end: 50, hook: 'A hook', reason: 'Because.', scores: { hook: 90, value: 80, standalone: 85, visual: 70 } };
  test('AI validation', 'accepts a well-formed clip', async () => {
    const r = S.validateClips({ clips: [good] }, 600);
    assert.eq(r.accepted.length, 1); assert.eq(r.rejected.length, 0);
  });
  test('AI validation', 'rejects each malformed field with a stable reason', async () => {
    const cases = [
      [{ ...good, start: -1 }, 'START_NEGATIVE'],
      [{ ...good, end: 700 }, 'END_AFTER_SOURCE'],
      [{ ...good, end: 10 }, 'END_BEFORE_START'],
      [{ ...good, end: 20 }, 'DURATION_OUT_OF_RANGE'],
      [{ ...good, end: 110 }, 'DURATION_OUT_OF_RANGE'],
      [{ ...good, start: '10' }, 'MISSING_TIMESTAMPS'],
      [{ ...good, scores: { ...good.scores, hook: 101 } }, 'SCORE_OUT_OF_RANGE'],
      [{ ...good, scores: { hook: 90, value: 80, standalone: 85 } }, 'SCORE_OUT_OF_RANGE'],
      [{ ...good, hook: '  ' }, 'INVALID_HOOK'],
      [{ ...good, hook: 'x'.repeat(81) }, 'INVALID_HOOK'],
      [{ ...good, reason: '' }, 'INVALID_REASON'],
      [null, 'NOT_AN_OBJECT'],
    ];
    for (const [c, why] of cases) assert.eq(S.validateClips({ clips: [c] }, 600).rejected[0].reason, why);
    assert.eq(S.validateClips({ nope: 1 }, 600).rejected[0].reason, 'MISSING_CLIPS');
    assert.eq(S.validateClips('garbage', 600).rejected[0].reason, 'MISSING_CLIPS');
  });
  test('AI validation', 'rejects heavily overlapping windows, keeps distinct ones', async () => {
    const r = S.validateClips({ clips: [good, { ...good, start: 15, end: 55 }, { ...good, start: 45, end: 85 }] }, 600);
    assert.eq(r.accepted.length, 2); assert.eq(r.rejected[0].reason, 'OVERLAP');
  });
  test('AI validation', 'suggestion count follows source duration', async () => {
    [[300, 3], [599, 3], [600, 5], [1199, 5], [1934, 7], [2400, 10], [3600, 10], [10800, 10]].forEach(([s, n]) => assert.eq(S.suggestionCount(s), n, s + 's'));
  });
  test('AI validation', 'combined score uses configured 30/30/25/15 weights', async () => {
    assert.eq(S.combinedScore({ hook: 100, value: 0, standalone: 0, visual: 0 }), 30);
    assert.eq(S.combinedScore({ hook: 0, value: 0, standalone: 100, visual: 100 }), 40);
    assert.eq(S.combinedScore({ hook: 94, value: 89, standalone: 93, visual: 82 }), 90);
  });
  test('AI validation', 'guidance input is bounded', async () => {
    assert.eq(S.validateGuidance({ goal: 'viral', avoid: [' politics ', ''] }).avoid.join(), 'politics');
    const bad = [{ goal: 'funny' }, { goal: 'viral', instruction: 'x'.repeat(201) }, { goal: 'viral', avoid: Array(11).fill('a') }, { goal: 'viral', avoid: ['x'.repeat(41)] }];
    for (const b of bad) { let code; try { S.validateGuidance(b); } catch (e) { code = e.code; } assert.eq(code, 'INVALID_INPUT'); }
  });
  test('AI validation', 'every served suggestion is valid and word-aligned', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await ready(api, clock);
    const { clips } = await api.getClips(id);
    const t = await api.getTranscript(id);
    const starts = new Set(), ends = new Set();
    t.segments.forEach((s) => s.words.forEach((w) => { starts.add(w.s); ends.add(w.e); }));
    assert.eq(clips.length, 7, '32-min source → 7');
    clips.forEach((c, i) => {
      assert.eq(c.rank, i + 1);
      assert.ok(c.duration >= 15 && c.duration <= 90, 'duration ' + c.duration);
      assert.ok(starts.has(c.start) && ends.has(c.end), 'starts/ends on word boundary');
      assert.ok(c.hook && c.reason, 'hook + reason');
      assert.eq(c.combined, S.combinedScore(c.scores));
    });
  });
  test('AI validation', 'custom instruction steers ranking', async () => {
    const { api, clock } = env(); await signIn(api);
    const id = await ready(api, clock, { goal: 'entertaining', instruction: 'clips about pricing' });
    assert.ok((await api.getClips(id)).clips[0].tags.includes('pricing'));
  });

  async function run(onResult) {
    const results = [];
    for (const t of tests) {
      const t0 = Date.now();
      let r;
      try { await t.fn(); r = { group: t.group, name: t.name, pass: true }; }
      catch (e) { r = { group: t.group, name: t.name, pass: false, error: e && e.message }; }
      r.ms = Date.now() - t0;
      results.push(r);
      if (onResult) onResult(r, results);
    }
    return results;
  }

  return { run, list: tests.map((t) => ({ group: t.group, name: t.name })) };
});
