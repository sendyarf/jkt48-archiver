// Verifikasi halaman ujicoba pemutar (/admin/trial): landscape vs vertikal.
// Membutuhkan: hasil `npm run build` dan Chrome dengan --remote-debugging-port=9222.
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const temp = mkdtempSync(join(tmpdir(), 'jkt48-trial-'));
const PORT = 3110;
const origin = `http://localhost:${PORT}`;
const secret = randomBytes(32).toString('hex');
const VIDEO = 'R8pnx79dyDQ';
const delay = ms => new Promise(r => setTimeout(r, ms));

const db = new DatabaseSync(join(temp, 'fixture.db'));
db.exec(`CREATE TABLE member_hls (username TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER, hls_confirmed INTEGER, last_live_at TEXT);
CREATE TABLE merge_groups (id INTEGER PRIMARY KEY, live_title TEXT, live_key TEXT);
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, live_id TEXT, member_username TEXT, member_name TEXT, merge_group_id INTEGER, started_at TEXT, created_at TEXT, youtube_video_id TEXT, status TEXT, file_size_bytes INTEGER);
CREATE TABLE web_publications (youtube_video_id TEXT PRIMARY KEY, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
INSERT INTO member_hls VALUES ('jkt48_test','Test Member',1,1,NULL);
INSERT INTO merge_groups VALUES (1,'T','K');
INSERT INTO live_sessions VALUES (1,'l','jkt48_test','Test Member',1,'2026-09-01','2026-09-01','${VIDEO}','done_youtube',1);
INSERT INTO web_publications VALUES ('${VIDEO}',1,datetime('now'));`);
db.close();

const server = spawn(process.execPath, [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', String(PORT)], {
  cwd: root,
  env: { ...process.env, NODE_ENV: 'production', ADMIN_SECRET: secret, APP_ORIGIN: origin, DB_PATH: join(temp, 'fixture.db'), TURNSTILE_SITE_KEY: 'off', TURNSTILE_SECRET_KEY: 'off' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = ''; server.stdout.on('data', d => logs += d); server.stderr.on('data', d => logs += d);

let ws; let target;
try {
  let up = false;
  for (let i = 0; i < 80; i++) { try { if ((await fetch(`${origin}/api/auth`)).ok) { up = true; break; } } catch {} await delay(250); }
  assert(up, logs);

  const login = await fetch(`${origin}/api/auth`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: origin },
    body: JSON.stringify({ secret }),
  });
  assert.equal(login.status, 200, 'Login admin gagal');
  const token = login.headers.get('set-cookie').split(';')[0].split('=')[1];
  assert(token, 'Token sesi tidak ditemukan');

  target = await fetch('http://localhost:9222/json/new?about:blank', { method: 'PUT' }).then(r => r.json());
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const pending = new Map();
  ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } });
  const send = (method, params = {}) => new Promise((resolve, reject) => { pending.set(++id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expr, userGesture = false) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true, userGesture }); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };

  await send('Page.enable');
  await send('Network.enable');
  await send('Network.clearBrowserCookies');
  await send('Network.setCookie', { name: 'jkt48_admin_session', value: token, domain: 'localhost', path: '/', secure: false, httpOnly: false });

  const inspect = async label => {
    for (let i = 0; i < 60; i++) {
      const ok = await evaluate("!!document.querySelector('.player-inner-container') && !document.querySelector('.player-inner-container').classList.contains('skeleton-player')");
      if (ok) break;
      await delay(500);
    }
    await delay(800);
    const state = await evaluate(`(() => {
      const outer = document.querySelector('.video-player-wrapper');
      const inner = document.querySelector('.player-inner-container');
      return {
        path: location.pathname + location.search,
        outerClass: outer ? outer.className : null,
        innerClass: inner ? inner.className : null,
        aspect: getComputedStyle(inner).aspectRatio,
        ratioBtn: !!document.querySelector('.ratio-btn'),
        theaterBtn: !!document.querySelector('.theater-btn'),
        mediaHost: !!document.querySelector('[data-media-player]'),
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth,
        // Geometri bilah progres: menjaga agar gaya form dari stylesheet lain
        // (mis. gaya '.admin-shell input' di portal.css) tidak membengkakkannya.
        seek: (() => {
          const s = document.querySelector('.video-seek-slider');
          if (!s) return null;
          const cs = getComputedStyle(s);
          return {
            h: +s.getBoundingClientRect().height.toFixed(1),
            minHeight: cs.minHeight,
            padding: cs.padding,
            border: cs.borderTopWidth,
            radius: cs.borderRadius,
          };
        })(),
      };
    })()`);
    console.log(`\n[${label}]`);
    console.log('  path      :', state.path);
    console.log('  wrapper   :', state.outerClass);
    console.log('  container :', state.innerClass, '| aspect-ratio:', state.aspect);
    console.log('  FIT:', state.ratioBtn, '| Theater:', state.theaterBtn, '| player:', state.mediaHost);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(import.meta.dirname, `trial-${label}.png`), Buffer.from(shot.data, 'base64'));
    return state;
  };

  await send('Page.navigate', { url: `${origin}/admin/trial?platform=showroom&youtube=${VIDEO}&title=Uji%20Showroom%20Landscape` });
  await delay(2500);
  const showroom = await inspect('showroom');
  assert.match(showroom.outerClass, /horizontal-player/, 'Kelas horizontal tidak dipakai');
  assert.match(showroom.innerClass, /horizontal-player-container/, 'Container horizontal tidak dipakai');
  assert.equal(showroom.ratioBtn, false, 'Tombol rasio vertikal seharusnya tidak muncul di landscape');
  assert.ok(showroom.theaterBtn, 'Tombol theater harus tetap ada');
  assert.ok(showroom.mediaHost, 'Pemutar tidak ter-render');
  assert.ok(showroom.scrollWidth <= showroom.innerWidth + 1, 'Layout meluber horizontal');
  console.log('  seek      :', JSON.stringify(showroom.seek));
  assert.ok(showroom.seek, 'Bilah progres tidak ditemukan');
  assert.equal(showroom.seek.h, 4, `Bilah progres harus 4px, dapat ${showroom.seek.h}px (bocor gaya form?)`);
  assert.equal(showroom.seek.minHeight, '0px', 'min-height bilah progres bocor dari gaya form luar');
  assert.equal(showroom.seek.padding, '0px', 'padding bilah progres bocor dari gaya form luar');
  assert.equal(showroom.seek.border, '0px', 'border bilah progres bocor dari gaya form luar');
  console.log('  -> PASS: landscape 16:9, tanpa tombol rasio vertikal');

  // Fullscreen ELEMEN untuk landscape (bukan theater satu halaman).
  await evaluate("document.querySelector('.theater-btn').click()", true);
  await delay(900);
  const fs = await evaluate(`(() => {
    const wrap = document.querySelector('.video-player-wrapper');
    const inner = document.querySelector('.player-inner-container');
    const wr = wrap.getBoundingClientRect();
    const ir = inner.getBoundingClientRect();
    return {
      wrapperIsFullscreen: document.fullscreenElement === wrap,
      wrapperFillsViewport: Math.abs(wr.width - innerWidth) < 2 && Math.abs(wr.height - innerHeight) < 2,
      containerRatio: +(ir.width / ir.height).toFixed(2),
      containerInsideViewport: ir.left >= -1 && ir.top >= -1 && ir.right <= innerWidth + 1 && ir.bottom <= innerHeight + 1,
      buttonActive: document.querySelector('.theater-btn').classList.contains('active'),
      noPageTheater: !document.querySelector('.theater-mode'),
      pageScrollFree: document.body.style.overflow !== 'hidden',
      mediaStillMounted: !!document.querySelector('[data-media-player]'),
      seekHeight: +document.querySelector('.video-seek-slider').getBoundingClientRect().height.toFixed(1),
      seekBorder: getComputedStyle(document.querySelector('.video-seek-slider')).borderTopWidth,
    };
  })()`);
  console.log('\n[showroom fullscreen]');
  console.log(' ', JSON.stringify(fs));
  assert.ok(fs.wrapperIsFullscreen, 'Fullscreen elemen tidak aktif pada wrapper');
  assert.ok(fs.wrapperFillsViewport, 'Wrapper fullscreen tidak mengisi viewport');
  assert.ok(Math.abs(fs.containerRatio - 16 / 9) < 0.05, `Rasio container saat fullscreen harus 16:9, dapat ${fs.containerRatio}`);
  assert.ok(fs.containerInsideViewport, 'Container keluar dari viewport saat fullscreen');
  assert.ok(fs.buttonActive, 'Tombol fullscreen tidak aktif');
  assert.ok(fs.noPageTheater, 'Landscape tidak boleh memakai theater satu halaman');
  assert.ok(fs.pageScrollFree, 'Scroll halaman tidak boleh dikunci pada fullscreen elemen');
  assert.ok(fs.mediaStillMounted, 'Pemutar ter-unmount saat fullscreen');
  assert.equal(fs.seekHeight, 4, `Bilah progres saat fullscreen harus 4px, dapat ${fs.seekHeight}px (bocor gaya form?)`);
  assert.equal(fs.seekBorder, '0px', 'Bilah progres saat fullscreen tidak boleh ber-border');
  const fsShot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(import.meta.dirname, 'trial-showroom-fullscreen.png'), Buffer.from(fsShot.data, 'base64'));
  console.log('  -> PASS: fullscreen elemen 16:9, tanpa theater satu halaman');

  await evaluate('document.exitFullscreen()', true);
  await delay(700);
  const exited = await evaluate("({ none: !document.fullscreenElement, inactive: !document.querySelector('.theater-btn').classList.contains('active') })");
  assert.ok(exited.none, 'Keluar fullscreen gagal');
  assert.ok(exited.inactive, 'Status tombol tidak kembali normal setelah keluar fullscreen');
  console.log('  -> PASS: keluar fullscreen mengembalikan status tombol');

  await send('Page.navigate', { url: `${origin}/admin/trial?platform=idn&youtube=${VIDEO}&title=Uji%20IDN%20Vertikal` });
  await delay(2500);
  const idn = await inspect('idn');
  assert.match(idn.outerClass, /vertical-player/, 'Kelas vertikal tidak dipakai untuk IDN');
  assert.match(idn.innerClass, /vertical-player-container/, 'Container vertikal tidak dipakai');
  assert.equal(idn.ratioBtn, true, 'Tombol rasio seharusnya muncul untuk IDN');
  assert.notEqual(showroom.aspect, idn.aspect, 'Rasio landscape dan vertikal harus berbeda');
  assert.ok(idn.seek, 'Bilah progres tidak ditemukan pada mode vertikal');
  assert.equal(idn.seek.h, 4, `Bilah progres vertikal harus 4px, dapat ${idn.seek.h}px`);
  assert.equal(idn.seek.minHeight, '0px', 'min-height bilah progres bocor pada mode vertikal');
  console.log('  -> PASS: vertikal 9:16 dengan tombol rasio');

  // Pastikan halaman ujicoba tidak bisa diakses tanpa sesi.
  await send('Network.clearBrowserCookies');
  await send('Page.navigate', { url: `${origin}/admin/trial` });
  await delay(2500);
  const guarded = await evaluate('location.pathname');
  assert.equal(guarded, '/login', 'Halaman ujicoba harus dialihkan ke login tanpa sesi');
  console.log('\n  -> PASS: tanpa sesi dialihkan ke', guarded);

  console.log(`\nPASS: ujicoba pemutar; landscape (${showroom.aspect}) berbeda dari vertikal (${idn.aspect}), akses terproteksi.`);
} finally {
  if (ws) ws.close();
  if (target) await fetch('http://localhost:9222/json/close/' + target.id);
  server.kill();
  await new Promise(r => server.once('exit', r));
  rmSync(temp, { recursive: true, force: true });
}