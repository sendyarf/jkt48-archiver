/**
 * auto-publish-check.mjs — memverifikasi aturan rilis otomatis 72 jam.
 *
 * Aturan yang diuji (lihat web/lib/db.ts → publicVisibilitySql):
 *   1. Tanpa keputusan admin + sudah lewat ambang  -> tampil PUBLIK
 *   2. Tanpa keputusan admin + belum lewat ambang  -> PRA-RILIS: tetap muncul di
 *      grid home sebagai kartu "Segera" + halaman countdown di /watch (bukan
 *      hilang dari situs, bukan pula bisa ditonton)
 *   3. Admin menahan eksplisit                     -> TERSEMBUNYI (menang)
 *   4. Admin menerbitkan eksplisit                 -> PUBLIK (menang, lebih cepat)
 *   5. AUTO_PUBLISH_AFTER_HOURS=0                  -> IDN wajib manual
 *   6. Rekaman SHOWROOM                            -> langsung PUBLIK tanpa
 *      menunggu ambang (default 0), tetap tunduk keputusan admin
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
const SHOWROOM_ID = 'SSSSSSSSSSS';
/** Cermin encodeWatchId() di web/lib/codec.ts — ID tersamar untuk URL /watch. */
const maskWatchId = (id) => Buffer.from(id, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
/** RegExp link /watch tersamar untuk sebuah YouTube ID. */
const watchLink = (id) => new RegExp(`/watch/${maskWatchId(id)}`);

db.exec(`CREATE TABLE member_hls (username TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER, hls_confirmed INTEGER, last_live_at TEXT);
CREATE TABLE merge_groups (id INTEGER PRIMARY KEY, live_title TEXT, live_key TEXT);
CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, live_id TEXT, member_username TEXT, member_name TEXT, merge_group_id INTEGER, started_at TEXT, created_at TEXT, download_ended_at TEXT, youtube_video_id TEXT, status TEXT, file_size_bytes INTEGER, platform TEXT NOT NULL DEFAULT 'idn', download_started_at TEXT, telegram_message_ids TEXT);
CREATE TABLE web_publications (youtube_video_id TEXT PRIMARY KEY, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')));
INSERT INTO member_hls VALUES ('jkt48_uji', 'Uji Member', 1, 1, NULL);
INSERT INTO merge_groups VALUES (1, 'UNUSED_GROUP_TITLE_OLD', 'k1'), (2, 'UNUSED_GROUP_TITLE_RECENT', 'k2'), (3, 'UNUSED_GROUP_TITLE_WITHHELD', 'k3'), (4, 'UNUSED_GROUP_TITLE_FAST', 'k4');
INSERT INTO live_sessions (id, live_id, member_username, member_name, merge_group_id, started_at, created_at, download_ended_at, youtube_video_id, status, file_size_bytes, platform) VALUES (1, 'auto-old', 'jkt48_uji', 'Uji Member', 1, datetime('now','-100 hours'), datetime('now','-100 hours'), datetime('now','-99 hours'), 'AAAAAAAAAAA', 'done_youtube', 1, 'idn');
INSERT INTO live_sessions (id, live_id, member_username, member_name, merge_group_id, started_at, created_at, download_ended_at, youtube_video_id, status, file_size_bytes, platform) VALUES (2, 'auto-recent', 'jkt48_uji', 'Uji Member', 2, datetime('now','-10 hours'), datetime('now','-10 hours'), datetime('now','-9 hours'), 'BBBBBBBBBBB', 'done_youtube', 1, 'idn');
INSERT INTO live_sessions (id, live_id, member_username, member_name, merge_group_id, started_at, created_at, download_ended_at, youtube_video_id, status, file_size_bytes, platform) VALUES (3, 'auto-withheld', 'jkt48_uji', 'Uji Member', 3, datetime('now','-100 hours'), datetime('now','-100 hours'), datetime('now','-99 hours'), 'CCCCCCCCCCC', 'done_youtube', 1, 'idn');
INSERT INTO live_sessions (id, live_id, member_username, member_name, merge_group_id, started_at, created_at, download_ended_at, youtube_video_id, status, file_size_bytes, platform) VALUES (4, 'auto-fast', 'jkt48_uji', 'Uji Member', 4, datetime('now','-10 hours'), datetime('now','-10 hours'), datetime('now','-9 hours'), 'DDDDDDDDDDD', 'done_youtube', 1, 'idn');
INSERT INTO live_sessions (id, live_id, member_username, member_name, merge_group_id, started_at, created_at, download_ended_at, youtube_video_id, status, file_size_bytes, platform) VALUES (5, 'sr-showroom-now', 'jkt48_uji', 'Uji Member', 1, datetime('now','-4 hours'), datetime('now','-4 hours'), datetime('now','-3 hours'), 'SSSSSSSSSSS', 'done_youtube', 1, 'showroom');
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
  assert.match(html, watchLink(OLD_ID), 'Rekaman lewat 72 jam harus tampil otomatis');
  assert.match(html, watchLink(FAST_ID), 'Rekaman yang diterbitkan manual harus tampil');
  assert.match(html, watchLink(SHOWROOM_ID), 'Rekaman Showroom harus langsung tampil tanpa menunggu 72 jam');
  // Pra-rilis tetap ada di grid home sebagai kartu "Segera" (belum bisa ditonton).
  assert.match(html, watchLink(RECENT_ID), 'Rekaman pra-rilis harus muncul di grid home sebagai kartu Segera');
  assert.match(html, /class="upcoming-badge"/, 'Kartu pra-rilis harus memakai badge Segera');
  // Rekaman yang ditahan admin hilang sepenuhnya dari grid.
  assert.doesNotMatch(html, watchLink(WITHHELD_ID), 'Rekaman yang ditahan admin harus tetap tersembunyi');
  // Link publik selalu tersamar — YouTube ID mentah tidak boleh jadi URL /watch.
  for (const id of [OLD_ID, RECENT_ID, FAST_ID, SHOWROOM_ID]) {
    assert.doesNotMatch(html, new RegExp(`/watch/${id}`), `Link /watch ${id} harus tersamar, bukan ID mentah`);
  }
  // Judul tampilan memakai format seragam, bukan judul bebas dari database.
  assert.match(html, /LIVE IDN UJI MEMBER/);
  assert.match(html, /LIVE SHOWROOM UJI MEMBER/);

  assert.equal((await fetch(s72.origin + `/watch/${maskWatchId(OLD_ID)}`)).status, 200, 'watch rekaman otomatis harus 200');
  assert.equal((await fetch(s72.origin + `/watch/${maskWatchId(FAST_ID)}`)).status, 200, 'watch rekaman manual harus 200');
  assert.equal((await fetch(s72.origin + `/watch/${maskWatchId(SHOWROOM_ID)}`)).status, 200, 'watch rekaman Showroom harus langsung 200');
  // Pra-rilis boleh dibuka, tetapi hanya menampilkan panel countdown.
  const prerelease = await fetch(s72.origin + `/watch/${maskWatchId(RECENT_ID)}`);
  assert.equal(prerelease.status, 200, 'watch rekaman pra-rilis harus 200 (countdown)');
  assert.match(await prerelease.text(), /Replay segera hadir/, 'Rekaman pra-rilis harus menampilkan panel countdown');
  // Rute dinamis yang notFound() dilaporkan 200 (konten halaman 404-nya benar) oleh
  // Next 16.3.5, jadi yang dinilai: rekaman ditahan tidak boleh bisa ditonton.
  const withheldPage = await (await fetch(s72.origin + `/watch/${maskWatchId(WITHHELD_ID)}`)).text();
  assert.doesNotMatch(withheldPage, /video-player-container/, 'Rekaman yang ditahan tidak boleh punya pemutar');
  assert.doesNotMatch(withheldPage, /countdown-panel/, 'Rekaman yang ditahan tidak boleh punya panel countdown');

  const members = (await (await fetch(s72.origin + '/api/members')).json()).members;
  assert.equal(members.length, 1);
  assert.equal(members[0].video_count, 3, 'Jumlah arsip publik hanya menghitung rekaman yang tampil');

  // Admin harus melihat keadaan yang membedakan otomatis vs manual.
  const login = await post(s72, '/api/auth', { secret });
  assert.equal(login.status, 200);
  const cookie = login.headers.get('set-cookie').split(';')[0];
  const admin = await (await fetch(s72.origin + '/api/admin/publications', { headers: { Cookie: cookie } })).json();
  assert.equal(admin.autoPublishAfterHours, 72, 'API admin harus melaporkan ambang 72 jam');
  assert.equal(admin.autoPublishAfterHoursShowroom, 0, 'API admin harus melaporkan ambang Showroom = langsung');
  const byId = Object.fromEntries(admin.videos.map(v => [v.youtube_video_id, v]));
  assert.equal(Number(byId['AAAAAAAAAAA'].visible), 1, 'LAMA_OTOMATIS harus terlihat');
  assert.equal(Number(byId['AAAAAAAAAAA'].decided), 0, 'LAMA_OTOMATIS belum diputuskan admin');
  assert.equal(Number(byId['BBBBBBBBBBB'].visible), 0, 'BARU_BELUM_RILIS belum terlihat');
  assert.equal(Number(byId['CCCCCCCCCCC'].visible), 0, 'LAMA_DITAHAN_ADMIN tetap tersembunyi');
  assert.equal(Number(byId['CCCCCCCCCCC'].decided), 1, 'LAMA_DITAHAN_ADMIN sudah diputuskan admin');
  assert.equal(Number(byId['DDDDDDDDDDD'].visible), 1, 'BARU_TERBIT_MANUAL terlihat');
  assert.equal(byId['SSSSSSSSSSS'].platform, 'showroom', 'Baris Showroom harus terbaca platform-nya');
  assert.equal(Number(byId['SSSSSSSSSSS'].visible), 1, 'SHOWROOM_BARU harus langsung terlihat');
  assert.equal(Number(byId['SSSSSSSSSSS'].decided), 0, 'SHOWROOM_BARU belum diputuskan admin');
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
  assert.doesNotMatch(html0, watchLink(OLD_ID), 'Dengan auto=0, rekaman IDN lama harus tetap tersembunyi');
  assert.doesNotMatch(html0, /class="upcoming-badge"/, 'Dengan auto=0 tidak ada jadwal rilis, jadi tidak ada kartu Segera');
  assert.match(html0, watchLink(FAST_ID), 'Publikasi manual tetap berlaku saat auto=0');
  assert.match(html0, watchLink(SHOWROOM_ID), 'Rekaman Showroom tetap langsung tampil meski ambang IDN = 0');
  // Dengan auto=0 rekaman IDN menunggu keputusan admin: halamannya tetap bisa
  // dibuka dengan panel "segera hadir" tanpa angka (bukan pemutar). RECENT_ID
  // dipakai di sini karena OLD_ID sudah ditahan admin pada langkah sebelumnya.
  const noSchedule = await fetch(s0.origin + `/watch/${maskWatchId(RECENT_ID)}`);
  assert.equal(noSchedule.status, 200, 'Dengan auto=0, halaman rekaman menunggu keputusan admin tetap 200');
  assert.match(await noSchedule.text(), /Replay segera hadir/, 'Tanpa jadwal rilis, panel "segera hadir" tetap tampil');
  assert.equal((await fetch(s0.origin + `/watch/${maskWatchId(SHOWROOM_ID)}`)).status, 200, 'Dengan auto=0, watch Showroom tetap 200');

  // Keputusan admin juga menang untuk rekaman Showroom yang "langsung tampil".
  assert.equal((await post(s72, '/api/admin/publications', { youtube_video_id: SHOWROOM_ID, published: false }, cookie)).status, 200);
  const afterShowroomWithhold = await (await fetch(s72.origin + '/')).text();
  assert.doesNotMatch(afterShowroomWithhold, new RegExp(SHOWROOM_ID), 'Showroom yang ditahan admin harus hilang dari publik');

  console.log('PASS: rilis otomatis 72 jam (IDN), Showroom langsung tampil, keputusan admin menang atas otomatis, ambang 0 mematikan rilis otomatis IDN.');
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
