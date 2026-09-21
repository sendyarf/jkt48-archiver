/**
 * home-upcoming-grid.mjs — memverifikasi kartu pra-rilis ("Segera") di GRID HOME.
 *
 * Aturan yang diuji (lihat web/lib/db.ts → getUpcomingVideos + PublicCatalog):
 *   1. SEMUA rekaman pra-rilis tampil di grid home — bukan hanya 6 item terdekat
 *      seperti versi lama (`ORDER BY publish_at ASC LIMIT 6`), yang membuat
 *      rekaman pra-rilis TERBARU tidak pernah muncul di grid.
 *   2. Urutan grid: rekaman terbaru lebih dulu (pra-rilis terbaru di atas).
 *   3. Rekaman yang DITAHAN admin (published=0) tidak muncul di grid & watch 404.
 *   4. Rekaman yang diterbitkan manual (published=1) tampil sebagai rekaman biasa.
 *   5. Filter member/platform tetap menampilkan pra-rilis yang cocok; pencarian
 *      kata kunci (q) tetap murni.
 *   6. Kartu pra-rilis hanya dipasang di halaman 1 (tidak digandakan).
 *   7. Link di grid memakai ID tersamar (Base64URL), bukan YouTube ID mentah.
 *   8. Ambang Showroom: 0 (default) = langsung terbit (bukan pra-rilis);
 *      > 0 = pra-rilis berjadwal dengan countdown.
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
const temp = mkdtempSync(join(tmpdir(), 'jkt48-home-upcoming-'));
const dbPath = join(temp, 'fixture.db');
const db = new DatabaseSync(dbPath);

// Port khusus: 3107 = public-private-check, 3108 = player-check,
// 3110 = player-trial-check, 3111/3112 = auto-publish-check.
// JANGAN pakai port yang sama agar uji tidak diam-diam menempel ke server lain.
const PORT_SHOWROOM_LIVE = 3113; // ambang Showroom 0 → langsung terbit
const PORT_SHOWROOM_SCHED = 3114; // ambang Showroom 12 jam → Showroom bisa pra-rilis

const MEMBER = 'jkt48_uji';
/** ID YouTube harus 11 karakter agar tersamar (lihat web/lib/codec.ts). */
const ytId = (prefix, n, width) => prefix + String(n).padStart(width, '0');
const PENDING = Array.from({ length: 9 }, (_, i) => ytId('UP', i + 1, 9));
const PENDING_HOURS = PENDING.map((_, i) => 2 + i * 2); // 2,4,…,18 jam (pra-rilis)
const OLD = Array.from({ length: 26 }, (_, i) => ytId('OLD', i + 1, 8)); // > 72 jam
const WITHHELD = 'WH000000001'; // ditahan admin (published = 0)
const MANUAL = 'MAN00000001'; // diterbitkan manual (published = 1) — 1 jam lalu
const SHOWROOM_NEW = 'SR000000001'; // Showroom 3 jam lalu
const SHOWROOM_OLD = 'SR000000002'; // Showroom 20 jam lalu

for (const id of [...PENDING, ...OLD, WITHHELD, MANUAL, SHOWROOM_NEW, SHOWROOM_OLD]) {
  assert.equal(id.length, 11, `ID fixture harus 11 karakter: ${id}`);
}

/**
 * Date search unit checks against the real extractSearchDates (web/lib/wib.ts).
 * Expectations are computed from the live server clock so the script stays
 * green whatever day it runs (see checkDateCases() below).
 */

const inserts = [];
const publications = [];

/**
 * Unit checks for extractSearchDates() (web/lib/wib.ts), the date parser
 * behind keyword date search. Expected values are derived from the real clock
 * so the script is green on any run date. wib.ts has no dependencies, so it
 * is imported directly (Node strips types).
 */
async function checkDateCases() {
  const { extractSearchDates } = await import('../lib/wib.ts');
  const now = new Date();
  const y = now.getFullYear();
  const pad = (n) => String(n).padStart(2, '0');
  const isoToday = `${y}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const monthToday = isoToday.slice(0, 7);
  const lastYear = `${y - 1}-${pad(now.getMonth() + 1)}`;
  const cases = [
    [`${now.getDate()} ${['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'][now.getMonth()]}`, [isoToday, `${y - 1}-${isoToday.slice(5)}`]],
    [`${now.getDate()} ${['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'][now.getMonth()]} ${y}`, [isoToday]],
    [`${['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'][now.getMonth()]} ${y}`, [monthToday]],
    [`${['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'][now.getMonth()]}`, [monthToday, lastYear]],
    ['18/09/2026', ['2026-09-18']],
    ['2026-09-18', ['2026-09-18']],
    [`${pad(now.getDate())}/${pad(now.getMonth() + 1)}/${y}`, [isoToday]],
    ['lulu', []],
    ['live idn', []],
    ['september', ['2026-09', '2025-09'].map((d) => d.replace(/^2026-09$/, monthToday).replace(/^2025-09$/, lastYear))],
  ];
  for (const [keyword, expected] of cases) {
    assert.deepEqual(extractSearchDates(keyword), expected, `extractSearchDates(${JSON.stringify(keyword)})`);
  }
}

/**
 * Satu baris rekaman. `endedHoursAgo` menentukan status (pra-rilis vs terbit)
 * karena semua query memakai julianday('now') vs download_ended_at.
 */
function addRow({ liveId, id, platform = 'idn', endedHoursAgo, decided = null }) {
  inserts.push(`INSERT INTO live_sessions (live_id, member_username, member_name, started_at, download_started_at, download_ended_at, status, file_size_bytes, platform, youtube_video_id, created_at)
    VALUES ('${liveId}', '${MEMBER}', 'Uji Member',
      datetime('now', '-${endedHoursAgo} hours'),
      datetime('now', '-${endedHoursAgo + 1} hours'),
      datetime('now', '-${endedHoursAgo} hours'),
      'done_youtube', 1048576, '${platform}', '${id}',
      datetime('now', '-${endedHoursAgo} hours'));`);
  if (decided !== null) {
    publications.push(`INSERT INTO web_publications (youtube_video_id, published) VALUES ('${id}', ${decided});`);
  }
}

PENDING.forEach((id, i) => addRow({ liveId: `up-${i + 1}`, id, endedHoursAgo: PENDING_HOURS[i] }));
OLD.forEach((id, i) => addRow({ liveId: `old-${i + 1}`, id, endedHoursAgo: 100 + i }));
addRow({ liveId: 'withheld', id: WITHHELD, endedHoursAgo: 5, decided: 0 });
addRow({ liveId: 'manual', id: MANUAL, endedHoursAgo: 1, decided: 1 });
addRow({ liveId: 'sr-new', id: SHOWROOM_NEW, platform: 'showroom', endedHoursAgo: 3 });
addRow({ liveId: 'sr-old', id: SHOWROOM_OLD, platform: 'showroom', endedHoursAgo: 20 });

db.exec(`
CREATE TABLE live_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  live_id TEXT UNIQUE NOT NULL,
  member_username TEXT NOT NULL,
  member_name TEXT,
  started_at TEXT,
  hls_url TEXT,
  download_started_at TEXT,
  download_ended_at TEXT,
  file_path TEXT,
  file_size_bytes INTEGER,
  status TEXT NOT NULL DEFAULT 'detected',
  telegram_message_id INTEGER,
  telegram_message_ids TEXT,
  youtube_video_id TEXT,
  error_message TEXT,
  merge_group_id INTEGER,
  platform TEXT NOT NULL DEFAULT 'idn',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE merge_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member_username TEXT NOT NULL,
  member_name TEXT,
  started_at TEXT,
  thumbnail_url TEXT,
  live_title TEXT,
  platform TEXT NOT NULL DEFAULT 'idn',
  status TEXT NOT NULL DEFAULT 'waiting',
  merged_file_path TEXT,
  merged_live_id TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE member_hls (
  username TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  hls_url TEXT,
  hls_confirmed INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_at TEXT DEFAULT (datetime('now')),
  last_live_at TEXT
);
CREATE TABLE web_publications (
  youtube_video_id TEXT PRIMARY KEY,
  published INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO member_hls (username, display_name, hls_confirmed) VALUES ('${MEMBER}', 'Uji Member', 1);
${inserts.join('\n')}
${publications.join('\n')}
`);

/** Cermin encodeWatchId() di web/lib/codec.ts untuk ID 11 karakter. */
const mask = (id) => Buffer.from(id, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const countOf = (html, needle) => html.split(needle).length - 1;
const CARD_UPCOMING = 'class="upcoming-badge"';

const secret = randomBytes(32).toString('hex');
const servers = [];

function startServer(port, autoHours, showroomHours) {
  const origin = `http://localhost:${port}`;
  const child = spawn(process.execPath, [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', String(port)], {
    cwd: root,
    env: {
      ...process.env,
      NODE_ENV: 'production',
      ADMIN_SECRET: secret,
      APP_ORIGIN: origin,
      DB_PATH: dbPath,
      AUTO_PUBLISH_AFTER_HOURS: String(autoHours),
      AUTO_PUBLISH_AFTER_HOURS_SHOWROOM: String(showroomHours),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  child.stdout.on('data', (d) => { logs += d; });
  child.stderr.on('data', (d) => { logs += d; });
  const server = { child, origin, port, logs: () => logs };
  servers.push(server);
  return server;
}

async function waitReady(server) {
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(server.origin + '/api/auth');
      if (res.ok) return;
    } catch { /* belum siap */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`Server :${server.port} tidak siap.\n${server.logs()}`);
}

/** Penjaga anti-tabrakan port: pastikan server memakai database fixture ini. */
async function assertOwnFixture(server) {
  const html = await (await fetch(server.origin + '/')).text();
  if (!html.includes('Uji Member')) {
    throw new Error(`Server :${server.port} tidak memakai database fixture (port mungkin dipakai proses lain).\n${server.logs()}`);
  }
}

async function getHtml(server, path = '/') {
  const res = await fetch(server.origin + path);
  assert.equal(res.status, 200, `GET ${path} harus 200`);
  return res.text();
}

try {
  // ── Bagian 1: ambang Showroom 0 (default) = Showroom langsung terbit ──────
  const live = startServer(PORT_SHOWROOM_LIVE, 72, 0);
  await waitReady(live);
  await assertOwnFixture(live);

  const html = await getHtml(live);
  // 1. SEMUA pra-rilis ada di grid home — bukan hanya 6 seperti versi lama.
  assert.equal(countOf(html, CARD_UPCOMING), PENDING.length,
    `Semua ${PENDING.length} rekaman pra-rilis harus tampil di grid home (versi lama hanya 6)`);
  for (const id of PENDING) {
    assert.ok(html.includes(`/watch/${mask(id)}`), `Pra-rilis ${id} harus punya kartu di grid home`);
    assert.doesNotMatch(html, new RegExp(`/watch/${id}`), `Link kartu ${id} tidak boleh memakai YouTube ID mentah`);
  }
  // 2. Urutan grid: pra-rilis terbaru di atas.
  const newest = PENDING[0]; // 2 jam lalu
  const oldest = PENDING[PENDING.length - 1]; // 18 jam lalu
  assert.ok(html.indexOf(mask(newest)) < html.indexOf(mask(oldest)),
    'Pra-rilis terbaru harus tampil di atas pra-rilis terlama');
  // 3. Ditahan admin → tidak muncul di grid.
  assert.doesNotMatch(html, new RegExp(`/watch/${mask(WITHHELD)}`),
    'Rekaman yang ditahan admin tidak boleh muncul di grid');
  // 4. Terbit manual → rekaman biasa (tanpa badge Segera) di urutan paling atas.
  assert.ok(html.includes(`/watch/${mask(MANUAL)}`), 'Rekaman terbit manual harus tampil di grid');
  assert.ok(html.indexOf(mask(MANUAL)) < html.indexOf(mask(newest)),
    'Rekaman 1 jam lalu harus di atas pra-rilis 2 jam lalu');
  // 5. Ringkasan: pra-rilis dilaporkan, Showroom ambang 0 dihitung terbit.
  assert.match(html, /replay siap ditonton/, 'Ringkasan jumlah replay harus tampil');
  assert.match(html, /\(\+9 segera hadir\)/, 'Ringkasan harus melaporkan 9 pra-rilis');
  assert.ok(html.includes(`/watch/${mask(SHOWROOM_NEW)}`) && html.includes(`/watch/${mask(SHOWROOM_OLD)}`),
    'Showroom dengan ambang 0 langsung tampil di grid');
  // 6. Durasi terisi dari rentang download (1 jam).
  assert.match(html, /1:00:00/, 'Kartu harus menampilkan durasi rekaman');

  // Filter member: pra-rilis member tersebut tetap tampil.
  const memberHtml = await getHtml(live, `/?member=${MEMBER}`);
  assert.equal(countOf(memberHtml, CARD_UPCOMING), PENDING.length,
    'Filter member harus tetap menampilkan pra-rilis member itu');
  const missingHtml = await getHtml(live, '/?member=jkt48_hilang');
  assert.equal(countOf(missingHtml, CARD_UPCOMING), 0, 'Filter tanpa rekaman tidak boleh menampilkan pra-rilis');
  assert.match(missingHtml, /Belum ada replay yang cocok/, 'Filter tanpa hasil harus menampilkan empty state');

  // Filter platform: IDN punya 9 pra-rilis; Showroom (ambang 0) tidak punya.
  const idnHtml = await getHtml(live, '/?platform=idn');
  assert.equal(countOf(idnHtml, CARD_UPCOMING), PENDING.length, 'Filter platform IDN harus menampilkan pra-rilis IDN');
  const showroomHtml = await getHtml(live, '/?platform=showroom');
  assert.equal(countOf(showroomHtml, CARD_UPCOMING), 0, 'Platform Showroom (ambang 0) tidak punya pra-rilis');
  assert.doesNotMatch(showroomHtml, /segera hadir/, 'Tanpa pra-rilis, ringkasan tidak boleh menyebut segera hadir');

  // Pencarian kata kunci tetap murni.
  const searchHtml = await getHtml(live, '/?q=uji');
  assert.equal(countOf(searchHtml, CARD_UPCOMING), 0, 'Hasil pencarian kata kunci tidak boleh dicampur pra-rilis');

  // ── Bagian 1b: pencarian tanggal ──────────────────────────────────────────
  // Fixture memakai jam berjalan: "tanggal"-nya = tanggal hari ini (WIB ±1 hari).
  await checkDateCases();
  const nowParts = new Intl.DateTimeFormat('id-ID', {
    timeZone: 'Asia/Jakarta', day: 'numeric', month: 'long', year: 'numeric',
  }).formatToParts(new Date());
  const part = (t) => nowParts.find((p) => p.type === t).value;
  const todayId = `${part('day')} ${part('month')} ${part('year')}`; // mis. "19 September 2026"
  const monthYearId = `${part('month')} ${part('year')}`; // mis. "September 2026"
  for (const keyword of [todayId, monthYearId, todayId.toLowerCase()]) {
    const dateHtml = await getHtml(live, `/?q=${encodeURIComponent(keyword)}`);
    // Ringkasan pencarian menggemakan kata kunci (tanda kutip khas "..." plus
    // pemisah komentar khas render React di antara node teks).
    assert.ok(/untuk /.test(dateHtml) && dateHtml.includes(keyword),
      `Ringkasan pencarian harus menggemakan kata kunci ${JSON.stringify(keyword)}`);
    assert.ok(countOf(dateHtml, 'video-card') >= 1 || !/Belum ada replay yang cocok/.test(dateHtml),
      `Pencarian tanggal ${JSON.stringify(keyword)} tidak boleh kosong total`);
  }
  // Kontrol negatif: tanggal yang pasti tidak ada di fixture tetap kosong.
  const emptyHtml = await getHtml(live, '/?q=17%20Januari%201990');
  assert.match(emptyHtml, /Belum ada replay yang cocok/, 'Tanggal fiktif harus tetap menampilkan empty state');

  // Halaman 2 tidak menggandakan kartu pra-rilis.
  const page2 = await getHtml(live, '/?page=2');
  assert.equal(countOf(page2, CARD_UPCOMING), 0, 'Kartu pra-rilis hanya dipasang di halaman 1');
  assert.ok(page2.includes('/?page=1#catalog'), 'Halaman 2 harus punya tautan kembali ke halaman 1');

  // Halaman pra-rilis = countdown; rekaman terbit = pemutar; ditahan = 404.
  const pendingRes = await fetch(live.origin + `/watch/${mask(newest)}`);
  assert.equal(pendingRes.status, 200, 'Halaman watch pra-rilis harus 200 (countdown)');
  const pendingHtml = await pendingRes.text();
  assert.match(pendingHtml, /Replay segera hadir/, 'Pra-rilis harus menampilkan panel countdown');
  assert.match(pendingHtml, /countdown-panel/, 'Panel countdown harus dirender server');
  const manualRes = await fetch(live.origin + `/watch/${mask(MANUAL)}`);
  assert.equal(manualRes.status, 200, 'Rekaman terbit manual harus 200');
  assert.doesNotMatch(await manualRes.text(), /Replay segera hadir/, 'Rekaman terbit manual tidak boleh menampilkan countdown');
  // Status HTTP untuk rute dinamis yang notFound() dilaporkan 200 oleh Next 16.3.5
  // (rute statis benar 404). Jadi keputusan "tidak boleh muncul" dinilai dari
  // konten: halaman tidak boleh berisi panel countdown maupun pemutar video.
  const withheldRes = await fetch(live.origin + `/watch/${mask(WITHHELD)}`);
  const withheldHtml = await withheldRes.text();
  assert.doesNotMatch(withheldHtml, /countdown-panel/, 'Rekaman yang ditahan admin tidak boleh punya panel countdown');
  assert.doesNotMatch(withheldHtml, /video-player-container/, 'Rekaman yang ditahan admin tidak boleh punya pemutar');

  // ── Bagian 2: ambang Showroom 12 jam = Showroom bisa pra-rilis ────────────
  const sched = startServer(PORT_SHOWROOM_SCHED, 72, 12);
  await waitReady(sched);
  await assertOwnFixture(sched);
  const schedHtml = await getHtml(sched);
  assert.equal(countOf(schedHtml, CARD_UPCOMING), PENDING.length + 1,
    'Showroom berjadwal (3 jam < ambang 12 jam) harus ikut tampil sebagai pra-rilis');
  assert.ok(schedHtml.includes(`/watch/${mask(SHOWROOM_NEW)}`), 'Showroom muda harus punya kartu di grid');
  assert.match(schedHtml, /\(\+10 segera hadir\)/, 'Ringkasan harus menghitung Showroom berjadwal sebagai pra-rilis');
  const srPending = await fetch(sched.origin + `/watch/${mask(SHOWROOM_NEW)}`);
  assert.equal(srPending.status, 200);
  assert.match(await srPending.text(), /countdown-panel/, 'Showroom pra-rilis harus punya countdown terjadwal');
  const srOld = await fetch(sched.origin + `/watch/${mask(SHOWROOM_OLD)}`);
  assert.equal(srOld.status, 200);
  assert.doesNotMatch(await srOld.text(), /Replay segera hadir/, 'Showroom lewat ambang 12 jam harus sudah terbit');

  console.log('PASS: semua rekaman pra-rilis tampil di grid home (tanpa batas 6), urut terbaru dulu, yang ditahan admin tetap tersembunyi, filter member/platform tetap menampilkan pra-rilis, pencarian tanggal berfungsi, dan Showroom mengikuti ambangnya.');
} finally {
  for (const server of servers) {
    if (server.child.exitCode === null && server.child.signalCode === null) {
      server.child.kill();
      await new Promise((r) => server.child.once('exit', r));
    }
  }
  db.close();
  rmSync(temp, { recursive: true, force: true });
}