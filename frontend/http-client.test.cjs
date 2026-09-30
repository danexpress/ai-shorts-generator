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

function memoryStorage() {
  const items = new Map();
  return {
    items,
    getItem: key => items.get(key) ?? null,
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: key => items.delete(key),
  };
}

test('refresh restores only the expiring token and scopes it to the backend URL', async () => {
  const storage = memoryStorage();
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, ...init });
    return response(url.endsWith('/google') ? { ...session, access_token: 'saved-token', expires_in: 3600 } : session);
  };
  const original = S.createHttpApi({ baseUrl: 'http://api.test/', storage, fetch });
  await original.signIn({ email: 'maya@example.com', password: 'secret' });
  assert.equal(storage.items.size, 1);
  const saved = JSON.parse([...storage.items.values()][0]);
  assert.deepEqual(Object.keys(saved).sort(), ['accessToken', 'expiresAt']);
  assert.equal(saved.accessToken, 'saved-token');
  assert.ok(saved.expiresAt > Date.now());
  const reloaded = S.createHttpApi({ baseUrl: 'http://api.test', storage, fetch });
  await reloaded.me();
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer saved-token');
  const otherBackend = S.createHttpApi({ baseUrl: 'http://other-api.test', storage, fetch });
  await otherBackend.me();
  assert.equal(calls.at(-1).headers.Authorization, undefined);
});

test('logout removes the stored token before the network finishes, even when offline', async () => {
  const storage = memoryStorage();
  let rejectLogout;
  let lastHeaders;
  const fetch = async (url, init) => {
    lastHeaders = init.headers;
    if (url.endsWith('/google')) return response({ access_token: 'token', expires_in: 3600 });
    if (url.endsWith('/logout')) return new Promise((resolve, reject) => { rejectLogout = reject; });
    return response(session);
  };
  const api = S.createHttpApi({ storage, fetch });
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  const pending = api.signOut();
  assert.equal(lastHeaders.Authorization, 'Bearer token');
  assert.equal(storage.items.size, 0);
  const reloaded = S.createHttpApi({ storage, fetch });
  await reloaded.me();
  assert.equal(lastHeaders.Authorization, undefined);
  rejectLogout(new Error('offline'));
  await assert.rejects(pending, { code: 'NETWORK_ERROR' });
  assert.equal(storage.items.size, 0);
});

for (const saved of ['{invalid json', 'null', JSON.stringify({ accessToken: 'old-token', expiresAt: Date.now() - 1000 }), JSON.stringify({ accessToken: 'token' })]) {
  test(`refresh discards an invalid or expired saved session: ${saved}`, async () => {
    const storage = memoryStorage();
    storage.setItem('shorts-session:same-origin', saved);
    let headers;
    const api = S.createHttpApi({ storage, fetch: async (url, init) => { headers = init.headers; return response(session); } });
    await api.me();
    assert.equal(headers.Authorization, undefined);
    assert.equal(storage.items.size, 0);
  });
}

test('blocked browser storage still allows login and authenticated requests in memory', async () => {
  const storage = Object.fromEntries(['getItem', 'setItem', 'removeItem'].map(name => [name, () => { throw new Error('Storage blocked'); }]));
  let headers;
  const api = S.createHttpApi({ storage, fetch: async (url, init) => {
    headers = init.headers;
    return response(url.endsWith('/google') ? { access_token: 'token', expires_in: 3600 } : session);
  }});
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.me();
  assert.equal(headers.Authorization, 'Bearer token');
  await api.signOut();
  await api.me();
  assert.equal(headers.Authorization, undefined);
});

test('a temporary network failure preserves the stored session for retry', async () => {
  const storage = memoryStorage();
  let offline = false;
  let headers;
  const fetch = async (url, init) => {
    headers = init.headers;
    if (offline) throw new Error('offline');
    return response(url.endsWith('/google') ? { access_token: 'token', expires_in: 3600 } : session);
  };
  await S.createHttpApi({ storage, fetch }).signIn({ email: 'maya@example.com', password: 'secret' });
  const reloaded = S.createHttpApi({ storage, fetch });
  offline = true;
  await assert.rejects(reloaded.me(), { code: 'NETWORK_ERROR' });
  assert.equal(storage.items.size, 1);
  offline = false;
  await reloaded.me();
  assert.equal(headers.Authorization, 'Bearer token');
});

test('a delayed login cannot recreate a stored session after logout', async () => {
  const storage = memoryStorage();
  let resolveLogin;
  const api = S.createHttpApi({ storage, fetch: async url => {
    if (url.endsWith('/google')) return new Promise(resolve => { resolveLogin = resolve; });
    return response({ ok: true });
  }});
  const pending = api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.signOut();
  resolveLogin(response({ access_token: 'late-token', expires_in: 3600 }));
  await assert.rejects(pending, { code: 'SESSION_CHANGED' });
  assert.equal(storage.items.size, 0);
});

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
    const storage = memoryStorage();
    let fail = false;
    let lastHeaders;
    const api = S.createHttpApi({ storage, fetch: async (url, init) => {
      lastHeaders = init.headers;
      if (url.endsWith('/google')) return response({ access_token: 'token', expires_in: 3600 });
      return fail ? response({ error: { code } }, code === 'UNAUTHENTICATED' ? 401 : 403) : response(session);
    }});
    await api.signIn({ email: 'maya@example.com', password: 'secret' });
    fail = true;
    await assert.rejects(api.me(), { code });
    assert.equal(storage.items.size, 0);
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

function component(services, baseUrl = 'http://api.test', globals = {}) {
  const html = fs.readFileSync(path.join(__dirname, 'AI Shorts Generator.dc.html'), 'utf8');
  const script = html.match(/<script type="text\/x-dc"[^>]*>([\s\S]*?)<\/script>/)[1];
  class DCLogic {
    props = {};
    setState(update) { this.state = { ...this.state, ...(typeof update === 'function' ? update(this.state) : update) }; }
  }
  const context = vm.createContext({ DCLogic, URL, window: { ShortsServices: services, SHORTS_CONFIG: { apiBaseUrl: baseUrl } }, setTimeout, clearTimeout, setInterval, clearInterval, ...globals });
  return vm.runInContext(script + '\nnew Component()', context);
}

test('startup validates the session and opens the dashboard without another login', async t => {
  const page = component({ ...S, createApi: () => ({ me: async () => session, listProjects: async () => [{ id: 'p1', status: 'draft' }] }) });
  t.after(() => page.componentWillUnmount());
  assert.equal(page.state.route, 'boot');
  await page.componentDidMount();
  assert.equal(page.state.route, 'dashboard');
  assert.equal(page.state.me.user.email, session.user.email);
  assert.equal(page.state.projects[0].id, 'p1');
});

test('startup without a valid session shows a clean login screen', async t => {
  const page = component({ ...S, createApi: () => ({ me: async () => { throw new S.ApiError('UNAUTHENTICATED'); } }) });
  t.after(() => page.componentWillUnmount());
  await page.componentDidMount();
  assert.equal(page.state.route, 'login');
  assert.equal(page.state.me, null);
  assert.equal(page.state.loginError, null);
});

test('session restoration offers a retry on connection failure and then restores the dashboard', async t => {
  let offline = true;
  const page = component({ ...S, createApi: () => ({
    me: async () => { if (offline) throw new S.ApiError('NETWORK_ERROR'); return session; },
    listProjects: async () => [],
  }) });
  t.after(() => page.componentWillUnmount());
  await page.componentDidMount();
  assert.equal(page.state.route, 'boot');
  assert.match(page.state.bootError, /connection.*try again/i);
  assert.equal(page.state.me, null);
  offline = false;
  await page.renderVals().appProps.v.restoreSession();
  assert.equal(page.state.route, 'dashboard');
  assert.equal(page.state.bootError, null);
});

test('a disabled account cannot be restored', async t => {
  const page = component({ ...S, createApi: () => ({ me: async () => { throw new S.ApiError('ACCOUNT_DISABLED'); } }) });
  t.after(() => page.componentWillUnmount());
  await page.componentDidMount();
  assert.equal(page.state.route, 'login');
  assert.equal(page.state.me, null);
  assert.equal(page.state.loginError, S.ERROR_COPY.ACCOUNT_DISABLED);
});

test('a slow startup response cannot reopen the dashboard after logout', async t => {
  let resolveMe;
  const page = component({ ...S, createApi: () => ({
    me: () => new Promise(resolve => { resolveMe = resolve; }),
    signOut: async () => ({ ok: true }),
  }) });
  t.after(() => page.componentWillUnmount());
  const boot = page.componentDidMount();
  await page.signOut();
  resolveMe(session);
  await boot;
  assert.equal(page.state.route, 'login');
  assert.equal(page.state.me, null);
  assert.equal(page.state.projects.length, 0);
});

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
  assert.match(page.state.loginError, /could not connect/);
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
  const storage = memoryStorage();
  const persistentServices = { ...S, createApi: config => S.createApi({ ...config, storage }) };
  const page = component(persistentServices, baseUrl);
  t.after(() => page.componentWillUnmount());
  await page.componentDidMount();
  assert.equal(page.state.route, 'login');
  page.setState({ loginEmail: 'maya@example.com', loginPassword: 'DemoPass123!' });
  await page.signIn({ preventDefault() {} });
  assert.equal(page.state.route, 'dashboard');
  assert.ok(page.state.projects.some(p => p.status === 'ready'));
  const reloadedPage = component(persistentServices, baseUrl);
  t.after(() => reloadedPage.componentWillUnmount());
  await reloadedPage.componentDidMount();
  assert.equal(reloadedPage.state.route, 'dashboard');
  assert.equal(reloadedPage.state.me.user.email, 'maya@example.com');
  assert.ok(reloadedPage.state.projects.some(p => p.status === 'ready'));
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
  await assert.rejects(reloadedPage.api.me(), { code: 'UNAUTHENTICATED' });
  assert.equal(reloadedPage.state.route, 'login');
  const afterLogout = component(persistentServices, baseUrl);
  t.after(() => afterLogout.componentWillUnmount());
  await afterLogout.componentDidMount();
  assert.equal(afterLogout.state.route, 'login');
  assert.equal(storage.items.size, 0);
});

test('redesigned workflow preserves preview selection and the chosen render resolution', async t => {
  const calls = [];
  const page = component({ ...S, createApi: () => ({
    renderClip: async (id, body) => { calls.push({ id, body }); return { filename: 'short.mp4', url: '/media/short', downloadUrl: '/media/download', resolution: body.resolution }; },
    getClips: async () => ({ clips: [], goal: 'educational' }), getProjectPlayback: async () => ({ url: '/media/source' }), getProjectDownloads: async () => [],
  }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ me: session, projectId: 'p1', clipsRes: { clips: [{ id: 'c1', rank: 1, start: 4, end: 24, duration: 20, hook: 'A clear hook', scores: { hook: 90, value: 80, standalone: 85, visual: 70 } }] } });
  page.renderVals().clips[0].preview();
  assert.equal(page.state.route, 'editor');
  assert.equal(page.state.previewId, 'c1');
  page.renderVals().appProps.v.setResolution(720);
  await page.renderVals().appProps.v.renderPreview();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.resolution, 720);
  assert.equal(page.state.route, 'result');
  assert.equal(page.state.result.downloadUrl, 'http://api.test/media/download');
  assert.equal(page.state.result.hook, 'A clear hook');
  assert.equal(page.state.renderingId, null);
});

test('render failure keeps the editing workspace and offers a readable error', async t => {
  const page = component({ ...S, createApi: () => ({ renderClip: async () => { throw new Error('Source expired. Upload your video again.'); } }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ route: 'editor', me: session, projectId: 'p1' });
  await page.renderClip('c1');
  assert.equal(page.state.route, 'editor');
  assert.match(page.state.renderError, /Upload your video again/);
  assert.equal(page.state.renderingId, null);
});

test('URL creation requires rights confirmation, and upload probing prevents submission', t => {
  const page = component(S);
  t.after(() => page.componentWillUnmount());
  page.setState({ me: session });
  page.setNp({ tab: 'url', url: 'https://youtube.com/watch?v=example' });
  assert.equal(page.renderVals().cannotStart, true);
  page.setNp({ rights: true });
  assert.equal(page.renderVals().cannotStart, false);
  page.setNp({ probing: true });
  assert.equal(page.renderVals().cannotStart, true);
});

test('signing out removes rendered media and usage from the redesigned workspace', t => {
  const page = component(S);
  t.after(() => page.componentWillUnmount());
  page.setState({ me: session, renderedClips: [{ url: 'https://private.test/video' }], result: { url: 'https://private.test/video' }, usageEntries: [{ projectId: 'private' }], renderError: 'private title' });
  page.resetSession();
  assert.equal(page.state.renderedClips.length, 0);
  assert.equal(page.state.result, null);
  assert.equal(page.state.usageEntries.length, 0);
  assert.equal(page.state.renderError, null);
});

test('usage activity converts ledger seconds without inventing minutes', t => {
  const page = component(S);
  t.after(() => page.componentWillUnmount());
  page.setState({ me: session, usageEntries: [{ processedSeconds: 150, adjustmentSeconds: -30, createdAt: Date.now() }] });
  assert.equal(page.renderVals().appProps.v.usageEntries[0].minutes, '2 min');
});

test('late suggestions from another project cannot replace the current project', async t => {
  let resolve;
  const page = component({ ...S, createApi: () => ({ getClips: () => new Promise(r => { resolve = r; }), getProjectPlayback: async () => null, getProjectDownloads: async () => [] }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ me: session, projectId: 'old' });
  const request = page.loadClips('old');
  page.setState({ projectId: 'new' });
  resolve({ clips: [{ id: 'old-clip' }] });
  await request;
  assert.equal(page.state.clipsRes, null);
});

test('returning to transcript editing preserves unsaved corrections', t => {
  const page = component(S);
  t.after(() => page.componentWillUnmount());
  page.setState({ projectId: 'p1', transcript: { revision: 1, segments: [{ id: 's1', text: 'Original', start: 0, words: [] }] }, drafts: { s1: 'Corrected name' } });
  page.loadTranscript = () => { throw new Error('Must not discard an existing draft'); };
  page.renderVals().goTranscript();
  assert.equal(page.state.route, 'transcript');
  assert.equal(page.renderVals().segments[0].text, 'Corrected name');
  assert.equal(page.renderVals().notDirty, false);
});

test('a finished render does not pull the user away from another screen', async t => {
  let finish;
  const page = component({ ...S, createApi: () => ({
    renderClip: () => new Promise(resolve => { finish = resolve; }),
    getClips: async () => ({ clips: [] }), getProjectPlayback: async () => null, getProjectDownloads: async () => [],
  }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ route: 'editor', projectId: 'p1', previewId: 'c1', me: session });
  const rendering = page.renderClip('c1');
  page.setState({ route: 'settings' });
  finish({ url: '/media/render.mp4' });
  await rendering;
  assert.equal(page.state.route, 'settings');
  assert.equal(page.state.result.url, 'http://api.test/media/render.mp4');
  assert.match(page.state.toast, /Short is ready/);
});


for (const duration of [10800, 10801]) {
  test(`source selection ${duration === 10800 ? 'accepts three hours' : 'rejects more than three hours'}`, t => {
    const video = { duration, videoWidth: 1920, videoHeight: 1080 };
    const page = component(S, 'http://api.test', {
      document: { createElement: () => video },
      URL: { createObjectURL: () => 'blob:long-source', revokeObjectURL() {} },
    });
    t.after(() => page.componentWillUnmount());
    const file = { name: 'long-episode.mp4', type: 'video/mp4', size: 1000 };
    page.onFile({ target: { files: [file] } });
    video.onloadedmetadata();
    assert.equal(page.state.np.probing, false);
    if (duration === 10800) {
      assert.equal(page.state.np.source.durationSec, 10800);
      assert.equal(page.state.pendingFile, file);
      assert.equal(page.state.np.error, null);
    } else {
      assert.equal(page.state.np.source, null);
      assert.equal(page.state.pendingFile, null);
      assert.match(page.state.np.error, /3 hours/);
    }
  });
}
