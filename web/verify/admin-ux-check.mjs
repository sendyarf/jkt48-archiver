import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const temp = mkdtempSync(join(tmpdir(), 'jkt48-admin-ux-'));
const db = new DatabaseSync(join(temp, 'fixture.db'));
db.exec(`CREATE TABLE member_hls (username TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER, hls_confirmed INTEGER, last_live_at TEXT);
CREATE TABLE merge_groups (id INTEGER PRIMARY KEY, live_title TEXT, live_key TEXT);
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, live_id TEXT, member_username TEXT, member_name TEXT, merge_group_id INTEGER, started_at TEXT, created_at TEXT, download_ended_at TEXT, download_started_at TEXT, platform TEXT, youtube_video_id TEXT, status TEXT, file_size_bytes INTEGER, content_uid TEXT, telegram_message_ids TEXT, error_message TEXT);
CREATE TABLE web_publications (youtube_video_id TEXT PRIMARY KEY, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE web_video_overrides (content_key TEXT PRIMARY KEY, published INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')));
INSERT INTO member_hls VALUES ('jkt48_uji', 'Uji Member', 1, 1, NULL);
INSERT INTO merge_groups VALUES (1, 'Judul Live Uji', 'k1'), (2, 'Judul Live Dua', 'k2'), (3, 'Judul Live Tiga', 'k3');
INSERT INTO live_sessions (id, live_id, member_username, member_name, merge_group_id, started_at, created_at, download_ended_at, platform, youtube_video_id, status, file_size_bytes) VALUES
 (1, 's1', 'jkt48_uji', 'Uji Member', 1, datetime('now','-5 hours'), datetime('now','-5 hours'), datetime('now','-4 hours'), 'idn', 'AAAAAAAAAAA', 'done_youtube', 1),
 (2, 's2', 'jkt48_uji', 'Uji Member', 2, datetime('now','-100 hours'), datetime('now','-100 hours'), datetime('now','-99 hours'), 'idn', 'BBBBBBBBBBB', 'done_youtube', 1),
 (3, 's3', 'jkt48_uji', 'Uji Member', 3, datetime('now','-100 hours'), datetime('now','-100 hours'), datetime('now','-99 hours'), 'showroom', 'CCCCCCCCCCC', 'done_youtube', 1);
INSERT INTO web_publications (youtube_video_id, published) VALUES ('CCCCCCCCCCC', 0);`);

const origin = 'http://localhost:3116';
const secret = randomBytes(32).toString('hex');
const child = spawn(process.execPath, [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', '3116'], {
  cwd: root,
  env: { ...process.env, NODE_ENV: 'production', ADMIN_SECRET: secret, APP_ORIGIN: origin, DB_PATH: join(temp, 'fixture.db'), AUTO_PUBLISH_AFTER_HOURS: '72', AUTO_PUBLISH_AFTER_HOURS_SHOWROOM: '0' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = ''; child.stdout.on('data', d => logs += d); child.stderr.on('data', d => logs += d);
const call = (route, options = {}) => fetch(origin + route, { redirect: 'manual', ...options });
const post = (route, body, cookie = '') => call(route, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin, Cookie: cookie }, body: JSON.stringify(body) });

try {
  let ready = false;
  for (let i = 0; i < 80; i++) {
    try { if ((await call('/api/auth')).ok) { ready = true; break; } } catch {}
    await new Promise(r => setTimeout(r, 250));
  }
  assert(ready, logs);

  assert.equal((await call('/api/admin/publications')).status, 401);
  assert.equal((await call('/admin/publications')).headers.get('location'), '/login');

  const login = await post('/api/auth', { secret });
  assert.equal(login.status, 200);
  const cookie = login.headers.get('set-cookie').split(';')[0];

  const all = await (await call('/api/admin/publications?status=all', { headers: { Cookie: cookie } })).json();
  assert.equal(all.success, true);
  assert.equal(all.videos.length, 3, `all videos: ${JSON.stringify(all.videos.map(v => v.youtube_video_id))}`);
  assert.deepEqual(
    { total: all.summary.total, waiting: all.summary.waiting, visible: all.summary.visible, held: all.summary.held },
    { total: 3, waiting: 1, visible: 1, held: 1 },
    `summary all: ${JSON.stringify(all.summary)}`,
  );

  const waiting = await (await call('/api/admin/publications?status=waiting', { headers: { Cookie: cookie } })).json();
  assert.equal(waiting.videos.length, 1);
  assert.equal(waiting.videos[0].youtube_video_id, 'AAAAAAAAAAA', 'waiting = IDN recent tanpa keputusan');

  const visible = await (await call('/api/admin/publications?status=visible', { headers: { Cookie: cookie } })).json();
  assert.equal(visible.videos.length, 1);
  assert.equal(visible.videos[0].youtube_video_id, 'BBBBBBBBBBB', 'visible = IDN lewat 72 jam');

  const held = await (await call('/api/admin/publications?status=held', { headers: { Cookie: cookie } })).json();
  assert.equal(held.videos.length, 1);
  assert.equal(held.videos[0].youtube_video_id, 'CCCCCCCCCCC', 'held = showroom ditahan manual');

  // Ringkasan tidak boleh berubah ikut filter.
  const heldAgain = await (await call('/api/admin/publications?status=held', { headers: { Cookie: cookie } })).json();
  assert.deepEqual(heldAgain.summary, all.summary);

  // Tahan replay menunggu → pindah ke Ditahan, lepas tahan → kembali Menunggu.
  const hold = await post('/api/admin/publications', { youtube_video_id: 'AAAAAAAAAAA', published: false }, cookie);
  assert.equal(hold.status, 200, `hold: ${hold.status} ${await hold.text()}`);
  const afterHold = await (await call('/api/admin/publications?status=all', { headers: { Cookie: cookie } })).json();
  assert.deepEqual(
    { waiting: afterHold.summary.waiting, held: afterHold.summary.held },
    { waiting: 0, held: 2 },
    `after hold summary: ${JSON.stringify(afterHold.summary)}`,
  );
  const reset = await post('/api/admin/publications', { youtube_video_id: 'AAAAAAAAAAA', action: 'reset' }, cookie);
  assert.equal(reset.status, 200, `reset: ${reset.status} ${await reset.text()}`);
  const afterReset = await (await call('/api/admin/publications?status=all', { headers: { Cookie: cookie } })).json();
  assert.deepEqual(
    { waiting: afterReset.summary.waiting, held: afterReset.summary.held },
    { waiting: 1, held: 1 },
    `after reset summary: ${JSON.stringify(afterReset.summary)}`,
  );

  // Halaman admin menyertakan chip filter & nav baru.
  const adminHtml = await (await call('/admin/publications', { headers: { Cookie: cookie } })).text();
  assert.match(adminHtml, /Admin Studio/);
  assert.match(adminHtml, /Publikasi/);

  console.log('admin-ux-check OK');
} finally {
  // Tunggu proses benar-benar keluar dulu — kalau tidak, Windows menolak
  // hapus fixture.db (handle masih dipegang) dan rmSync melempar EPERM.
  await new Promise((resolveExit) => {
    const timer = setTimeout(resolveExit, 2000);
    child.once('exit', () => {
      clearTimeout(timer);
      resolveExit(undefined);
    });
    try { child.kill(); } catch { /* sudah mati */ }
  });
  // Windows sering menolak hapus temp (handle SQLite/AV) — jangan gagalkan uji.
  try { rmSync(temp, { recursive: true, force: true }); } catch { /* best-effort */ }
}
