/**
 * tiktok-page.mjs — verifikasi halaman arsip TikTok (/tiktok).
 *
 * Aturan yang diuji (lihat web/lib/tiktok.ts + components/TikTokArchive.tsx):
 *   1. Halaman /tiktok 200 dan memuat tata letak 3 kolom
 *      (.tiktok-shell > .tiktok-accounts · .tiktok-stage · .tiktok-posts).
 *   2. Sidebar kiri hanya memuat akun ENABLED (akun nonaktif disembunyikan).
 *   3. Sidebar kanan hanya memuat arsip yang SUDAH siap (punya video YouTube
 *      atau media di channel arsip Telegram) — postingan yang masih diproses dan
 *      yang ditandai `visible = 0` tidak boleh muncul.
 *   4. Urutan daftar: terbaru lebih dulu.
 *   5. Postingan FOTO menampilkan jumlah fotonya dan tombol download-nya memakai
 *      payload bot `tt_<post_id>` (bukan YouTube ID).
 *   6. Story ikut terdaftar dan diberi label "Story".
 *   7. API `/api/tiktok/posts` bisa memfilter satu akun.
 *   8. Anti-regresi bahasa: halaman publik tidak memakai kata "Anda" maupun
 *      istilah "siaran ulang".
 *   9. Arsip terpilih saat halaman dibuka = terbaru yang BISA DIPUTAR (punya
 *      YouTube), bukan terbaru yang hanya bisa diunduh via bot.
 *  10. Kartu tanpa YouTube diberi penanda "Unduh via bot"; ringkasan header
 *      memecah "bisa diputar" vs "unduh via bot"; sidebar akun punya pencarian.
 *  11. Navigasi feed: tombol ↑/↓ melayang di area pemutar (plus roda mouse,
 *      panah keyboard di sisi klien) dengan label aksesibel — gestur jari
 *      sengaja tidak dipakai agar tidak berebut scroll dengan halaman.
 *
 * Port 3115 (3107 = public-private-check, 3108 = player-check,
 * 3110 = player-trial-check, 3111/3112 = auto-publish-check,
 * 3113/3114 = home-upcoming-grid) supaya tidak menempel ke server lain.
 */
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const temp = mkdtempSync(join(tmpdir(), 'jkt48-tiktok-page-'));
const dbPath = join(temp, 'fixture.db');
const PORT = 3115;
const BOT_USERNAME = 'jkt48_uji_bot';

/** ID YouTube 11 karakter (syarat codec web/lib/codec.ts). */
const YT_VIDEO = 'TTV00000001';
const YT_PHOTO = 'TTP00000001';
const POST_VIDEO = '7685965837307596052';
const POST_PHOTO = '7679000000000000001';
const POST_STORY = '7685000000000000009';
const POST_LULU = '7681262713913347348';
const POST_NEWEST_DL = '7690000000000000001'; // terbaru, TANPA YouTube (unduh via bot)
const POST_PENDING = '7670000000000000099';   // belum siap (tanpa yt & tg)
const POST_HIDDEN = '7670000000000000088';    // visible = 0
const ACCOUNT_OFF = 'offjkt48';               // enabled = 0
// Foto member dari roster resmi (bot/jkt48_members.py → kolom avatar_url).
const AVATAR_INDAH = 'https://jkt48.com/api/v1/storages/media/jkt48-member/indah_cahya.jpg';

const db = new DatabaseSync(dbPath);
db.exec(`
CREATE TABLE member_hls (
  username TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  hls_url TEXT,
  hls_confirmed INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_at TEXT DEFAULT (datetime('now')),
  last_live_at TEXT
);
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
CREATE TABLE web_publications (
  youtube_video_id TEXT PRIMARY KEY,
  published INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE tiktok_accounts (
  unique_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  member_username TEXT,
  sec_uid TEXT,
  avatar_url TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_checked_at TEXT,
  last_post_at TEXT,
  added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE tiktok_posts (
  id TEXT PRIMARY KEY,
  unique_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'video',
  is_story INTEGER NOT NULL DEFAULT 0,
  title TEXT,
  created_at TEXT,
  duration_seconds INTEGER DEFAULT 0,
  image_count INTEGER DEFAULT 0,
  cover_url TEXT,
  source_url TEXT,
  media_path TEXT,
  media_size_bytes INTEGER DEFAULT 0,
  images_json TEXT,
  local_images_json TEXT,
  telegram_message_ids TEXT,
  youtube_video_id TEXT,
  visible INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'detected',
  error_message TEXT,
  added_at TEXT DEFAULT (datetime('now'))
);

INSERT INTO member_hls (username, display_name, hls_confirmed) VALUES
  ('jkt48_indah', 'Indah JKT48', 1),
  ('jkt48_lulu', 'Lulu JKT48', 1);

INSERT INTO tiktok_accounts (unique_id, display_name, member_username, avatar_url, enabled) VALUES
  ('indahjkt48', 'Indah JKT48', 'jkt48_indah', '${AVATAR_INDAH}', 1),
  ('lulu_jkt48', 'Lulu JKT48', 'jkt48_lulu', NULL, 1),
  ('${ACCOUNT_OFF}', 'Akun Nonaktif', NULL, NULL, 0);
`);

db.exec(`
INSERT INTO tiktok_posts
  (id, unique_id, kind, is_story, title, created_at, duration_seconds, image_count,
   cover_url, source_url, media_path, telegram_message_ids, youtube_video_id, visible, status)
VALUES
  ('${POST_VIDEO}', 'indahjkt48', 'video', 0, 'twinnie', datetime('now', '-1 hours'),
   12, 0, 'https://p16.invalid/cover-video.webp',
   'https://www.tiktok.com/@indahjkt48/video/${POST_VIDEO}', '/tmp/tt/video.mp4',
   '101', '${YT_VIDEO}', 1, 'done'),
  ('${POST_PHOTO}', 'indahjkt48', 'photo', 0, 'foto hari ini (12 foto)', datetime('now', '-3 hours'),
   36, 12, 'https://p16.invalid/cover-photo.webp',
   'https://www.tiktok.com/@indahjkt48/video/${POST_PHOTO}', '/tmp/tt/slide.mp4',
   '201,202', '${YT_PHOTO}', 1, 'done'),
  ('${POST_STORY}', 'indahjkt48', 'video', 1, 'story latihan', datetime('now', '-5 hours'),
   9, 0, 'https://p16.invalid/cover-story.webp',
   'https://www.tiktok.com/@indahjkt48/video/${POST_STORY}', '/tmp/tt/story.mp4',
   '301', NULL, 1, 'done'),
  ('${POST_LULU}', 'lulu_jkt48', 'video', 0, 'makan bareng', datetime('now', '-7 hours'),
   30, 0, 'https://p16.invalid/cover-lulu.webp',
   'https://www.tiktok.com/@lulu_jkt48/video/${POST_LULU}', '/tmp/tt/lulu.mp4',
   '401', 'TTL00000001', 1, 'done'),
  ('${POST_NEWEST_DL}', 'indahjkt48', 'video', 0, 'baru banget', datetime('now', '-10 minutes'),
   21, 0, 'https://p16.invalid/cover-baru.webp',
   'https://www.tiktok.com/@indahjkt48/video/${POST_NEWEST_DL}', '/tmp/tt/baru.mp4',
   '601', NULL, 1, 'done'),
  ('${POST_PENDING}', 'indahjkt48', 'video', 0, 'belum selesai diproses',
   datetime('now', '-30 minutes'), 15, 0, NULL,
   'https://www.tiktok.com/@indahjkt48/video/${POST_PENDING}', '/tmp/tt/pending.mp4',
   NULL, NULL, 1, 'downloading'),
  ('${POST_HIDDEN}', 'indahjkt48', 'video', 0, 'ditahan pengelola', datetime('now', '-2 hours'),
   20, 0, NULL, 'https://www.tiktok.com/@indahjkt48/video/${POST_HIDDEN}',
   '/tmp/tt/hidden.mp4', '501', 'TTH00000001', 0, 'done');
`);

const servers = [];

function startServer() {
  const origin = `http://localhost:${PORT}`;
  const child = spawn(
    process.execPath,
    [join(root, 'node_modules/next/dist/bin/next'), 'start', '-p', String(PORT)],
    {
      cwd: root,
      env: {
        ...process.env,
        NODE_ENV: 'production',
        DB_PATH: dbPath,
        ADMIN_SECRET: randomBytes(32).toString('hex'),
        APP_ORIGIN: origin,
        NEXT_PUBLIC_REPLAY_BOT_USERNAME: BOT_USERNAME,
        TURNSTILE_SITE_KEY: 'off',
        TURNSTILE_SECRET_KEY: 'off',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  );
  const server = { origin, child, logs: '' };
  child.stdout.on('data', (d) => { server.logs += d.toString(); });
  child.stderr.on('data', (d) => { server.logs += d.toString(); });
  servers.push(server);
  return server;
}

async function waitReady(server) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (server.child.exitCode !== null) {
      throw new Error(`Server mati lebih awal:\n${server.logs}`);
    }
    try {
      const res = await fetch(`${server.origin}/tiktok`);
      if (res.ok) return res;
    } catch {
      // server belum siap, tunggu lagi
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`Server tidak siap dalam 60s:\n${server.logs}`);
}


try {
  const server = startServer();
  const first = await waitReady(server);
  const html = await first.text();

  assert.match(html, /Arsip TikTok member JKT48/, '/tiktok harus memuat judul halamannya');
  assert.match(html, /class="tiktok-shell"/, 'Tata letak 3 kolom (.tiktok-shell) wajib ada');
  assert.match(html, /tiktok-accounts/, 'Sidebar kiri (akun) wajib ada');
  assert.match(html, /tiktok-stage/, 'Kolom tengah (pemutar) wajib ada');
  assert.match(html, /tiktok-posts/, 'Sidebar kanan (daftar arsip) wajib ada');

  // ── Sidebar kiri: hanya akun enabled ────────────────────────────────────
  assert.ok(html.includes('Indah JKT48'), 'Akun Indah harus tampil di sidebar');
  assert.ok(html.includes('Lulu JKT48'), 'Akun Lulu harus tampil di sidebar');
  assert.ok(!html.includes('Akun Nonaktif'), 'Akun yang di-stop (enabled=0) tidak boleh tampil');
  assert.ok(!html.includes(ACCOUNT_OFF), 'Username akun nonaktif tidak boleh tampil');

  // Foto member dari roster resmi (kolom avatar_url) lewat next/image; akun
  // tanpa foto tetap memakai inisial supaya baris daftar tidak melompat.
  assert.match(html, /tiktok-account-avatar/, 'Akun dengan foto roster memakai .tiktok-account-avatar');
  assert.ok(
    html.includes(encodeURIComponent(AVATAR_INDAH)),
    'Foto member harus dimuat dari URL roster resmi jkt48.com'
  );
  assert.match(html, /tiktok-account-initial/, 'Akun tanpa foto tetap memakai inisial');

  // ── Sidebar kanan: hanya arsip yang sudah siap ──────────────────────────
  assert.ok(html.includes('twinnie'), 'Arsip video terbaru harus muncul di daftar');
  assert.ok(html.includes('makan bareng'), 'Arsip akun lain tetap ikut di daftar "Semua akun"');
  assert.ok(
    !html.includes('belum selesai diproses'),
    'Postingan yang masih diproses (tanpa YouTube/Telegram) tidak boleh tampil'
  );
  assert.ok(
    !html.includes('ditahan pengelola'),
    'Postingan visible=0 tidak boleh tampil di halaman publik'
  );

  // Urutan: terbaru lebih dulu (video 1 jam lalu sebelum foto 3 jam lalu).
  assert.ok(
    html.indexOf('twinnie') < html.indexOf('foto hari ini (12 foto)'),
    'Daftar arsip harus urut terbaru lebih dulu'
  );

  // ── Detail arsip terpilih: terbaru yang BISA DIPUTAR (bukan terbaru mutlak) ─
  // Fixture sengaja punya arsip PALING baru tanpa YouTube ('baru banget') —
  // pemutar awal tetap harus memakai arsip terbaru ber-YouTube ('twinnie').
  assert.match(html, /Video TikTok Indah JKT48/, 'Judul pemutar memuat jenis arsip + member');
  assert.match(html, /tiktok-player-container/, 'Wadah pemutar wajib dirender');
  assert.match(html, new RegExp(`data-download-payload="tt_${POST_VIDEO}"`),
    'Arsip terpilih awal = terbaru yang bisa diputar (punya YouTube)');
  assert.doesNotMatch(html, new RegExp(`data-download-payload="tt_${POST_NEWEST_DL}"`),
    'Arsip terbaru tanpa YouTube TIDAK boleh jadi pilihan awal');

  // ── Penanda unduh + ringkasan jujur + pencarian akun ─────────────────────
  assert.match(html, /Unduh via bot/, 'Kartu tanpa YouTube wajib diberi penanda "Unduh via bot"');
  assert.match(html, /5 arsip dari 2 akun — 3 bisa diputar · 2 unduh via bot/,
    'Ringkasan header memecah arsip bisa diputar vs unduh via bot');
  assert.match(html, /Cari member/, 'Sidebar akun wajib punya kolom pencarian');

  // ── Navigasi feed (↑/↓) di area pemutar ──────────────────────────────────
  const nav = html.match(/<div class="tiktok-nav">[\s\S]*?<\/div>/)?.[0] || '';
  assert.ok(nav, 'Wadah tombol navigasi wajib ada di area pemutar');
  assert.match(nav, /aria-label="Arsip sebelumnya"/, 'Tombol arsip sebelumnya wajib ada');
  assert.match(nav, /aria-label="Arsip berikutnya"/, 'Tombol arsip berikutnya wajib ada');
  // Fixture: terpilih = 'twinnie' (urutan ke-2 dari 5) → dua tombol aktif.
  assert.ok(!nav.includes('disabled'), 'Di tengah daftar kedua tombol harus aktif');

  // ── Postingan foto: label jumlah foto + story berlabel ──────────────────
  assert.match(html, /12 foto/, 'Postingan foto harus menampilkan jumlah fotonya');
  assert.match(html, /Story/, 'Arsip story harus diberi label Story');

  // ── Anti-regresi bahasa ─────────────────────────────────────────────────
  assert.doesNotMatch(html, /\bAnda\b/, 'UI publik memakai "kamu", bukan "Anda"');
  assert.doesNotMatch(html, /siaran ulang/i, 'Istilah publik adalah "replay", bukan "siaran ulang"');

  // ── API: daftar lengkap + filter per akun ───────────────────────────────
  const all = await (await fetch(`${server.origin}/api/tiktok/posts`)).json();
  assert.equal(all.success, true, 'API daftar arsip harus sukses');
  assert.equal(all.total, 5, `API harus mengembalikan 5 arsip siap (dapat ${all.total})`);
  const ids = all.posts.map((p) => p.id);
  assert.ok(ids.includes(POST_VIDEO) && ids.includes(POST_PHOTO) && ids.includes(POST_STORY));
  assert.ok(!ids.includes(POST_PENDING), 'Arsip yang belum siap tidak boleh dikirim API');
  assert.ok(!ids.includes(POST_HIDDEN), 'Arsip visible=0 tidak boleh dikirim API');

  const lulu = await (
    await fetch(`${server.origin}/api/tiktok/posts?account=lulu_jkt48`)
  ).json();
  assert.equal(lulu.total, 1, 'Filter akun harus menyisakan arsip akun itu saja');
  assert.equal(lulu.posts[0].id, POST_LULU);
  assert.equal(lulu.posts[0].download_payload, `tt_${POST_LULU}`);

  const photo = all.posts.find((p) => p.id === POST_PHOTO);
  assert.equal(photo.kind, 'photo');
  assert.equal(photo.image_count, 12);
  assert.match(photo.display_title, /Foto TikTok Indah JKT48/);
  assert.match(
    photo.thumbnail_url,
    /^https:\/\/img\.youtube\.com\/vi\//,
    'Thumbnail memakai YouTube (lebih awet daripada URL CDN TikTok)'
  );
  assert.equal(photo.telegram_archived, true);

  const story = all.posts.find((p) => p.id === POST_STORY);
  assert.equal(story.is_story, true);
  assert.match(story.display_title, /Story TikTok/);
  assert.equal(story.youtube_video_id, '', 'Story tanpa YouTube tetap tampil (ada arsip Telegram)');

  // ── Navigasi: tautan /tiktok tersedia di navbar halaman utama ───────────
  const home = await (await fetch(`${server.origin}/`)).text();
  assert.match(home, /href="\/tiktok"/, 'Navbar harus punya tautan ke /tiktok');

  console.log(
    'PASS: halaman /tiktok menampilkan tata letak 3 kolom (akun · pemutar · daftar arsip), ' +
    'hanya akun aktif & arsip siap tayang yang muncul (terbaru lebih dulu), arsip terpilih ' +
    'awal = terbaru yang bisa diputar, kartu tanpa YouTube berpenanda "Unduh via bot", ' +
    'ringkasan memecah bisa-diputar vs unduh-via-bot, sidebar akun punya pencarian, postingan ' +
    'foto memakai payload bot tt_<id>, story berlabel Story, API bisa memfilter per akun, ' +
    'dan tidak ada kata "Anda"/"siaran ulang".'
  );
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

