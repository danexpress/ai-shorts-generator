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

// Cookie sessions: the browser holds httpOnly cookies; the client keeps only the
// session's CSRF token in memory and never writes to browser storage.
const signedIn = { ...session, csrfToken: 'csrf-1', access_token: 'api-token', expires_in: 3600 };

function recorder(routes = {}) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, ...init });
    const path = new URL(url, 'http://api.test').pathname;
    const route = routes[path];
    if (route) return typeof route === 'function' ? route(init, calls) : route;
    if (path === '/v1/auth/google') return response(signedIn);
    return response(session);
  };
  return { calls, fetch };
}

test('requests carry cookies, never a stored token, and nothing touches browser storage', async () => {
  const storage = memoryStorage();
  const { calls, fetch } = recorder();
  const api = S.createHttpApi({ baseUrl: 'http://api.test/', fetch, storage });
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.me();
  assert.equal(storage.items.size, 0);
  for (const call of calls) {
    assert.equal(call.credentials, 'include');
    assert.equal(call.headers.Authorization, undefined);
  }
  assert.deepEqual(JSON.parse(calls[0].body), { email: 'maya@example.com', password: 'secret' });
});

test('the CSRF token from sign-in or /me is sent on writes only', async () => {
  const { calls, fetch } = recorder({ '/v1/me': response({ ...session, csrfToken: 'csrf-from-me' }) });
  const api = S.createHttpApi({ baseUrl: 'http://api.test', fetch });
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.processProject('prj 1', { goal: 'viral' }, { idempotencyKey: 'one' });
  assert.equal(calls[1].url, 'http://api.test/v1/projects/prj%201/process');
  assert.equal(calls[1].headers['X-CSRF-Token'], 'csrf-1');
  assert.equal(calls[1].headers['Idempotency-Key'], 'one');
  // After a page reload the token comes from /v1/me.
  const reloaded = S.createHttpApi({ baseUrl: 'http://api.test', fetch });
  await reloaded.me();
  assert.equal(calls.at(-1).headers['X-CSRF-Token'], undefined);
  await reloaded.getProjectPlayback('project-1');
  assert.equal(calls.at(-1).method, 'POST');
  assert.equal(calls.at(-1).headers['X-CSRF-Token'], 'csrf-from-me');
});

test('uploads send raw video bytes with their content type', async () => {
  const { calls, fetch } = recorder();
  const api = S.createHttpApi({ baseUrl: 'http://api.test/', fetch });
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  const file = new Blob(['video-bytes'], { type: 'video/mp4' });
  await api.uploadMedia('upload-1', file);
  assert.equal(calls[1].url, 'http://api.test/v1/uploads/upload-1/media');
  assert.equal(calls[1].headers['Content-Type'], 'video/mp4');
  assert.equal(calls[1].body, file);
});

test('an expired session refreshes once, shared by concurrent requests, then retries', async () => {
  let expired = true;
  const { calls, fetch } = recorder({
    '/v1/projects': () => (expired ? response({ error: { code: 'UNAUTHENTICATED' } }, 401) : response([])),
    '/v1/auth/refresh': () => { expired = false; return response({ csrfToken: 'csrf-2', expiresIn: 900 }); },
  });
  const api = S.createHttpApi({ fetch });
  const [a, b] = await Promise.all([api.listProjects(), api.listProjects()]);
  assert.deepEqual([a, b], [[], []]);
  assert.equal(calls.filter((c) => c.url.endsWith('/v1/auth/refresh')).length, 1);
  await api.createUpload({});
  assert.equal(calls.at(-1).headers['X-CSRF-Token'], 'csrf-2');
});

test('a refresh racing another tab retries the request instead of signing out', async () => {
  let attempts = 0;
  const { fetch } = recorder({
    '/v1/me': () => (++attempts === 1 ? response({ error: { code: 'UNAUTHENTICATED' } }, 401) : response(session)),
    '/v1/auth/refresh': response({ error: { code: 'REFRESH_IN_PROGRESS' } }, 409),
  });
  assert.equal((await S.createHttpApi({ fetch }).me()).user.email, session.user.email);
});

test('a failed refresh ends the session and reports UNAUTHENTICATED', async () => {
  const { calls, fetch } = recorder({
    '/v1/me': response({ error: { code: 'UNAUTHENTICATED' } }, 401),
    '/v1/auth/refresh': response({ error: { code: 'UNAUTHENTICATED' } }, 401),
  });
  const api = S.createHttpApi({ fetch });
  await assert.rejects(api.me(), { code: 'UNAUTHENTICATED' });
  assert.equal(calls.filter((c) => c.url.endsWith('/v1/me')).length, 1);  // no retry loop
});

test('session endpoints never trigger an automatic refresh', async () => {
  const { calls, fetch } = recorder({ '/v1/auth/google': response({ error: { code: 'UNAUTHENTICATED' } }, 401) });
  await assert.rejects(S.createHttpApi({ fetch }).signIn({ email: 'x@example.com', password: 'bad' }), { code: 'UNAUTHENTICATED' });
  assert.equal(calls.some((c) => c.url.endsWith('/v1/auth/refresh')), false);
});

for (const code of ['UNAUTHENTICATED', 'ACCOUNT_DISABLED', 'NOT_INVITED']) {
  test(`HTTP ${code} forgets the CSRF token`, async () => {
    let fail = false;
    const { calls, fetch } = recorder({
      '/v1/me': () => (fail ? response({ error: { code } }, code === 'UNAUTHENTICATED' ? 401 : 403) : response(session)),
      '/v1/auth/refresh': response({ error: { code: 'UNAUTHENTICATED' } }, 401),
    });
    const api = S.createHttpApi({ fetch });
    await api.signIn({ email: 'maya@example.com', password: 'secret' });
    fail = true;
    await assert.rejects(api.me(), { code });
    await api.createUpload({});
    assert.equal(calls.at(-1).headers['X-CSRF-Token'], undefined);
  });
}

test('sign-out forgets the CSRF token even when the network fails', async () => {
  const { calls, fetch } = recorder({ '/v1/auth/logout': () => { throw new Error('offline'); } });
  const api = S.createHttpApi({ fetch });
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await assert.rejects(api.signOut(), { code: 'NETWORK_ERROR' });
  await api.createUpload({});
  assert.equal(calls.at(-1).headers['X-CSRF-Token'], undefined);
});

test('a delayed login or private response cannot apply after sign-out', async () => {
  let resolveLogin, resolveMe;
  const { fetch } = recorder({
    '/v1/auth/google': () => new Promise((resolve) => { resolveLogin = resolve; }),
    '/v1/me': () => new Promise((resolve) => { resolveMe = resolve; }),
    '/v1/auth/logout': response({ ok: true }),
  });
  const api = S.createHttpApi({ fetch });
  const login = api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.signOut();
  resolveLogin(response(signedIn));
  await assert.rejects(login, { code: 'SESSION_CHANGED' });
  const pending = api.me();
  await api.signOut();
  resolveMe(response(session));
  await assert.rejects(pending, { code: 'SESSION_CHANGED' });
});

test('a sign-in response without a session is an explicit server error', async () => {
  const { fetch } = recorder({ '/v1/auth/google': response(session) });
  await assert.rejects(S.createHttpApi({ fetch }).signIn({ email: 'maya@example.com', password: 'secret' }), { code: 'SERVER_ERROR' });
});

test('Google sign-in starts at the API and the login page shows the options it offers', async (t) => {
  assert.equal(S.googleSignInUrl('http://api.test/'), 'http://api.test/v1/auth/google/start');
  const page = component({ ...S, createApi: () => ({ getAuthOptions: async () => ({ google: true, password: false }), me: async () => { throw new S.ApiError('UNAUTHENTICATED', null, 401); } }) });
  t.after(() => page.componentWillUnmount());
  await page.componentDidMount();
  await new Promise((r) => setImmediate(r));
  const v = page.renderVals();
  assert.equal(v.googleEnabled, true);
  assert.equal(v.passwordEnabled, false);
  assert.equal(page.state.route, 'login');
});

test('an auth_error returned by Google sign-in is shown once and removed from the URL', async (t) => {
  const replaced = [];
  const globals = { URLSearchParams };
  const page = component({ ...S, createApi: () => ({ getAuthOptions: async () => ({ google: true, password: true }), me: async () => { throw new S.ApiError('UNAUTHENTICATED', null, 401); } }) }, 'http://api.test', globals);
  t.after(() => page.componentWillUnmount());
  // The page reads window.location / history from its vm context: inject an auth_error.
  const win = vm.runInContext('window', vmContextOf(page));
  win.location = { search: '?auth_error=NOT_INVITED', pathname: '/', hash: '' };
  win.history = { replaceState: (...args) => replaced.push(args[2]) };
  await page.componentDidMount();
  await new Promise((r) => setImmediate(r));
  assert.equal(page.state.loginError, S.ERROR_COPY.NOT_INVITED);
  assert.deepEqual(replaced, ['/']);
});

test('a video dropped anywhere on New Project is selected, and drops elsewhere are blocked', async t => {
  const page = component({ ...S, createApi: () => ({ getAuthOptions: async () => ({}), me: async () => session, listProjects: async () => [] }) });
  t.after(() => page.componentWillUnmount());
  const listeners = {};
  Object.assign(vm.runInContext('window', vmContextOf(page)), { addEventListener: (type, fn) => { listeners[type] = fn; }, removeEventListener() {} });
  await page.componentDidMount();
  const picked = [];
  page.onFile = (e) => picked.push(e.target.files[0]);
  const file = { name: 'talk.mp4', type: 'video/mp4' };
  const fire = (type) => { const e = { type, prevented: false, preventDefault() { this.prevented = true; }, dataTransfer: { types: ['Files'], files: [file], dropEffect: 'none' } }; listeners[type](e); return e; };

  page.setState({ route: 'dashboard' });
  fire('dragenter');
  assert.equal(page.state.fileDragOver, undefined);
  assert.equal(fire('drop').prevented, true);
  assert.deepEqual(picked, []);

  page.setState({ route: 'new', np: { ...page.state.np, tab: 'url' } });
  fire('dragenter');
  assert.equal(page.state.fileDragOver, true);
  assert.equal(fire('dragover').dataTransfer.dropEffect, 'copy');
  fire('drop');
  assert.equal(page.state.fileDragOver, false);
  assert.equal(page.state.np.tab, 'upload');
  assert.deepEqual(picked, [file]);
});

test('choosing a non-video file shows a format error without probing it', t => {
  const page = component({ ...S, createApi: () => ({}) });
  t.after(() => page.componentWillUnmount());
  page.onFile({ target: { files: [{ name: 'notes.txt', type: 'text/plain' }] } });
  assert.equal(page.state.np.probing, false);
  assert.match(page.state.np.error, /MP4, MOV, or WebM/);
});

function component(services, baseUrl = 'http://api.test', globals = {}) {
  const html = fs.readFileSync(path.join(__dirname, 'AI Shorts Generator.dc.html'), 'utf8');
  const script = html.match(/<script type="text\/x-dc"[^>]*>([\s\S]*?)<\/script>/)[1];
  class DCLogic {
    props = {};
    setState(update) { this.state = { ...this.state, ...(typeof update === 'function' ? update(this.state) : update) }; }
  }
  const context = vm.createContext({ DCLogic, URL, window: { ShortsServices: services, SHORTS_CONFIG: { apiBaseUrl: baseUrl } }, setTimeout, clearTimeout, setInterval, clearInterval, ...globals });
  const page = vm.runInContext(script + '\nnew Component()', context);
  contexts.set(page, context);
  return page;
}
const contexts = new WeakMap();
const vmContextOf = (page) => contexts.get(page);

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
  // The URL tab is always visible; with import off it cannot start a project.
  assert.equal(page.renderVals().tabs.length, 2);
  page.setNp({ tab: 'url', url: 'https://youtu.be/abcdefghijk', rights: true });
  assert.equal(page.renderVals().urlImportOff, true);
  assert.equal(page.renderVals().cannotStart, true);
  page.setState({ me: { ...session, features: { youtubeImport: true } } });
  assert.equal(page.renderVals().tabs.length, 2);
  assert.equal(page.renderVals().cannotStart, false);
  page.setNp({ tab: 'upload', url: '', rights: false });
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
  // Node's fetch has no cookie jar; this one stands in for the browser's (by name and path).
  const jar = new Map();
  const browserFetch = async (url, init = {}) => {
    const { pathname } = new URL(url);
    const cookies = [...jar].filter(([, c]) => pathname.startsWith(c.path)).map(([name, c]) => `${name}=${c.value}`);
    const res = await fetch(url, { ...init, headers: { ...init.headers, ...(cookies.length ? { Cookie: cookies.join('; ') } : {}) } });
    for (const line of res.headers.getSetCookie()) {
      const [pair, ...attrs] = line.split(';').map((part) => part.trim());
      const [name, value] = pair.split('=');
      const path = (attrs.find((a) => a.toLowerCase().startsWith('path=')) || 'path=/').slice(5);
      if (/max-age=0/i.test(line) || value === '' || value === '""') jar.delete(name); else jar.set(name, { value, path });
    }
    return res;
  };
  const storage = memoryStorage();
  const persistentServices = { ...S, createApi: config => S.createApi({ ...config, fetch: browserFetch, storage }) };
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
  assert.equal(page.renderVals().tabs.length, 2);
  assert.equal(page.renderVals().urlImportOff, true);  // the isolated backend has import off
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
  assert.equal(storage.items.size, 0);  // nothing is ever kept in browser storage
  assert.equal(jar.size, 0);  // sign-out cleared the session cookies
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
  page.setState({ me: { ...session, features: { youtubeImport: true } } });
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

const socialCopy = { youtube: {title:'A faithful title', description:'The actual insight.', hashtags:['#Shorts']}, tiktok: {caption:'The insight in a conversational tone.', hashtags:[]}, x:{text:'A distinct insight for X.'} };
const socialAccounts = {accounts:['youtube','tiktok','x'].map(platform=>({id:'acct-'+platform,platform,account_name:'Creator',status:'connected'})),platforms:['youtube','tiktok','x'].map(platform=>({platform,enabled:true})),copy_available:true};
async function publishingPage(t, overrides={}) {
  const api={me:async()=>session,listProjects:async()=>[],listSocialAccounts:async()=>socialAccounts,getSocialCopy:async()=>({copy:socialCopy,transcript_verified:true,source_transcript:'The actual insight.'}),getPublishingHistory:async()=>[],getTikTokCreatorInfo:async()=>({privacy_options:['SELF_ONLY']}),...overrides};
  const page=component({...S,createApi:()=>api});t.after(()=>page.componentWillUnmount());await page.componentDidMount();
  await page.openPublish({id:'rnd_abc',url:'http://api.test/v1/media/rendered/rnd_abc'});
  return page;
}

test('opening cached publishing copy never posts automatically and selects no platforms',async t=>{
  let publishes=0,generates=0;
  const page=await publishingPage(t,{publishRender:async()=>publishes++,generateSocialCopy:async()=>generates++});
  assert.equal(page.state.route,'publish');assert.equal(Object.keys(page.state.publishing.selected).length,0);
  assert.equal(page.state.publishing.copy.youtube.title,socialCopy.youtube.title);assert.equal(publishes,0);assert.equal(generates,0);
});

test('Publish Everywhere sends only explicit selections and reuses the request key after a network failure',async t=>{
  const calls=[];
  const page=await publishingPage(t,{publishRender:async(...args)=>{calls.push(args);throw new S.ApiError('NETWORK_ERROR');}});
  page.editSocialCopy('x','text','My own reviewed text');page.selectPublishPlatform('x',true);
  await page.publishEverywhere();await page.publishEverywhere();
  assert.equal(calls.length,2);assert.equal(calls[0][1].approved,true);assert.equal(calls[0][1].targets.length,1);
  assert.equal(calls[0][1].targets[0].platform,'x');assert.equal(calls[0][1].targets[0].x.text,'My own reviewed text');
  assert.equal(calls[0][2].idempotencyKey,calls[1][2].idempotencyKey);
});

test('successful admission clears selections and leaves successful platforms out of later batches',async t=>{
  const calls=[];let batch;
  const page=await publishingPage(t,{publishRender:async(id,body)=>{calls.push(body);return batch={id:'batch',created_at:Date.now(),jobs:body.targets.map(target=>({id:'job-'+target.platform,platform:target.platform,status:'published'}))};},getPublishBatch:async()=>batch});
  page.selectPublishPlatform('youtube',true);await page.publishEverywhere();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(Object.keys(page.state.publishing.selected).length,0);
  page.selectPublishPlatform('x',true);await page.publishEverywhere();
  assert.equal(calls[1].targets.length,1);assert.equal(calls[1].targets[0].platform,'x');
});

test('individual regeneration keeps other platform edits and passes transcript approval',async t=>{
  const requests=[];
  const page=await publishingPage(t,{regenerateSocialCopy:async(id,body)=>{requests.push(body);return {copy:{youtube:{...socialCopy.youtube,title:'Another faithful title'}}};}});
  page.editSocialCopy('x','text','My edited X copy');await page.generateSocialCopy('youtube');
  assert.equal(page.state.publishing.copy.x.text,'My edited X copy');assert.equal(page.state.publishing.copy.youtube.title,'Another faithful title');
  assert.equal(requests[0].platforms.join(','),'youtube');assert.equal(requests[0].transcript_confirmed,true);
});

test('late AI output cannot restore private publishing data after logout',async t=>{
  let finish;
  const page=await publishingPage(t,{regenerateSocialCopy:()=>new Promise(resolve=>finish=resolve)});
  const pending=page.generateSocialCopy('x');page.resetSession();finish({copy:{x:{text:'Private late output'}}});await pending;
  assert.equal(page.state.route,'login');assert.equal(Object.keys(page.state.publishing.copy).length,0);
  assert.equal(page.state.social.accounts.length,0);assert.equal(page.state.publishing.history.length,0);
});

test('retry calls only the failed job endpoint',async t=>{
  const retries=[];
  const batch={id:'batch',created_at:Date.now(),jobs:[{id:'yt',platform:'youtube',status:'published'},{id:'x',platform:'x',status:'failed',retryable:true}]};
  const page=await publishingPage(t,{getPublishingHistory:async()=>[batch],retryPublishJob:async id=>retries.push(id),getPublishBatch:async()=>batch});
  await page.retrySocialJob(batch.jobs[1]);assert.deepEqual(retries,['x']);
});

test('paths with several parameters fill them in order and encode each one', async () => {
  const calls = [];
  const api = S.createHttpApi({ baseUrl: 'http://api.test', storage: null, fetch: async (url, init) => { calls.push({ url, ...init }); return response({}); } });
  await api.saveCaptions('prj 1', 'clp/2', { preset: 'creator-pop' });
  assert.equal(calls[0].url, 'http://api.test/v1/projects/prj%201/clips/clp%2F2/captions');
  assert.equal(calls[0].method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[0].body), { preset: 'creator-pop' });
  await api.listCaptionPresets();
  assert.equal(calls[1].url, 'http://api.test/v1/caption-presets');
});

test('caption helpers mirror backend layout and never move words past neighbours', () => {
  assert.deepEqual(S.captionLines(['most', 'founders', 'hire']), [['most'], ['founders', 'hire']]);
  assert.deepEqual(S.captionLines(['hire', 'for', 'the']), [['hire', 'for', 'the']]);
  const chunk = { words: [{ text: 'teh', start: 1, end: 1.5 }, { text: 'idea', start: 1.5, end: 2 }] };
  assert.deepEqual(S.retimeCaptionText(chunk, 'the idea').words, [{ text: 'the', start: 1, end: 1.5 }, { text: 'idea', start: 1.5, end: 2 }]);
  const split = S.retimeCaptionText(chunk, 'the big idea').words;
  assert.equal(split.length, 3);
  assert.ok(split.every((w) => w.estimated));
  assert.equal(split[0].start, 1);
  assert.equal(split[2].end, 2);
  assert.equal(S.retimeCaptionText(chunk, '   '), null);
  const next = { words: [{ text: 'n', start: 2.05, end: 2.5 }] };
  const { chunks, moved } = S.nudgeCaption([chunk, next], 0, 0.1, 0, 10);
  assert.equal(moved, 0.05);
  assert.equal(chunks[0].words[1].end, 2.05);
  assert.equal(S.nudgeCaption([chunk, next], 1, 0.1, 0, 2.5).moved, 0);
});

test('editor loads captions, commits text on blur, saves edits before rendering', async t => {
  const calls = [];
  const track = { projectId: 'p1', clipId: 'c1', clipStart: 4, clipEnd: 24, preset: 'clean-bold', keepFillers: false, edited: false, stale: false, transcriptRevision: 1,
    chunks: [{ id: 'c0', start: 4, end: 4.6, words: [{ text: 'Most', start: 4, end: 4.3 }, { text: 'foundrs', start: 4.3, end: 4.6 }] }] };
  const presets = [{ id: 'clean-bold', label: 'Clean Bold', description: '', style: { font_name: 'Montserrat ExtraBold' } }];
  const page = component({ ...S, createApi: () => ({
    getCaptions: async (...args) => { calls.push(['get', ...args]); return track; },
    listCaptionPresets: async () => presets,
    saveCaptions: async (id, clipId, body) => { calls.push(['save', id, clipId, body]); return { ...track, edited: true, chunks: body.chunks.map((c, i) => ({ id: 'c' + i, start: c.words[0].start, end: c.words.at(-1).end, words: c.words })) }; },
    renderClip: async (id, body) => { calls.push(['render', id, body]); return { id: 'r1', url: '/m', downloadUrl: '/m?d' }; },
    getClips: async () => ({ clips: [] }), getProjectPlayback: async () => null, getProjectDownloads: async () => [],
  }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ me: session, projectId: 'p1', clipsRes: { clips: [{ id: 'c1', rank: 1, start: 4, end: 24, duration: 20, hook: 'Hook', scores: { hook: 90, value: 80, standalone: 85, visual: 70 } }] } });
  page.renderVals().clips[0].preview();
  await new Promise((r) => setImmediate(r));
  assert.equal(JSON.stringify(calls[0]), JSON.stringify(['get', 'p1', 'c1']));
  let c = page.renderVals().appProps.v.captions;
  assert.equal(c.ready, true);
  assert.equal(c.rows[0].text, 'Most foundrs');
  assert.equal(c.presets[0].pressed, true);

  // Typing alone never re-times; committing the same word count keeps real timing.
  c.rows[0].type({ target: { value: 'Most founders' } });
  assert.equal(page.state.captions.dirty, false);
  c = page.renderVals().appProps.v.captions;
  c.rows[0].commit({ target: { value: 'Most founders' } });
  assert.equal(page.state.captions.dirty, true);
  // Objects from the page's vm realm compare by value through JSON.
  assert.equal(JSON.stringify(page.state.captions.draft[0].words[1]), JSON.stringify({ text: 'founders', start: 4.3, end: 4.6 }));

  page.renderVals().appProps.v.captions.framings[1].pick();  // fit
  await page.renderVals().appProps.v.renderPreview();
  const [save, render] = calls.slice(1);
  assert.equal(save[0], 'save');
  assert.equal(save[3].chunks[0].words[1].text, 'founders');
  assert.equal(JSON.stringify(render), JSON.stringify(['render', 'p1', { clipId: 'c1', resolution: 1080, captions: true, framing: 'fit' }]));
  assert.equal(page.state.captions.dirty, false);
});

test('LLM highlights without sub-scores show one engagement score and signal note', (t) => {
  const page = component(S);
  t.after(() => page.componentWillUnmount());
  page.setState({ me: session, projectId: 'p1', clipsRes: { clips: [
    { id: 'c1', rank: 1, start: 4, end: 30, duration: 26, hook: 'Hook', reason: 'Why', combined: 87, scores: null, signals: { loudnessLu: 2.4, sceneChanges: 1, adjustment: 0.6 } },
    { id: 'c2', rank: 2, start: 40, end: 70, duration: 30, hook: 'Demo', reason: 'Why', combined: 80, scores: { hook: 90, value: 80, standalone: 85, visual: 70 } },
  ] } });
  const [llm, demo] = page.renderVals().clips;
  assert.equal(JSON.stringify(llm.scores), JSON.stringify([{ label: 'Engagement', value: 87 }]));
  assert.equal(llm.signalNote, 'Energy +2.4 LU · 1 scene change');
  assert.equal(demo.scores.length, 4);
  assert.equal(demo.signalNote, '');
});

test('account deletion needs the exact email, then signs out with a clear summary', async (t) => {
  const calls = [];
  const page = component({ ...S, createApi: () => ({
    deleteAccount: async (body) => { calls.push(body); return { ok: true, revokedPlatforms: ['youtube'], notRevokedPlatforms: ['tiktok'] }; },
  }) });
  t.after(() => page.componentWillUnmount());
  page.componentDidMount();
  page.setState({ me: session, route: 'settings' });
  const v = () => page.renderVals().appProps.v;
  v().accountDeletion.onConfirm({ target: { value: 'someone@example.com' } });
  assert.equal(v().accountDeletion.canDelete, false);
  await page.deleteAccount();
  assert.equal(calls.length, 0);
  v().accountDeletion.onConfirm({ target: { value: ' MAYA@example.com ' } });
  assert.equal(v().accountDeletion.canDelete, true);
  await v().accountDeletion.submit();
  assert.equal(JSON.stringify(calls), JSON.stringify([{ confirmEmail: 'MAYA@example.com' }]));
  assert.equal(page.state.route, 'login');
  assert.equal(page.state.me, null);
  assert.match(page.state.loginError, /deleted/);
  assert.match(page.state.loginError, /TikTok/);
  assert.equal(page.state.accountDeletion.confirm, '');
});

test('the HTTP client sends account deletion as DELETE with a JSON body and CSRF token', async () => {
  const { calls, fetch } = recorder();
  const api = S.createHttpApi({ baseUrl: 'http://api.test', fetch });
  await api.signIn({ email: 'maya@example.com', password: 'secret' });
  await api.deleteAccount({ confirmEmail: 'maya@example.com' });
  const call = calls.at(-1);
  assert.equal(call.method, 'DELETE');
  assert.equal(call.url, 'http://api.test/v1/me');
  assert.equal(call.headers['X-CSRF-Token'], 'csrf-1');
  assert.deepEqual(JSON.parse(call.body), { confirmEmail: 'maya@example.com' });
});
