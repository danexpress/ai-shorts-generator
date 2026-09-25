const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const { spawn } = require('node:child_process');
const S = require('./services.js');

const response = (data, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(data) });
const session = { user: { displayName: 'Maya', email: 'maya@example.com' }, usage: { usedMinutes: 19, limitMinutes: 60, remainingMinutes: 41 }, features: { youtubeImport: false }, activeJob: null };

test('HTTP login sends a password, attaches the token, and revokes it on logout', async () => {
  const calls = [];
  const api = S.createHttpApi({ baseUrl: 'http://api.test/', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return response(url.endsWith('/google') ? { ...session, access_token: 'demo-token' } : { ok: true });
  }});
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.processProject('prj 1', { goal: 'viral' }, { idempotencyKey: 'one' });
  await api.signOut();
  await api.me();
  assert.deepEqual(JSON.parse(calls[0].body), { email: 'maya@example.com', password: 'secret' });
  assert.equal(calls[0].headers.Authorization, undefined);
  assert.equal(calls[1].url, 'http://api.test/v1/projects/prj%201/process');
  assert.equal(calls[1].headers.Authorization, 'Bearer demo-token');
  assert.equal(calls[1].headers['Idempotency-Key'], 'one');
  assert.equal(calls[2].headers.Authorization, 'Bearer demo-token');
  assert.equal(calls[3].headers.Authorization, undefined);
});

test('HTTP sends video bytes and requests a project playback link with bearer auth', async () => {
  const calls = [];
  const api = S.createHttpApi({ baseUrl: 'http://api.test/', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return response(url.endsWith('/google') ? { access_token: 'demo-token' } : { url: '/v1/media/project?expires=123&signature=abc', expiresAt: 123000 });
  }});
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  const file = new Blob(['video-bytes'], { type: 'video/mp4' });
  await api.uploadMedia('upload-1', file);
  await api.getProjectPlayback('project-1');
  assert.equal(calls[1].url, 'http://api.test/v1/uploads/upload-1/media');
  assert.equal(calls[1].headers.Authorization, 'Bearer demo-token');
  assert.equal(calls[1].headers['Content-Type'], 'video/mp4');
  assert.equal(calls[1].body, file);
  assert.equal(calls[2].url, 'http://api.test/v1/projects/project-1/playback');
  assert.equal(calls[2].method, 'POST');
  assert.equal(calls[2].headers.Authorization, 'Bearer demo-token');
});

for (const code of ['UNAUTHENTICATED', 'ACCOUNT_DISABLED', 'NOT_INVITED']) {
  test(`HTTP ${code} clears the token`, async () => {
    let fail = false;
    let lastHeaders;
    const api = S.createHttpApi({ fetch: async (url, init) => {
      lastHeaders = init.headers;
      if (url.endsWith('/google')) return response({ access_token: 'token' });
      return fail ? response({ error: { code } }, code === 'UNAUTHENTICATED' ? 401 : 403) : response(session);
    }});
    await api.signIn({ email: 'maya@example.com', password: 'secret' });
    fail = true;
    await assert.rejects(api.me(), { code });
    fail = false;
    await api.me();
    assert.equal(lastHeaders.Authorization, undefined);
  });
}

test('network failure on logout still clears the token', async () => {
  let lastHeaders;
  const api = S.createHttpApi({ fetch: async (url, init) => {
    lastHeaders = init.headers;
    if (url.endsWith('/logout')) throw new Error('offline');
    return response({ access_token: 'token' });
  }});
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await assert.rejects(api.signOut(), { code: 'NETWORK_ERROR' });
  await api.me();
  assert.equal(lastHeaders.Authorization, undefined);
});

test('a delayed private response cannot restore data after logout', async () => {
  let resolveMe;
  const api = S.createHttpApi({ fetch: async (url) => {
    if (url.endsWith('/google')) return response({ access_token: 'token' });
    if (url.endsWith('/me')) return new Promise(resolve => { resolveMe = resolve; });
    return response({ ok: true });
  }});
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  const pending = api.me();
  await api.signOut();
  resolveMe(response(session));
  await assert.rejects(pending, { code: 'SESSION_CHANGED' });
});

test('missing login token is an explicit server error', async () => {
  const api = S.createHttpApi({ fetch: async () => response(session) });
  await assert.rejects(api.signIn({ email: 'maya@example.com', password: 'secret' }), { code: 'SERVER_ERROR' });
});

function component(services, baseUrl = 'http://api.test') {
  const html = fs.readFileSync(path.join(__dirname, 'AI Shorts Generator.dc.html'), 'utf8');
  const script = html.match(/<script type="text\/x-dc"[^>]*>([\s\S]*?)<\/script>/)[1];
  class DCLogic {
    props = {};
    setState(update) { this.state = { ...this.state, ...(typeof update === 'function' ? update(this.state) : update) }; }
  }
  const context = vm.createContext({ DCLogic, window: { ShortsServices: services, SHORTS_CONFIG: { apiBaseUrl: baseUrl } }, setTimeout, clearTimeout, setInterval, clearInterval });
  return vm.runInContext(script + '\nnew Component()', context);
}

test('page uses the HTTP client, password form state and server feature flags', async t => {
  const options = [];
  const page = component({ ...S, createApi: config => {
    options.push(config);
    return { signIn: async body => { assert.equal(body.password, 'secret'); return session; }, me: async () => session, listProjects: async () => [] };
  }});
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  assert.equal(options[0].mode, 'http');
  assert.equal(options[0].baseUrl, 'http://api.test');
  page.setState({ loginEmail: 'maya@example.com', loginPassword: 'secret' });
  await page.signIn({ preventDefault() {} });
  assert.equal(page.state.route, 'dashboard');
  assert.equal(page.state.loginPassword, '');
  assert.equal(page.renderVals().tabs.length, 1);
  page.setState({ me: { ...session, features: { youtubeImport: true } } });
  assert.equal(page.renderVals().tabs.length, 2);
  page.setState({ projects: [{ id: 'private' }], transcript: { segments: [] } });
  options[0].onCall({ method: 'getTranscript', ok: false, code: 'UNAUTHENTICATED' });
  assert.equal(page.state.route, 'login');
  assert.equal(page.state.projects.length, 0);
  assert.equal(page.state.transcript, null);
});

test('page shows backend outage and clears password after unsuccessful login', async t => {
  const page = component({ ...S, createApi: () => ({ signIn: async () => { throw new S.ApiError('NETWORK_ERROR'); } }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ loginEmail: 'maya@example.com', loginPassword: 'secret' });
  await page.signIn({ preventDefault() {} });
  assert.equal(page.state.route, 'login');
  assert.match(page.state.loginError, /Cannot reach the backend/);
  assert.equal(page.state.loginPassword, '');
  assert.equal(page.state.loginBusy, false);
});

test('clip preview uses the selected local video and seeks to the suggested clip', async t => {
  const page = component({ ...S, createApi: () => ({}) });
  t.after(() => page.componentWillUnmount());
  page.setState({
    route: 'suggestions', projectId: 'project-1', previewUrls: { 'project-1': 'blob:source-video' },
    clipsRes: { clips: [{ id: 'clip-1', rank: 1, start: 12, end: 32, duration: 20, hook: 'A useful hook', reason: 'A complete thought', combined: 90, scores: { hook: 90, value: 90, standalone: 90, visual: 90 } }] },
  });
  const clip = page.renderVals().clips[0];
  assert.equal(clip.hasVideo, true);
  assert.equal(clip.videoUrl, 'blob:source-video#t=12,32');
  page.setState({ previewId: 'clip-1' });
  assert.equal(page.renderVals().pv.videoUrl, 'blob:source-video#t=12,32');
});

test('page and HTTP client work with a live isolated FastAPI backend', { timeout: 20000 }, async t => {
  const backend = path.resolve(__dirname, '../backend');
  const python = path.join(backend, '.venv/bin/python');
  assert.ok(fs.existsSync(python), 'Run make install before running frontend integration tests.');
  const dataDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'shorts-http-test-'));
  const databaseUrl = 'sqlite+pysqlite:///' + path.join(dataDirectory, 'test.db');
  const server = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '0'], { cwd: backend, env: { ...process.env, DATABASE_URL: databaseUrl, SEED_DEMO_DATA: 'true' }, stdio: ['ignore', 'ignore', 'pipe'] });
  t.after(async () => {
    if (server.exitCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill('SIGTERM');
      await exited;
    }
    fs.rmSync(dataDirectory, { recursive: true, force: true });
  });
  const baseUrl = await new Promise((resolve, reject) => {
    let logs = '';
    const timer = setTimeout(() => reject(new Error('Backend did not start: ' + logs)), 10000);
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Backend exited (${code}): ${logs}`)); });
    server.stderr.on('data', chunk => {
      logs += chunk;
      const match = logs.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
  });
  const page = component(S, baseUrl);
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ loginEmail: 'maya@example.com', loginPassword: 'DemoPass123!' });
  await page.signIn({ preventDefault() {} });
  assert.equal(page.state.route, 'dashboard');
  assert.ok(page.state.projects.some(p => p.status === 'ready'));
  const ready = page.state.projects.find(p => p.status === 'ready');
  await page.openProject(ready);
  await page.loadTranscript(ready.id);
  assert.ok(page.state.clipsRes.clips.length > 0);
  assert.ok(page.state.transcript.segments.length > 0);
  assert.equal(page.renderVals().tabs.length, 1);
  page.setState({ pendingFile: new Blob([new Uint8Array(12)], { type: 'video/mp4' }) });
  page.setNp({ source: { fileName: 'demo.mp4', title: 'HTTP integration demo', mimeType: 'video/mp4', sizeBytes: 12, durationSec: 300, width: 1920, height: 1080, fps: 30, hasVideo: true, hasAudio: true } });
  await page.start();
  assert.equal(page.state.route, 'processing');
  assert.equal(page.state.uploading, false);
  assert.equal(page.state.np.busy, false);
  assert.equal((await page.api.getStatus(page.state.projectId)).status, 'transcribing');
  const playback = await page.api.getProjectPlayback(page.state.projectId);
  const mediaResponse = await fetch(new URL(playback.url, baseUrl), { headers: { Range: 'bytes=0-4' } });
  assert.equal(mediaResponse.status, 206);
  assert.equal((await mediaResponse.arrayBuffer()).byteLength, 5);
  await page.cancel();
  assert.equal(page.state.status.status, 'canceled');
  await page.signOut();
  assert.equal(page.state.route, 'login');
  assert.equal(page.state.projects.length, 0);
  await assert.rejects(page.api.me(), { code: 'UNAUTHENTICATED' });
});
