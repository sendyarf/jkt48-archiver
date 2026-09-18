/**
 * auto-publish-check.mjs — memverifikasi aturan rilis otomatis 72 jam.
 *
 * Aturan yang diuji (lihat web/lib/db.ts → publicVisibilitySql):
 *   1. Tanpa keputusan admin + sudah lewat ambang  -> tampil PUBLIK
 *   2. Tanpa keputusan admin + belum lewat ambang  -> TERSEMBUNYI
 *   3. Admin menahan eksplisit                     -> TERSEMBUNYI (menang)
 *   4. Admin menerbitkan eksplisit                 -> PUBLIK (menang, lebih cepat)
 *   5. AUTO_PUBLISH_AFTER_HOURS=0                  -> semua wajib manual
 *
 * Umur data dihitung relatif terhadap jam berjalan memakai datetime('now'),
 * jadi uji ini tidak bergantung pada tanggal saat dijalankan.
 */
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const temp = mkdtempSync(join(tmpdir(), 'jkt48-auto-'));
const dbPath = join(temp, 'fixture.db');
const db = new DatabaseSync(dbPath);

const OLD_ID = 'AAAAAAAAAAA';
const RECENT_ID = 'BBBBBBBBBBB';
const WITHHELD_ID = 'CCCCCCCCCCC';
const FAST_ID = 'DDDDDDDDDDD';

db.exec(`CREATE TABLE member_hls (username TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER, hls_confirmed INTEGER, last_live_at TEXT);
CREATE TABLE merge_groups (id INTEGER PRIMARY KEY, live_title TEXT, live_key TEXT);
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, live_id TEXT, member_username TEXT, member_name TEXT, merge_group_id INTEGER, started_at TEXT, created_at TEXT, download_ended_at TEXT, youtube_video_id TEXT, status TEXT, file_size_bytes INTEGER);
CREATE TABLE web_publications (youtube_video_id TEXT PRIMARY KEY, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')));
INSERT INTO member_hls VALUES ('jkt48_uji', 'Uji Member', 1, 1, NULL);
INSERT INTO merge_groups VALUES (1, 'UNUSED_GROUP_TITLE_OLD', 'k1'), (2, 'UNUSED_GROUP_TITLE_RECENT', 'k2'), (3, 'UNUSED_GROUP_TITLE_WITHHELD', 'k3'), (4, 'UNUSED_GROUP_TITLE_FAST', 'k4');
INSERT INTO live_sessions VALUES (1, 'auto-old', 'jkt48_uji', 'Uji Member', 1, datetime('now','-100 hours'), datetime('now','-100 hours'), datetime('now','-99 hours'), 'AAAAAAAAAAA', 'done_youtube', 1);
INSERT INTO live_sessions VALUES (2, 'auto-recent', 'jkt48_uji', 'Uji Member', 2, datetime('now','-10 hours'), datetime('now','-10 hours'), datetime('now','-9 hours'), 'BBBBBBBBBBB', 'done_youtube', 1);
INSERT INTO live_sessions VALUES (3, 'auto-withheld', 'jkt48_uji', 'Uji Member', 3, datetime('now','-100 hours'), datetime('now','-100 hours'), datetime('now','-99 hours'), 'CCCCCCCCCCC', 'done_youtube', 1);
INSERT INTO live_sessions VALUES (4, 'auto-fast', 'jkt48_uji', 'Uji Member', 4, datetime('now','-10 hours'), datetime('now','-10 hours'), datetime('now','-9 hours'), 'DDDDDDDDDDD', 'done_youtube', 1);
INSERT INTO web_publications (youtube_video_id, published) VALUES ('CCCCCCCCCCC', 0), ('DDDDDDDDDDD', 1);`);

const secret = randomBytes(32).toString('hex');
const servers = [];
// Port khusus: 3107 = public-private-check, 3108 = player-check,
// 3110 = player-trial-check. JANGAN pakai port yang sama agar uji tidak
// diam-diam menempel ke server milik skrip lain.
const PORT_AUTO_ON = 3111;
const PORT_AUTO_OFF = 3112;

function startServer(port, autoHours) {
  const origin = `http://localhost:${port}`;
  const child = spawn(process.execPath, [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', String(port)], {
    cwd: root,
    env: { ...process.env, NODE_ENV: 'production', ADMIN_SECRET: secret, APP_ORIGIN: origin, DB_PATH: dbPath, AUTO_PUBLISH_AFTER_HOURS: String(autoHours) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  child.stdout.on('data', d => logs += d);
  child.stderr.on('data', d => logs += d);
  const server = { child, origin, logs: () => logs, port };
  servers.push(server);
  return server;
}

async function waitReady(server) {
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(server.origin + '/api/auth');
      if (res.ok) return;
    } catch { /* belum siap */ }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`Server :${server.port} tidak siap.\n${server.logs()}`);
}

/**
 * Penjaga anti-tabrakan port: pastikan server yang menjawab benar-benar
 * memakai database fixture ini. Tanpa ini, port yang sudah dipakai skrip lain
 * akan membuat uji menilai aplikasi yang salah dan hasilnya menyesatkan.
 */
async function assertOwnFixture(server) {
  const html = await (await fetch(server.origin + '/')).text();
  if (!html.includes('Uji Member')) {
    throw new Error(`Server :${server.port} tidak memakai database fixture (port mungkin dipakai proses lain).\n${server.logs()}`);
  }
}

const post = (server, route, body, cookie) => fetch(server.origin + route, {
  method: 'POST',
  redirect: 'manual',
  headers: { 'Content-Type': 'application/json', Origin: server.origin, Cookie: cookie ?? '' },
  body: JSON.stringify(body),
});
try {
  // ── Bagian 1: AUTO_PUBLISH_AFTER_HOURS = 72 ────────────────────────────
  const s72 = startServer(PORT_AUTO_ON, 72);
  await waitReady(s72);
  await assertOwnFixture(s72);

  const html = await (await fetch(s72.origin + '/')).text();
  assert.match(html, new RegExp(OLD_ID), 'Rekaman lewat 72 jam harus tampil otomatis');
  assert.match(html, new RegExp(FAST_ID), 'Rekaman yang diterbitkan manual harus tampil');
  assert.doesNotMatch(html, new RegExp(RECENT_ID), 'Rekaman yang belum lewat 72 jam harus tersembunyi');
  assert.doesNotMatch(html, new RegExp(WITHHELD_ID), 'Rekaman yang ditahan admin harus tetap tersembunyi');
  // Judul tampilan memakai format seragam, bukan judul bebas dari database.
  assert.match(html, /LIVE IDN UJI MEMBER/);

  assert.equal((await fetch(s72.origin + '/watch/AAAAAAAAAAA')).status, 200, 'watch rekaman otomatis harus 200');
  assert.equal((await fetch(s72.origin + '/watch/DDDDDDDDDDD')).status, 200, 'watch rekaman manual harus 200');
  assert.equal((await fetch(s72.origin + '/watch/BBBBBBBBBBB')).status, 404, 'watch rekaman belum rilis harus 404');
  assert.equal((await fetch(s72.origin + '/watch/CCCCCCCCCCC')).status, 404, 'watch rekaman ditahan harus 404');

  const members = (await (await fetch(s72.origin + '/api/members')).json()).members;
  assert.equal(members.length, 1);
  assert.equal(members[0].video_count, 2, 'Jumlah arsip publik hanya menghitung rekaman yang tampil');

  // Admin harus melihat keadaan yang membedakan otomatis vs manual.
  const login = await post(s72, '/api/auth', { secret });
  assert.equal(login.status, 200);
  const cookie = login.headers.get('set-cookie').split(';')[0];
  const admin = await (await fetch(s72.origin + '/api/admin/publications', { headers: { Cookie: cookie } })).json();
  assert.equal(admin.autoPublishAfterHours, 72, 'API admin harus melaporkan ambang 72 jam');
  const byId = Object.fromEntries(admin.videos.map(v => [v.youtube_video_id, v]));
  assert.equal(Number(byId['AAAAAAAAAAA'].visible), 1, 'LAMA_OTOMATIS harus terlihat');
  assert.equal(Number(byId['AAAAAAAAAAA'].decided), 0, 'LAMA_OTOMATIS belum diputuskan admin');
  assert.equal(Number(byId['BBBBBBBBBBB'].visible), 0, 'BARU_BELUM_RILIS belum terlihat');
  assert.equal(Number(byId['CCCCCCCCCCC'].visible), 0, 'LAMA_DITAHAN_ADMIN tetap tersembunyi');
  assert.equal(Number(byId['CCCCCCCCCCC'].decided), 1, 'LAMA_DITAHAN_ADMIN sudah diputuskan admin');
  assert.equal(Number(byId['DDDDDDDDDDD'].visible), 1, 'BARU_TERBIT_MANUAL terlihat');
  assert.ok(Number(byId['AAAAAAAAAAA'].hours_since_end) >= 99, 'Umur rekaman lama harus terbaca');
  assert.ok(Number(byId['BBBBBBBBBBB'].hours_since_end) < 72, 'Umur rekaman baru harus di bawah ambang');

  // Admin menahan rekaman yang tadinya otomatis → harus hilang dari publik.
  assert.equal((await post(s72, '/api/admin/publications', { youtube_video_id: 'AAAAAAAAAAA', published: false }, cookie)).status, 200);
  const afterWithhold = await (await fetch(s72.origin + '/')).text();
  assert.doesNotMatch(afterWithhold, new RegExp(OLD_ID), 'Setelah ditahan admin, rekaman otomatis harus hilang');
  assert.match(afterWithhold, new RegExp(FAST_ID), 'Rekaman manual lain tidak boleh terpengaruh');

  // ── Bagian 2: AUTO_PUBLISH_AFTER_HOURS = 0 (wajib manual) ───────────────
  const s0 = startServer(PORT_AUTO_OFF, 0);
  await waitReady(s0);
  await assertOwnFixture(s0);
  const html0 = await (await fetch(s0.origin + '/')).text();
  assert.doesNotMatch(html0, new RegExp(OLD_ID), 'Dengan auto=0, rekaman lama harus tetap tersembunyi');
  assert.match(html0, new RegExp(FAST_ID), 'Publikasi manual tetap berlaku saat auto=0');
  assert.equal((await fetch(s0.origin + '/watch/AAAAAAAAAAA')).status, 404, 'Dengan auto=0, watch rekaman lama harus 404');

  console.log('PASS: rilis otomatis 72 jam, keputusan admin menang atas otomatis, ambang 0 mematikan rilis otomatis.');
} finally {
  for (const server of servers) {
    if (server.child.exitCode === null && server.child.signalCode === null) {
      server.child.kill();
      await new Promise(r => server.child.once('exit', r));
    }
  }
  db.close();
  rmSync(temp, { recursive: true, force: true });
}
