// Player smoke/regression check on an isolated fixture database.
// Requires a production build (npm run build) and Chrome with --remote-debugging-port=9222.
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const temp = mkdtempSync(join(tmpdir(), 'jkt48-player-'));
const VIDEO = 'R8pnx79dyDQ';
const PORT = 3108;
const origin = `http://localhost:${PORT}`;
const delay = ms => new Promise(r => setTimeout(r, ms));

const db = new DatabaseSync(join(temp, 'fixture.db'));
db.exec(`CREATE TABLE member_hls (username TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER, hls_confirmed INTEGER, last_live_at TEXT);
CREATE TABLE merge_groups (id INTEGER PRIMARY KEY, live_title TEXT, live_key TEXT);
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, live_id TEXT, member_username TEXT, member_name TEXT, merge_group_id INTEGER, started_at TEXT, created_at TEXT, youtube_video_id TEXT, status TEXT, file_size_bytes INTEGER);
CREATE TABLE web_publications (youtube_video_id TEXT PRIMARY KEY, published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0,1)), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
INSERT INTO member_hls VALUES ('jkt48_test', 'Test Member', 1, 1, NULL);
INSERT INTO merge_groups VALUES (1, 'PLAYER_FIXTURE_TITLE', 'INTERNAL_KEY_SENTINEL');
INSERT INTO live_sessions VALUES (1, 'player-live', 'jkt48_test', 'Test Member', 1, '2026-09-01', '2026-09-01', '${VIDEO}', 'done_youtube', 9000);
INSERT INTO web_publications VALUES ('${VIDEO}', 1, datetime('now'));`);
db.close();

const server = spawn(process.execPath, [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', String(PORT)], {
  cwd: root,
  env: { ...process.env, NODE_ENV: 'production', ADMIN_SECRET: randomBytes(32).toString('hex'), APP_ORIGIN: origin, DB_PATH: join(temp, 'fixture.db') },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = ''; server.stdout.on('data', d => logs += d); server.stderr.on('data', d => logs += d);

let ws; let target;
try {
  let up = false;
  for (let i = 0; i < 80; i++) { try { if ((await fetch(`${origin}/watch/${VIDEO}`)).ok) { up = true; break; } } catch {} await delay(250); }
  assert(up, logs);

  target = await fetch(`http://localhost:9222/json/new?${origin}/watch/${VIDEO}`, { method: 'PUT' }).then(r => r.json());
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const pending = new Map();
  ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } });
  const send = (method, params = {}) => new Promise((resolve, reject) => { pending.set(++id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression, userGesture = false) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture });
    if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const consoleErrors = [];
  ws.addEventListener('message', e => {
    const m = JSON.parse(e.data);
    if (m.method === 'Runtime.exceptionThrown') consoleErrors.push(m.params.exceptionDetails?.exception?.description || 'exception');
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push((m.params.args || []).map(a => a.value ?? a.description ?? '').join(' '));
  });
  await send('Runtime.enable');
  const clickTheater = async () => { await evaluate("document.querySelector('.theater-btn').click()", true); await delay(600); };
  let checks = 0;

  await send('Page.enable');
  for (let i = 0; i < 60; i++) { if (await evaluate("!!document.querySelector('.theater-btn')")) break; await delay(500); }
  assert(await evaluate("!!document.querySelector('.theater-btn')"), 'Theater button missing');
  await evaluate("localStorage.removeItem('jkt48_player_fit_mode')");
  await send('Page.reload', { ignoreCache: true });
  await delay(2000);
  for (let i = 0; i < 60; i++) { if (await evaluate("document.readyState === 'complete' && !!document.querySelector('.theater-btn')")) break; await delay(300); }
  assert(await evaluate("document.querySelector('.player-inner-container').className.includes('ratio-fit')"), 'Default fit mode missing');
  await evaluate("(() => { window.originalPlayer = document.querySelector('[data-media-player]'); return !!window.originalPlayer; })()");

  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, mobile: false, deviceScaleFactor: 1 });
  await clickTheater();
  assert(await evaluate("!!document.querySelector('.theater-mode')"), 'Theater mode not applied');
  assert(await evaluate('document.fullscreenElement === document.documentElement'), 'Desktop fullscreen not entered');

  for (const [width, height, mobile] of [[1440, 900, false], [390, 844, true], [844, 390, true]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, mobile, deviceScaleFactor: 1 });
    await send('Emulation.setTouchEmulationEnabled', { enabled: mobile, maxTouchPoints: 5 });
    for (let mode = 0; mode < 3; mode++) {
      await evaluate("document.querySelector('.ratio-btn').click()");
      await delay(300);
      const result = await evaluate(`(() => {
        const shell = document.querySelector('.theater-mode');
        const rect = shell.getBoundingClientRect();
        const p = shell.querySelector('.player-inner-container').getBoundingClientRect();
        return { viewport: Math.abs(rect.width-innerWidth)<2 && Math.abs(rect.height-innerHeight)<2 && Math.abs(rect.top)<2,
          bounds: p.left>=-1 && p.top>=-1 && p.right<=innerWidth+1 && p.bottom<=innerHeight+1,
          samePlayer: originalPlayer === document.querySelector('[data-media-player]'),
          inert: document.querySelector('.watch-details-card').inert,
          locked: document.body.style.overflow === 'hidden' };
      })()`);
      assert(Object.values(result).every(Boolean), `layout ${width}x${height} mode ${mode}: ${JSON.stringify(result)}`);
      checks++;
    }
    await evaluate("document.querySelector('[aria-label^=\"Atur volume\"]').focus(); document.querySelector('[aria-label^=\"Atur volume\"]').click()");
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await delay(200);
    assert(await evaluate("!document.querySelector('.volume-panel') && !!document.querySelector('.theater-mode')"), 'First Escape should only close volume');
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await delay(250);
    assert(await evaluate("!document.querySelector('.theater-mode') && !document.querySelector('.watch-details-card').inert"), 'Escape cleanup failed');
    assert(await evaluate("document.activeElement === document.querySelector('.theater-btn')"), 'Focus not restored');
    await clickTheater();
  }

  const stored = await evaluate("localStorage.getItem('jkt48_player_fit_mode')");
  assert(stored === 'fit', `Fit mode should cycle back to fit, got ${stored}`);
  await evaluate("document.querySelector('.ratio-btn').click()");
  await delay(300);
  assert(await evaluate("localStorage.getItem('jkt48_player_fit_mode')") === 'fill', 'Fit mode not persisted');
  assert(await evaluate("document.querySelector('.player-inner-container').className.includes('ratio-fill')"), 'Persisted mode not applied');

  await clickTheater();
  await delay(300);
  assert(await evaluate("!document.querySelector('.theater-mode')"), 'Theater should be closed before reload');
  await send('Page.navigate', { url: `${origin}/watch/${VIDEO}` });
  await delay(2500);
  let reloaded = false;
  for (let i = 0; i < 60; i++) { if (await evaluate("document.readyState === 'complete' && !!document.querySelector('[data-media-player]')")) { reloaded = true; break; } await delay(300); }
  assert(reloaded, 'Player missing after reload: ' + JSON.stringify(await evaluate("({ href: location.href, state: document.readyState, ratio: !!document.querySelector('.ratio-btn'), text: document.body.innerText.slice(0, 160) })")));
  assert(await evaluate("document.querySelector('.player-inner-container').className.includes('ratio-fill')"), 'Stored fit mode not restored after reload');
  assert(await evaluate("localStorage.getItem('jkt48_player_fit_mode')") === 'fill', 'Stored value lost after reload');
  assert(!consoleErrors.some(text => /hydrat|mismatch/i.test(text)), 'Hydration error: ' + consoleErrors.join(' | '));

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(import.meta.dirname, 'player-check.png'), Buffer.from(shot.data, 'base64'));
  console.log(`PASS: player renders, theater/fullscreen, ${checks} responsive ratio layouts, player identity, volume Escape order, focus restore, fit-mode persistence.`);
} finally {
  if (ws) ws.close();
  if (target) await fetch('http://localhost:9222/json/close/' + target.id);
  server.kill();
  await new Promise(r => server.once('exit', r));
  rmSync(temp, { recursive: true, force: true });
}
