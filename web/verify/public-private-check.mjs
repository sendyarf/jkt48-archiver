import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
const root = resolve(import.meta.dirname, '..');
const temp = mkdtempSync(join(tmpdir(), 'jkt48-portal-'));
const db = new DatabaseSync(join(temp, 'fixture.db'));
db.exec(`CREATE TABLE member_hls (username TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER, hls_confirmed INTEGER, last_live_at TEXT);
CREATE TABLE merge_groups (id INTEGER PRIMARY KEY, live_title TEXT, live_key TEXT);
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, live_id TEXT, member_username TEXT, member_name TEXT, merge_group_id INTEGER, started_at TEXT, created_at TEXT, download_ended_at TEXT, download_started_at TEXT, platform TEXT, youtube_video_id TEXT, status TEXT, file_size_bytes INTEGER);
INSERT INTO member_hls VALUES ('jkt48_test', 'Test Member', 1, 1, NULL);
INSERT INTO merge_groups VALUES (1, 'UNUSED_GROUP_TITLE_ONE', 'INTERNAL_KEY_SENTINEL'), (2, 'UNUSED_GROUP_TITLE_TWO', 'PRIVATE_KEY_SENTINEL'), (3, 'UNUSED_GROUP_TITLE_THREE', 'SHOWROOM_KEY');
INSERT INTO live_sessions VALUES (1, 'private-live-one', 'jkt48_test', 'Test Member', 1, '2026-09-01', '2026-09-01', '2026-09-01 12:00:00', '2026-09-01 11:00:00', 'idn', 'R8pnx79dyDQ', 'done_youtube', 9000), (2, 'private-live-two', 'jkt48_test', 'Test Member', 2, '2026-09-02', '2026-09-02', '2026-09-02 12:00:00', '2026-09-02 11:00:00', 'idn', 'abcdefghijk', 'done_youtube', 9000), (3, 'sr_JKT48_Test_1', 'jkt48_test', 'Test Member', 3, '2026-09-03', '2026-09-03', '2026-09-03 12:00:00', '2026-09-03 11:00:00', 'showroom', 'lmnopqrstuv', 'done_youtube', 9000);`);
const origin = 'http://localhost:3107';
const secret = randomBytes(32).toString('hex');
// AUTO_PUBLISH_AFTER_HOURS=0 → gerbang manual IDN diuji secara deterministik,
// tanpa bergantung jarak waktu antara data uji dan jam berjalan.
// Aturan 72 jam diuji terpisah di verify/auto-publish-check.mjs.
// Catatan: baris SHOWROOM di fixture langsung tampil (ambang Showroom = 0),
// sehingga /api/members tidak lagi kosong sejak awal.
const child = spawn(process.execPath, [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', '3107'], { cwd: root, env: { ...process.env, NODE_ENV: 'production', ADMIN_SECRET: secret, APP_ORIGIN: origin, DB_PATH: join(temp, 'fixture.db'), AUTO_PUBLISH_AFTER_HOURS: '0', TURNSTILE_SITE_KEY: 'off', TURNSTILE_SECRET_KEY: 'off' }, stdio: ['ignore', 'pipe', 'pipe'] });
let logs = ''; child.stdout.on('data', d => logs += d); child.stderr.on('data', d => logs += d);
const call = (route, options = {}) => fetch(origin + route, { redirect: 'manual', ...options });
const post = (route, body, cookie = '', requestOrigin = origin) => call(route, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: requestOrigin, Cookie: cookie }, body: JSON.stringify(body) });
/** Cermin encodeWatchId() di web/lib/codec.ts — ID tersamar untuk URL /watch. */
const mask = (id) => Buffer.from(id, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
try {
  let ready = false;
  for (let i = 0; i < 80; i++) { try { if ((await call('/api/auth')).ok) { ready = true; break; } } catch {} await new Promise(r => setTimeout(r, 250)); }
  assert(ready, logs);
  for (const route of ['/api/admin/members', '/api/admin/publications']) {
    assert.equal((await call(route)).status, 401);
    assert.equal((await post(route, {})).status, 401);
  }
  const adminStatus = await call('/admin/status');
  assert.equal(adminStatus.status, 404, '/admin/status tanpa sesi harus 404');
  assert.equal(adminStatus.headers.get('location'), null, '/admin/status tanpa sesi tidak boleh redirect ke login');
  const statusRedirect = await call('/status');
  assert.equal(statusRedirect.status, 308, '/status harus redirect permanen 308');
  assert.equal(statusRedirect.headers.get('location'), '/admin/status');
  // IDN belum diterbitkan (auto=0): watch tetap 200 sebagai panel "segera hadir"
  // (tanpa pemutar) — konsisten dengan verify/auto-publish-check.mjs.
  // Pakai ID tersamar; raw ID memicu permanentRedirect() → 308 ke bentuk tersamar.
  const prereleaseWatch = await call(`/watch/${mask('R8pnx79dyDQ')}`);
  assert.equal(prereleaseWatch.status, 200);
  const prereleaseHtml = await prereleaseWatch.text();
  assert.match(prereleaseHtml, /Replay segera hadir|segera hadir/, 'Watch IDN belum terbit harus panel segera hadir');
  assert.doesNotMatch(prereleaseHtml, /data-media-player/, 'Watch IDN belum terbit tidak boleh punya pemutar');
  // Hanya rekaman Showroom yang tampil otomatis; IDN masih tersembunyi.
  // avatar_url selalu ikut DTO publik (null bila member belum punya foto roster).
  assert.deepEqual((await (await call('/api/members')).json()).members, [
    { username: 'jkt48_test', display_name: 'Test Member', video_count: 1, avatar_url: null },
  ]);
  assert.equal((await post('/api/auth', { secret }, '', 'https://untrusted.invalid')).status, 403);
  assert.equal((await post('/api/auth', { secret: 'incorrect' })).status, 401);
  const login = await post('/api/auth', { secret }); assert.equal(login.status, 200);
  const setCookie = login.headers.get('set-cookie'); assert.match(setCookie, /HttpOnly/i); assert.match(setCookie, /Secure/i); assert.match(setCookie, /SameSite=strict/i);
  const cookie = setCookie.split(';')[0];
  assert.equal((await call('/api/admin/members', { headers: { Cookie: cookie } })).status, 200);
  assert.equal((await post('/api/admin/members', { action: 'toggle', username: 'jkt48_test', enabled: 'false' }, cookie)).status, 400);
  assert.equal((await post('/api/admin/publications', { youtube_video_id: 'R8pnx79dyDQ', published: true }, cookie)).status, 200);
  const html = await (await call('/')).text(); assert.match(html, /LIVE IDN TEST MEMBER/); assert.doesNotMatch(html, /UNUSED_GROUP_TITLE_TWO|INTERNAL_KEY_SENTINEL|private-live-one|done_youtube|Bot Standby|Status Bot/);
  assert.equal((await call(`/watch/${mask('R8pnx79dyDQ')}`)).status, 200);
  const unpublishedWatch = await call(`/watch/${mask('abcdefghijk')}`);
  assert.equal(unpublishedWatch.status, 200, 'IDN belum terbit tetap 200 (panel segera hadir)');
  const unpublishedHtml = await unpublishedWatch.text();
  assert.match(unpublishedHtml, /Replay segera hadir|segera hadir/, 'IDN belum terbit harus panel segera hadir');
  assert.doesNotMatch(unpublishedHtml, /data-media-player/, 'IDN belum terbit tidak boleh punya pemutar');
  const members = (await (await call('/api/members')).json()).members;
  assert.deepEqual(Object.keys(members[0]).sort(), ['avatar_url', 'display_name', 'username', 'video_count']); assert.equal(members[0].video_count, 2);
  assert.doesNotMatch(await (await call('/?platform=showroom')).text(), /LIVE IDN TEST MEMBER/);
  assert.equal((await call('/?page=invalid')).status, 200);
  // Filter platform: rekaman Showroom hanya muncul di filternya sendiri, dan
  // sebaliknya rekaman IDN tidak bocor ke filter Showroom.
  assert.equal((await post('/api/admin/publications', { youtube_video_id: 'lmnopqrstuv', published: true }, cookie)).status, 200);
  const showroomHtml = await (await call('/?platform=showroom')).text();
  assert.match(showroomHtml, /LIVE SHOWROOM TEST MEMBER/);
  assert.doesNotMatch(showroomHtml, /LIVE IDN TEST MEMBER/);
  const idnHtml = await (await call('/?platform=idn')).text();
  assert.match(idnHtml, /LIVE IDN TEST MEMBER/);
  assert.doesNotMatch(idnHtml, /LIVE SHOWROOM TEST MEMBER/);
  assert.equal((await call(`/watch/${mask('lmnopqrstuv')}`)).status, 200);
  assert.equal((await (await call('/api/members')).json()).members[0].video_count, 2);
  // Video milik channel yang TIDAK terkait JKT48 tidak boleh bisa masuk arsip.
  // ID yang tidak ada di live_sessions harus tidak punya pemutar/countdown.
  const unrelatedId = 'zzzzzzzzzzz';
  const unrelatedHtml = await (await call(`/watch/${mask(unrelatedId)}`)).text();
  assert.doesNotMatch(unrelatedHtml, /data-media-player/, 'ID asing tidak boleh punya pemutar');
  assert.doesNotMatch(unrelatedHtml, /countdown-panel/, 'ID asing tidak boleh punya countdown');
  assert.equal((await post('/api/admin/publications', { youtube_video_id: unrelatedId, published: true }, cookie)).status, 404);
  assert.doesNotMatch(await (await call('/')).text(), new RegExp(unrelatedId));
  assert.equal((await post('/api/admin/publications', { youtube_video_id: 'R8pnx79dyDQ', published: false }, cookie)).status, 200);
  const withheldHtml = await (await call(`/watch/${mask('R8pnx79dyDQ')}`)).text();
  assert.doesNotMatch(withheldHtml, /data-media-player/, 'Ditahan admin → tanpa pemutar');
  assert.doesNotMatch(withheldHtml, /countdown-panel/, 'Ditahan admin → tanpa countdown');
  assert.doesNotMatch(await (await call('/')).text(), /LIVE IDN TEST MEMBER/);
  assert.equal((await call('/api/auth', { method: 'DELETE', headers: { Cookie: cookie, Origin: origin } })).status, 200);
  assert.equal((await call('/api/admin/members', { headers: { Cookie: cookie } })).status, 401);
  console.log('PASS: private defaults (auto-publish IDN off), Showroom langsung publik, public DTO, direct watch/metadata gate, publication/revocation, filters, admin auth, origin checks, cookie flags, logout.');
} finally {
  if (child.exitCode === null && child.signalCode === null) {
    child.kill();
    await new Promise(r => child.once('exit', r));
  }
  db.close();
  rmSync(temp, { recursive: true, force: true });
}
