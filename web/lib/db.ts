import { existsSync } from 'node:fs';
import path from 'path';
import { DatabaseSync } from 'node:sqlite';
import { decodeWatchId, encodeWatchId } from './codec';

let _db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (!_db) {
    // Look for jkt48_live.db in parent directory or current directory
    const candidates = [
      process.env.DB_PATH,
      path.resolve(process.cwd(), '..', 'jkt48_live.db'),
      path.resolve(process.cwd(), 'jkt48_live.db'),
    ].filter(Boolean) as string[];

    let dbPath = candidates[0];
    for (const p of candidates) {
      try {
        if (existsSync(p)) {
          dbPath = p;
          break;
        }
      } catch {
        // continue
      }
    }

    // Peringatan salah-setel (terlihat di log server): DB_PATH diset tetapi
    // berkasnya tidak ada (mis. path RELATIF padahal cwd proses web adalah
    // folder web/) — web diam-diam jatuh ke kandidat lain, yang bisa jadi
    // database BERBEDA dari yang ditulis bot. DB_PATH harus ABSOLUT dan sama
    // persis dengan DB_PATH bot (lihat deploy/README.md).
    if (process.env.DB_PATH && !existsSync(process.env.DB_PATH)) {
      console.warn(
        `[db] DB_PATH "${process.env.DB_PATH}" tidak ditemukan — memakai ` +
        `"${dbPath}". Isi DB_PATH dengan path absolut yang sama dengan bot.`
      );
    }

    _db = new DatabaseSync(dbPath);
    // WAL + busy_timeout: bot (writer) dan web (reader) berbagi file yang sama.
    _db.exec('PRAGMA journal_mode=WAL');
    _db.exec('PRAGMA busy_timeout=5000');
    _db.exec('PRAGMA synchronous=NORMAL');

    // Initialize media_catalog table if not exists
    _db.exec(`
      CREATE TABLE IF NOT EXISTS web_publications (
        youtube_video_id TEXT PRIMARY KEY,
        published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      -- Soft-hide per konten (content_uid / youtube_video_id) dari /admin/videos.
      -- Menang atas web_publications & aturan auto-publish bila baris ada.
      CREATE TABLE IF NOT EXISTS web_video_overrides (
        content_key TEXT PRIMARY KEY,
        published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE TABLE IF NOT EXISTS media_catalog (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        live_id           TEXT UNIQUE,
        platform          TEXT NOT NULL DEFAULT 'idn',
        streamer_username TEXT NOT NULL,
        streamer_name     TEXT,
        title             TEXT,
        started_at        TEXT,
        duration_seconds  INTEGER DEFAULT 0,
        file_path         TEXT,
        file_size_bytes   INTEGER DEFAULT 0,
        youtube_video_id  TEXT,
        youtube_channel   TEXT,
        status            TEXT NOT NULL DEFAULT 'published',
        thumbnail_url     TEXT,
        created_at        TEXT DEFAULT (datetime('now'))
      );
      -- Arsip TikTok (dibuat bot; dibuat di sini juga agar halaman /tiktok
      -- tidak 500 saat web dijalankan terhadap database yang belum di-seed).
      CREATE TABLE IF NOT EXISTS tiktok_accounts (
        unique_id       TEXT PRIMARY KEY,
        display_name    TEXT NOT NULL DEFAULT '',
        member_username TEXT,
        sec_uid         TEXT,
        avatar_url      TEXT,
        enabled         INTEGER NOT NULL DEFAULT 1,
        last_checked_at TEXT,
        last_post_at    TEXT,
        added_at        TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE IF NOT EXISTS tiktok_posts (
        id                  TEXT PRIMARY KEY,
        unique_id           TEXT NOT NULL,
        kind                TEXT NOT NULL DEFAULT 'video',
        is_story            INTEGER NOT NULL DEFAULT 0,
        title               TEXT,
        created_at          TEXT,
        duration_seconds    INTEGER DEFAULT 0,
        image_count         INTEGER DEFAULT 0,
        cover_url           TEXT,
        source_url          TEXT,
        media_path          TEXT,
        media_size_bytes    INTEGER DEFAULT 0,
        images_json         TEXT,
        local_images_json   TEXT,
        telegram_message_ids TEXT,
        youtube_video_id    TEXT,
        visible             INTEGER NOT NULL DEFAULT 1,
        status              TEXT NOT NULL DEFAULT 'detected',
        error_message       TEXT,
        added_at            TEXT DEFAULT (datetime('now'))
      );
    `);

    // Index untuk query berat — best-effort & per-statement: fixture uji dan
    // database lama bisa tidak punya tabel/kolomnya (mis. merge_groups tanpa
    // status). Gagal membuat index hanya memengaruhi performa, bukan benar/salah.
    const indexStatements = [
      'CREATE INDEX IF NOT EXISTS idx_live_sessions_status ON live_sessions(status)',
      'CREATE INDEX IF NOT EXISTS idx_live_sessions_youtube ON live_sessions(youtube_video_id)',
      'CREATE INDEX IF NOT EXISTS idx_live_sessions_member ON live_sessions(member_username)',
      'CREATE INDEX IF NOT EXISTS idx_live_sessions_created ON live_sessions(created_at)',
      'CREATE INDEX IF NOT EXISTS idx_live_sessions_merge ON live_sessions(merge_group_id)',
      'CREATE INDEX IF NOT EXISTS idx_merge_groups_status ON merge_groups(status)',
      'CREATE INDEX IF NOT EXISTS idx_tiktok_posts_status ON tiktok_posts(status)',
      'CREATE INDEX IF NOT EXISTS idx_tiktok_posts_unique ON tiktok_posts(unique_id)',
    ];
    for (const sql of indexStatements) {
      try {
        _db.exec(sql);
      } catch {
        // Tabel/kolom belum ada — lewati.
      }
    }

    // Kolom milik skema bot yang dipakai aturan publik & label platform.
    // Bila web dijalankan terhadap database lama yang belum memilikinya,
    // tambahkan sebagai kolom nullable/default agar halaman publik tidak 500.
    // Nilai kosong otomatis diperlakukan sebagai 'idn' oleh query.
    try {
      const columns = _db.prepare('PRAGMA table_info(live_sessions)').all() as unknown as { name: string }[];
      if (columns.length > 0) {
        const existing = new Set(columns.map(column => column.name));
        if (!existing.has('download_ended_at')) {
          _db.exec('ALTER TABLE live_sessions ADD COLUMN download_ended_at TEXT');
        }
        if (!existing.has('download_started_at')) {
          _db.exec('ALTER TABLE live_sessions ADD COLUMN download_started_at TEXT');
        }
        if (!existing.has('platform')) {
          _db.exec("ALTER TABLE live_sessions ADD COLUMN platform TEXT NOT NULL DEFAULT 'idn'");
        }
        if (!existing.has('telegram_message_ids')) {
          _db.exec('ALTER TABLE live_sessions ADD COLUMN telegram_message_ids TEXT');
        }
        if (!existing.has('content_uid')) {
          _db.exec('ALTER TABLE live_sessions ADD COLUMN content_uid TEXT');
        }
      }
    } catch {
      // Tabel live_sessions belum ada (mis. database baru) — biarkan query
      // pemanggil yang menangani.
    }

    // Kolom `avatar_url` (foto member dari roster resmi jkt48.com) dipakai
    // sidebar /tiktok. Database lama belum memilikinya, sedangkan CREATE TABLE
    // IF NOT EXISTS tidak mengubah tabel yang sudah ada.
    try {
      const columns = _db.prepare('PRAGMA table_info(tiktok_accounts)').all() as unknown as { name: string }[];
      if (columns.length > 0 && !columns.some(column => column.name === 'avatar_url')) {
        _db.exec('ALTER TABLE tiktok_accounts ADD COLUMN avatar_url TEXT');
      }
    } catch {
      // Tabel tiktok_accounts belum ada — sama seperti di atas.
    }
  }
  return _db;
}

/**
 * Berapa jam setelah live selesai sebuah rekaman otomatis tampil di halaman
 * publik. 0 = nonaktif (semua rekaman wajib disetujui manual).
 *
 * Aturan visibilitas lengkap:
 *   - ada baris web_publications dengan published=1  -> PUBLIK (override admin)
 *   - ada baris dengan published=0                   -> TERSEMBUNYI (override admin)
 *   - tidak ada baris + sudah lewat AUTO_PUBLISH     -> PUBLIK (otomatis)
 *   - tidak ada baris + belum lewat                  -> TERSEMBUNYI
 *
 * Override admin selalu menang, sehingga keputusan manual tidak "tertimpa"
 * oleh aturan otomatis.
 */
export const AUTO_PUBLISH_AFTER_HOURS: number = (() => {
  const raw = Number(process.env.AUTO_PUBLISH_AFTER_HOURS ?? '72');
  return Number.isFinite(raw) && raw > 0 ? raw : 0;
})();

/**
 * Ambang rilis otomatis khusus rekaman **Showroom** (jam setelah live selesai).
 * Default `0` = rekaman Showroom **langsung tampil** segera setelah tersimpan,
 * tanpa menunggu ambang IDN — permintaan pemilik (Sep 2026). Isi nilai negatif
 * untuk mematikan rilis otomatis Showroom (wajib persetujuan admin).
 * Keputusan admin (terbit/tahan) tetap selalu menang atas aturan ini.
 */
export const AUTO_PUBLISH_AFTER_HOURS_SHOWROOM: number = (() => {
  const raw = Number(process.env.AUTO_PUBLISH_AFTER_HOURS_SHOWROOM ?? '0');
  return Number.isFinite(raw) && raw >= 0 ? raw : -1;
})();

/**
 * Predikat SQL untuk rekaman yang boleh tampil di halaman publik.
 * `alias` adalah alias tabel live_sessions pada query pemanggil.
 *
 * Waktu acuan memakai `download_ended_at` (kapan rekaman selesai) dan jatuh
 * ke `created_at` bila kosong. Keduanya UTC, dibandingkan dengan julianday
 * ('now') yang juga UTC — jadi tidak bergantung zona waktu server.
 *
 * Ambang per platform: showroom memakai AUTO_PUBLISH_AFTER_HOURS_SHOWROOM
 * (default 0 = langsung tampil; positif = tunggu N jam; negatif = wajib manual),
 * platform lain memakai AUTO_PUBLISH_AFTER_HOURS (default 72; 0 = nonaktif, wajib
 * manual). Baris tanpa kolom/platform diperlakukan 'idn' agar perilaku lama tidak
 * berubah.
 *
 * Prioritas keputusan (paling kuat → otomatis):
 *   1. web_video_overrides pada content_key (/admin/videos — sembunyikan/terbitkan
 *      per konten, termasuk arsip TG-first tanpa youtube_video_id)
 *   2. web_publications pada youtube_video_id (/admin/publications)
 *   3. Aturan auto-publish per platform.
 */
function publicVisibilitySql(alias = 'ls'): string {
  const contentKey = `COALESCE(NULLIF(${alias}.youtube_video_id, ''), ${alias}.content_uid)`;
  const overrideHide = `EXISTS (SELECT 1 FROM web_video_overrides vo WHERE vo.content_key = ${contentKey} AND vo.content_key != '' AND vo.published = 0)`;
  const overrideShow = `EXISTS (SELECT 1 FROM web_video_overrides vo WHERE vo.content_key = ${contentKey} AND vo.content_key != '' AND vo.published = 1)`;
  const explicitOn = `EXISTS (SELECT 1 FROM web_publications p WHERE p.youtube_video_id = ${alias}.youtube_video_id AND p.youtube_video_id != '' AND p.published = 1)`;
  const withheld = `EXISTS (SELECT 1 FROM web_publications p0 WHERE p0.youtube_video_id = ${alias}.youtube_video_id AND p0.youtube_video_id != '' AND p0.published = 0)`;
  const elapsedHours = `(julianday('now') - julianday(COALESCE(${alias}.download_ended_at, ${alias}.created_at))) * 24`;
  // Showroom: 0 = langsung tampil (konstanta 1, tanpa jeda), negatif = nonaktif,
  // positif = terjadwal N jam. Nilai positif sebelumnya diperlakukan sama dengan 0
  // sehingga jadwal rilis Showroom tidak pernah berlaku (padahal getUpcomingVideos
  // sudah menandainya pra-rilis → kartu muncul di grid tapi pemutar ikut muncul).
  const showroomAuto = AUTO_PUBLISH_AFTER_HOURS_SHOWROOM > 0
    ? `(${elapsedHours} >= ${AUTO_PUBLISH_AFTER_HOURS_SHOWROOM})`
    : (AUTO_PUBLISH_AFTER_HOURS_SHOWROOM === 0 ? '1' : '0');
  // IDN & platform lain: butuh lewat ambang; 0 = nonaktif (wajib manual).
  const idnAuto = AUTO_PUBLISH_AFTER_HOURS > 0
    ? `(${elapsedHours} >= ${AUTO_PUBLISH_AFTER_HOURS})`
    : '0';
  const fallback = `(${explicitOn} OR (NOT ${withheld} AND (CASE WHEN COALESCE(${alias}.platform, 'idn') = 'showroom' THEN ${showroomAuto} ELSE ${idnAuto} END)))`;
  return `((NOT ${overrideHide}) AND (${overrideShow} OR ${fallback}))`;
}

export interface VideoItem {
  id: number | string;
  platform: 'idn' | 'showroom' | 'other';
  streamer_username: string;
  streamer_name: string;
  title: string;
  started_at: string;
  /** Tanggal WIB siap-tampil untuk kartu (dihitung server, bukan browser). */
  date_display: string;
  duration_seconds: number;
  duration_formatted: string;
  youtube_video_id: string;
  /**
   * Kunci konten unik bot (`merged_<gid>` / `live_id`). Dipakai deep-link
   * replay bot & /watch bila YouTube belum ter-upload (arsip TG first).
   */
  content_uid?: string;
  /** ID tersamar (Base64URL) untuk URL /watch publik. */
  watch_id?: string;
  thumbnail_url: string;
  created_at: string;
  /** True bila replay sudah tersimpan di channel arsip Telegram (bisa diunduh via bot). */
  telegram_archived?: boolean;
  /** True bila rekaman berumur < 24 jam (untuk badge "BARU"). */
  is_new?: boolean;
  /** True bila rekaman sudah boleh tampil publik (sudah lewat ambang / diterbitkan). */
  is_visible?: boolean;
  /** ISO timestamp kapan rekaman otomatis terbit (untuk countdown pra-rilis). Kosong bila tidak dijadwalkan. */
  publish_at?: string;
}

/** Format detik → "H:MM:SS" atau "M:SS". */
function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  const ss = String(sec).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Hitung durasi dari rentang waktu mulai & selesai (detik). 0 bila tidak valid. */
function durationFromRange(start: string | null, end: string | null): number {
  if (!start || !end) return 0;
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  const diff = Math.round((b - a) / 1000);
  return diff > 0 ? diff : 0;
}

/** True bila timestamp ISO berada dalam `hours` terakhir. */
function isRecent(iso: string, hours = 24): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return false;
  return Date.now() - t < hours * 3600 * 1000;
}

function cleanDisplayName(username: string, name?: string): string {
  const normalizedUsername = (username || '').trim();
  const fallbackName = normalizedUsername.toLowerCase().startsWith('jkt48_')
    ? normalizedUsername.slice(5).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) + ' JKT48'
    : normalizedUsername;
  if (name && name.trim() && name.trim().toLowerCase() !== normalizedUsername.toLowerCase()) {
    return name.trim();
  }
  if (fallbackName) return fallbackName;
  return normalizedUsername;
}

// Konversi & format WIB dipusatkan di lib/wib.ts (server-side saja).
import { buildDisplayTitle, extractSearchDates, formatWibCardDate } from './wib';

export { buildDisplayTitle };

/** Normalisasi nilai platform dari database ke tipe yang dipakai UI. */
function normalizePlatform(value: string | null | undefined): VideoItem['platform'] {
  const text = (value || '').trim().toLowerCase();
  if (text === 'showroom') return 'showroom';
  if (text === 'idn' || text === 'idn_live') return 'idn';
  if (text) return 'other';
  return 'idn';
}

interface VideoRow {
  platform: string | null;
  member_username: string;
  streamer_name: string | null;
  title: string | null;
  started_at: string | null;
  created_at: string | null;
  youtube_video_id: string;
  content_uid: string | null;
  telegram_message_ids: string | null;
  has_tg?: number;
  dur_start: string | null;
  dur_end: string | null;
}

/**
 * Kunci pengelompokan konten di katalog: YouTube ID bila sudah ada (prioritas
 * lama & web_publications), selain itu `content_uid` (arsip TG-first, YT belum).
 * Baris tanpa keduanya tidak lolos WHERE — aman untuk GROUP BY.
 */
const CONTENT_KEY_SQL = `COALESCE(NULLIF(ls.youtube_video_id, ''), ls.content_uid)`;

export function getAllVideos(options: {
  search?: string;
  member?: string;
  platform?: string;
  page?: number;
  limit?: number;
}): { videos: VideoItem[]; total: number; page: number; totalPages: number } {
  const db = getDb();
  const page = Math.max(1, Math.min(100000, Math.floor(options.page || 1)));
  const limit = Math.max(1, Math.min(100, options.limit || 24));
  const offset = (page - 1) * limit;

  // `platform` berasal dari kolom bot (2026). Baris lama dan database pra-migrasi
  // tidak punya nilainya, jadi selalu jatuh ke 'idn' — bukan ditebak dari judul.
  let baseQuery = `
    SELECT 
      COALESCE(NULLIF(ls.platform, ''), 'idn') as platform,
      ls.member_username,
      COALESCE(NULLIF(ls.member_name, ''), NULLIF(mh.display_name, ''), ls.member_username) as streamer_name,
      COALESCE(NULLIF(mg.live_title, ''), '') as title,
      MIN(ls.started_at) as started_at,
      MAX(ls.created_at) as created_at,
      ls.youtube_video_id,
      ${CONTENT_KEY_SQL} as content_uid,
      MAX(CASE WHEN ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != '' THEN 1 ELSE 0 END) as has_tg,
      MIN(ls.download_started_at) as dur_start,
      MAX(ls.download_ended_at) as dur_end
    FROM live_sessions ls
    LEFT JOIN merge_groups mg ON ls.merge_group_id = mg.id
    LEFT JOIN member_hls mh ON ls.member_username = mh.username
    WHERE (
      (ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != '')
      OR (
        ls.content_uid IS NOT NULL AND ls.content_uid != ''
        AND ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != ''
      )
    )
      AND ${publicVisibilitySql('ls')}
  `;

  if (options.platform) baseQuery += ` AND COALESCE(NULLIF(ls.platform, ''), 'idn') = ?`;

  const params: (string | number)[] = [];

  if (options.platform) params.push(options.platform.toLowerCase());

  if (options.member) {
    baseQuery += ` AND LOWER(ls.member_username) = ?`;
    params.push(options.member.toLowerCase());
  }

  if (options.search) {
    // Tanggal yang terlihat di judul ("18 September 2026") dibentuk belakangan
    // dari started_at, jadi kata kunci tanggal tidak akan cocok ke kolom teks.
    // Karena itu tanggal diekstrak dulu dari kata kunci lalu dicocokkan ke
    // `date(..., 'unixepoch', '+7 hours')` — artinya "tanggal berapa di WIB saat
    // live MULAI" — persis tanggal yang dirender buildDisplayTitle. started_at
    // ditulis bot sebagai jam dinding server ATAU UTC berpenanda, jadi kedua
    // tafsir dicoba (tanpa 'unixepoch' ATAU dengan 'unixepoch').
    const dateKeys = extractSearchDates(options.search);
    // Dua tafsir supaya cocok dengan TANGGAL YANG TAMPIL DI KARTU (lib/wib.ts):
    //  - server UTC  → tanggal tampil = waktu tersimpan + 7 jam
    //  - server WIB  → tanggal tampil = waktu tersimpan apa adanya
    // Ini penting di sekitar tengah malam WIB (mis. 23:06 UTC = 20 Sep WIB).
    const wallClock = `COALESCE(ls.started_at, ls.created_at)`;
    const placeholders = dateKeys.map(() => '?').join(', ');
    const dateCond = dateKeys.length
      ? ` OR strftime('%Y-%m', ${wallClock}) IN (${placeholders})
         OR date(${wallClock}) IN (${placeholders})
         OR strftime('%Y-%m', ${wallClock}, '+7 hours') IN (${placeholders})
         OR date(${wallClock}, '+7 hours') IN (${placeholders})`
      : '';
    baseQuery += ` AND (
      LOWER(ls.member_username) LIKE ?
      OR LOWER(ls.member_name) LIKE ?
      OR LOWER(mg.live_title) LIKE ?
      OR LOWER(mh.display_name) LIKE ?${dateCond}
    )`;
    const s = `%${options.search.toLowerCase()}%`;
    params.push(s, s, s, s);
    for (let i = 0; i < 4; i++) params.push(...dateKeys);
  }

  baseQuery += ` GROUP BY ${CONTENT_KEY_SQL}`;

  // Count total distinct
  const countSql = `SELECT COUNT(*) as total FROM (${baseQuery})`;
  const countStmt = db.prepare(countSql);
  const totalRow = countStmt.get(...params) as { total: number };
  const total = totalRow ? totalRow.total : 0;

  // Order & paginate
  const dataSql = `${baseQuery} ORDER BY created_at DESC LIMIT ? OFFSET ?`;
  const dataStmt = db.prepare(dataSql);
  const rows = dataStmt.all(...params, limit, offset) as unknown as VideoRow[];

  const videos: VideoItem[] = rows.map((r) => {
    const dispName = cleanDisplayName(r.member_username, r.streamer_name || undefined);
    const startedAt = r.started_at || r.created_at || '';
    const platform = normalizePlatform(r.platform);
    const title = buildDisplayTitle(platform, dispName, startedAt);
    const ytId = (r.youtube_video_id || '').trim();
    const contentUid = (r.content_uid || '').trim();
    const durSec = durationFromRange(r.dur_start, r.dur_end);
    const watchKey = ytId || contentUid;
    return {
      id: ytId || contentUid,
      platform: platform,
      streamer_username: r.member_username,
      streamer_name: dispName,
      title: title,
      started_at: startedAt,
      date_display: formatWibCardDate(startedAt),
      duration_seconds: durSec,
      duration_formatted: durSec > 0 ? formatDuration(durSec) : '',
      youtube_video_id: ytId,
      content_uid: contentUid,
      watch_id: ytId ? encodeWatchId(ytId) : watchKey,
      thumbnail_url: ytId
        ? `https://img.youtube.com/vi/${ytId}/hqdefault.jpg`
        : '',
      created_at: r.created_at || '',
      telegram_archived: !!r.has_tg,
      is_new: isRecent(startedAt, 24),
    };
  });

  return {
    videos,
    total,
    page,
    totalPages: Math.ceil(total / limit) || 1,
  };
}

/** Opsi pemilihan rekaman pra-rilis (belum lewat ambang auto-publish). */
export interface UpcomingOptions {
  /**
   * Batas jumlah item. `<= 0` (default) = SEMUA rekaman pra-rilis. Jangan
   * dibatasi diam-diam: item yang terpotong itulah yang dianggap "hilang"
   * dari grid oleh pengunjung.
   */
  limit?: number;
  /** Saring per member (username) agar pra-rilis tetap tampil saat difilter. */
  member?: string;
  /** Saring per platform ('idn'/'showroom'); kosong = semua platform. */
  platform?: string;
}

/**
 * Rekaman yang AKAN terbit (pra-rilis): sudah ter-upload ke YouTube, belum lewat
 * ambang auto-publish, dan TIDAK ditahan admin. Dipakai untuk menggabungkan kartu
 * "Segera" ke grid utama home agar pengunjung bisa menemukan halaman countdown-nya.
 *
 * Aturan:
 *  - SEMUA rekaman pra-rilis dikembalikan (tanpa batas bawaan). Versi sebelumnya
 *    hanya mengambil 6 item terurut `publish_at ASC`, sehingga rekaman pra-rilis
 *    TERBARU tidak pernah muncul di grid.
 *  - Showroom hanya ikut bila ambangnya > 0. Ambang 0 (default) = Showroom langsung
 *    terbit, ambang negatif = wajib persetujuan admin — keduanya bukan pra-rilis
 *    terjadwal, jadi tidak ditampilkan sebagai "Segera hadir" berjadwal.
 */
export function getUpcomingVideos(options: UpcomingOptions = {}): VideoItem[] {
  const { limit = 0, member, platform } = options;
  const hours = AUTO_PUBLISH_AFTER_HOURS;
  if (hours <= 0) return []; // rilis otomatis IDN nonaktif → tidak ada jadwal rilis
  const showroomHours = AUTO_PUBLISH_AFTER_HOURS_SHOWROOM > 0 ? AUTO_PUBLISH_AFTER_HOURS_SHOWROOM : 0;
  const wantedPlatform = (platform || '').trim().toLowerCase();
  if (wantedPlatform === 'showroom' && showroomHours <= 0) return [];

  const conditions = [
    // Pra-rilis: YT sudah ada ATAU arsip TG siap (content_uid terisi) — konten
    // TG-first tetap bisa tampil sebagai "Segera" sebelum YouTube ready.
    `(
      (ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != '')
      OR (
        ls.content_uid IS NOT NULL AND ls.content_uid != ''
        AND ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != ''
      )
    )`,
    // Ditahan admin (published = 0) / terbit manual (published = 1) bukan pra-rilis.
    // Baik keputusan publications (YT) maupun override /admin/videos (content_key).
    `NOT EXISTS (SELECT 1 FROM web_publications pw WHERE pw.youtube_video_id = ls.youtube_video_id AND ls.youtube_video_id != '' AND pw.published = 0)`,
    `NOT EXISTS (SELECT 1 FROM web_publications po WHERE po.youtube_video_id = ls.youtube_video_id AND ls.youtube_video_id != '' AND po.published = 1)`,
    `NOT EXISTS (SELECT 1 FROM web_video_overrides vw WHERE vw.content_key = COALESCE(NULLIF(ls.youtube_video_id, ''), ls.content_uid) AND vw.content_key != '' AND vw.published = 0)`,
    `NOT EXISTS (SELECT 1 FROM web_video_overrides vo2 WHERE vo2.content_key = COALESCE(NULLIF(ls.youtube_video_id, ''), ls.content_uid) AND vo2.content_key != '' AND vo2.published = 1)`,
  ];
  const params: (string | number)[] = [];
  if (showroomHours <= 0) conditions.push(`COALESCE(ls.platform, 'idn') != 'showroom'`);
  if (wantedPlatform) {
    conditions.push(`COALESCE(NULLIF(ls.platform, ''), 'idn') = ?`);
    params.push(wantedPlatform);
  }
  if (member) {
    conditions.push(`LOWER(ls.member_username) = ?`);
    params.push(member.toLowerCase());
  }
  // limit <= 0 → LIMIT -1 (tanpa batas atas) di SQLite.
  const rowLimit = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : -1;

  const db = getDb();
  // Waktu acuan rilis = waktu TERAKHIR segmen selesai di seluruh grup (bukan per
  // baris), agar live panjang yang tersimpan sebagai banyak segmen dinilai sebagai
  // satu video. "Akan terbit" = belum visible (publicVisibilitySql=false) DAN
  // publish_at masih di masa depan. Terbaru lebih dulu supaya bila pemanggil
  // membatasi jumlah, yang terambil adalah rekaman pra-rilis paling baru.
  const rows = db.prepare(`
    SELECT
      COALESCE(NULLIF(ls.platform, ''), 'idn') as platform,
      ls.member_username,
      COALESCE(NULLIF(ls.member_name, ''), NULLIF(mh.display_name, ''), ls.member_username) as streamer_name,
      COALESCE(NULLIF(mg.live_title, ''), '') as title,
      MIN(ls.started_at) as started_at,
      MAX(ls.created_at) as created_at,
      ls.youtube_video_id,
      ${CONTENT_KEY_SQL} as content_uid,
      MAX(CASE WHEN ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != '' THEN 1 ELSE 0 END) as has_tg,
      MAX(COALESCE(ls.download_ended_at, ls.created_at)) as last_end,
      CASE
        WHEN COALESCE(ls.platform, 'idn') = 'showroom'
          THEN datetime(MAX(COALESCE(ls.download_ended_at, ls.created_at)), '+${showroomHours} hours')
        ELSE datetime(MAX(COALESCE(ls.download_ended_at, ls.created_at)), '+${hours} hours')
      END as publish_at
    FROM live_sessions ls
    LEFT JOIN merge_groups mg ON ls.merge_group_id = mg.id
    LEFT JOIN member_hls mh ON ls.member_username = mh.username
    WHERE ${conditions.join('\n      AND ')}
    GROUP BY ${CONTENT_KEY_SQL}
    HAVING (julianday('now') - julianday(MAX(COALESCE(ls.download_ended_at, ls.created_at)))) * 24
      < CASE WHEN COALESCE(ls.platform, 'idn') = 'showroom' THEN ${showroomHours} ELSE ${hours} END
    ORDER BY last_end DESC
    LIMIT ?
  `).all(...params, rowLimit) as unknown as Array<VideoRow & { publish_at: string | null }>;

  return rows.map((r) => {
    const dispName = cleanDisplayName(r.member_username, r.streamer_name || undefined);
    const startedAt = r.started_at || r.created_at || '';
    const platform = normalizePlatform(r.platform);
    const title = buildDisplayTitle(platform, dispName, startedAt);
    const ytId = (r.youtube_video_id || '').trim();
    const contentUid = (r.content_uid || '').trim();
    const watchKey = ytId || contentUid;
    return {
      id: ytId || contentUid,
      platform,
      streamer_username: r.member_username,
      streamer_name: dispName,
      title,
      started_at: startedAt,
      date_display: formatWibCardDate(startedAt),
      duration_seconds: 0,
      duration_formatted: '',
      youtube_video_id: ytId,
      content_uid: contentUid,
      watch_id: ytId ? encodeWatchId(ytId) : watchKey,
      thumbnail_url: ytId
        ? `https://img.youtube.com/vi/${ytId}/hqdefault.jpg`
        : '',
      created_at: r.created_at || '',
      telegram_archived: !!r.has_tg,
      is_visible: false,
      publish_at: r.publish_at ? r.publish_at.replace(' ', 'T') + 'Z' : '',
    };
  });
}

interface VideoDetailRow {
  platform: string | null;
  member_username: string;
  streamer_name: string | null;
  title: string | null;
  started_at: string | null;
  created_at: string | null;
  download_started_at: string | null;
  download_ended_at: string | null;
  youtube_video_id: string | null;
  content_uid: string | null;
  telegram_message_ids: string | null;
  is_visible: number;
  publish_at: string | null;
}

export function getVideoById(videoIdOrLiveId: string): VideoItem | null {
  const db = getDb();
  // Terima ID tersamar (Base64URL) maupun ID mentah; decode dulu agar lookup
  // selalu memakai youtube_video_id asli. live_id / numeric id tetap didukung.
  const decoded = decodeWatchId(videoIdOrLiveId);
  const lookupId = decoded || videoIdOrLiveId;
  // Ambang jam per platform (Showroom langsung = 0). Waktu acuan download_ended_at
  // (UTC), jatuh ke created_at. publish_at = waktu acuan + ambang (diubah ke ISO UTC
  // dengan datetime(...)). Rekaman yang DITAHAN manual (published=0) tidak dikembalikan.
  // Showroom hanya punya jadwal rilis bila ambangnya > 0 — dengan ambang 0 (langsung
  // terbit) atau negatif (wajib persetujuan admin) tidak ada tanggal rilis.
  const idnHours = AUTO_PUBLISH_AFTER_HOURS > 0 ? AUTO_PUBLISH_AFTER_HOURS : 0;
  const showroomHours = AUTO_PUBLISH_AFTER_HOURS_SHOWROOM > 0 ? AUTO_PUBLISH_AFTER_HOURS_SHOWROOM : 0;
  const publishAtSql = (hours: number) => `datetime(COALESCE(ls.download_ended_at, ls.created_at), '+${hours} hours')`;
  const sql = `
    SELECT
      COALESCE(NULLIF(ls.platform, ''), 'idn') as platform,
      ls.member_username,
      COALESCE(NULLIF(ls.member_name, ''), NULLIF(mh.display_name, ''), ls.member_username) as streamer_name,
      COALESCE(NULLIF(mg.live_title, ''), '') as title,
      ls.started_at,
      ls.created_at,
      ls.download_started_at,
      ls.download_ended_at,
      ls.youtube_video_id,
      ls.content_uid,
      ls.telegram_message_ids,
      (${publicVisibilitySql('ls')}) as is_visible,
      CASE
        WHEN COALESCE(ls.platform, 'idn') = 'showroom' THEN ${showroomHours > 0 ? publishAtSql(showroomHours) : 'NULL'}
        WHEN ${idnHours} <= 0 THEN NULL
        ELSE ${publishAtSql(idnHours)}
      END as publish_at
    FROM live_sessions ls
    LEFT JOIN merge_groups mg ON ls.merge_group_id = mg.id
    LEFT JOIN member_hls mh ON ls.member_username = mh.username
    WHERE (ls.youtube_video_id = ? OR ls.live_id = ? OR ls.id = ? OR ls.content_uid = ?)
      AND NOT EXISTS (
        SELECT 1 FROM web_publications pw
        WHERE pw.youtube_video_id = ls.youtube_video_id AND ls.youtube_video_id != '' AND pw.published = 0
      )
      AND NOT EXISTS (
        SELECT 1 FROM web_video_overrides wo
        WHERE wo.content_key = COALESCE(NULLIF(ls.youtube_video_id, ''), ls.content_uid)
          AND wo.content_key != '' AND wo.published = 0
      )
    ORDER BY ls.id DESC
    LIMIT 1
  `;
  const stmt = db.prepare(sql);
  const r = stmt.get(lookupId, videoIdOrLiveId, videoIdOrLiveId, lookupId) as unknown as VideoDetailRow | undefined;
  if (!r) return null;

  const dispName = cleanDisplayName(r.member_username, r.streamer_name || undefined);
  const startedAt = r.started_at || r.created_at || '';
  const platform = normalizePlatform(r.platform);
  const title = buildDisplayTitle(platform, dispName, startedAt);
  const ytId = (r.youtube_video_id || '').trim();
  const contentUid = (r.content_uid || '').trim();
  const durSec = durationFromRange(r.download_started_at, r.download_ended_at);

  // publish_at dari datetime() berbentuk "YYYY-MM-DD HH:MM:SS" (UTC) — tambahkan 'Z'
  // agar diparse sebagai UTC, bukan waktu lokal browser.
  const publishAt = r.publish_at ? r.publish_at.replace(' ', 'T') + 'Z' : '';

  return {
    id: ytId || contentUid,
    platform: platform,
    streamer_username: r.member_username,
    streamer_name: dispName,
    title: title,
    started_at: startedAt,
    date_display: formatWibCardDate(startedAt),
    duration_seconds: durSec,
    duration_formatted: durSec > 0 ? formatDuration(durSec) : '',
    youtube_video_id: ytId,
    content_uid: contentUid,
    watch_id: ytId ? encodeWatchId(ytId) : contentUid,
    thumbnail_url: ytId ? `https://img.youtube.com/vi/${ytId}/maxresdefault.jpg` : '',
    created_at: r.created_at || '',
    telegram_archived: !!(r.telegram_message_ids && r.telegram_message_ids.trim()),
    is_new: isRecent(startedAt, 24),
    is_visible: !!r.is_visible,
    publish_at: publishAt,
  };
}

export interface PublicMember {
  username: string;
  display_name: string;
  video_count: number;
  /** Foto member (roster jkt48.com / kolom bot); null bila belum ada. */
  avatar_url: string | null;
}

interface PublicMemberRow {
  username: string;
  display_name: string;
  video_count: number;
  avatar_url: string | null;
}

// Foto roster di-load lewat lib/member-photos (fallback bila bot belum menautkan
// akun TikTok). Impor di sini agar modul db tetap satu pintu untuk halaman publik.
import { rosterPhotoFor } from './member-photos';

function withAvatar(row: PublicMemberRow): PublicMember {
  const display_name = cleanDisplayName(row.username, row.display_name);
  const avatar_url = (row.avatar_url || '').trim() || rosterPhotoFor(row.username, display_name);
  return { username: row.username, display_name, video_count: row.video_count, avatar_url: avatar_url || null };
}

export function getPublicMembers(): PublicMember[] {
  const rows = getDb().prepare(`SELECT ls.member_username AS username,
    COALESCE(NULLIF(mh.display_name, ''), ls.member_username) AS display_name,
    COUNT(DISTINCT ${CONTENT_KEY_SQL}) AS video_count,
    NULLIF(ta.avatar_url, '') AS avatar_url
    FROM live_sessions ls
    LEFT JOIN member_hls mh ON mh.username = ls.member_username
    LEFT JOIN tiktok_accounts ta ON ta.member_username = ls.member_username AND ta.enabled = 1
    WHERE (
      (ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != '')
      OR (
        ls.content_uid IS NOT NULL AND ls.content_uid != ''
        AND ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != ''
      )
    )
      AND ${publicVisibilitySql('ls')}
    GROUP BY ls.member_username ORDER BY display_name COLLATE NOCASE`).all() as unknown as PublicMemberRow[];
  return rows.map(withAvatar);
}

/** Satu member publik berdasarkan username (null bila tidak ada di arsip). */
export function getPublicMember(username: string): PublicMember | null {
  const wanted = (username || '').trim().toLowerCase();
  if (!wanted) return null;
  const hit = getPublicMembers().find((m) => m.username.toLowerCase() === wanted);
  if (hit) return hit;
  // Member yang saat ini hanya punya rekaman pra-rilis (belum lolos ambang)
  // tidak ikut getPublicMembers — cek lewat upcoming agar halaman detail tetap 200.
  const upcoming = getUpcomingVideos({ member: wanted });
  if (!upcoming.length) return null;
  const display_name = upcoming[0].streamer_name;
  const avatar_url = rosterPhotoFor(wanted, display_name);
  return { username: upcoming[0].streamer_username, display_name, video_count: 0, avatar_url };
}

export interface Publication {
  youtube_video_id: string;
  title: string;
  member_name: string;
  /** Sumber rekaman: 'idn' | 'showroom' | ... (baris lama = 'idn') */
  platform: string;
  /** 1 = admin menerbitkan eksplisit, 0 = admin menahan eksplisit */
  published: number;
  /** 1 = admin pernah memutuskan (terbit atau tahan) */
  decided: number;
  /** 1 = saat ini tampil di halaman publik (hasil aturan lengkap) */
  visible: number;
  /** Jam sejak live selesai; null bila waktunya tidak terbaca */
  hours_since_end: number | null;
}

export type PublicationStatusFilter = 'all' | 'waiting' | 'visible' | 'held';

export interface PublicationSummary {
  total: number;
  waiting: number;
  visible: number;
  held: number;
}

const PUBLICATION_FILTER_SQL: Record<PublicationStatusFilter, string> = {
  all: '',
  // Alias kolom hasil GROUP BY hanya sah di HAVING (bukan WHERE).
  waiting: 'HAVING decided = 0 AND visible = 0',
  visible: 'HAVING visible = 1',
  held: 'HAVING decided = 1 AND published = 0',
};

function publicationBaseSql(search: string): { sql: string; params: string[] } {
  const filter = `%${search.slice(0, 100)}%`;
  return {
    sql: `SELECT ls.youtube_video_id,
      COALESCE(NULLIF(MAX(ls.member_name), ''), ls.member_username) AS member_name,
      COALESCE(MAX(mh.display_name), '') AS display_name,
      ls.member_username AS member_username,
      COALESCE(NULLIF(MAX(ls.platform), ''), 'idn') AS platform,
      MIN(ls.started_at) AS started_at,
      MAX(COALESCE(ls.created_at, ls.started_at)) AS sort_at,
      COALESCE(MAX(p.published), 0) AS published,
      CASE WHEN MAX(p.youtube_video_id) IS NULL THEN 0 ELSE 1 END AS decided,
      MAX(${publicVisibilitySql('ls')}) AS visible,
      MAX((julianday('now') - julianday(COALESCE(ls.download_ended_at, ls.created_at))) * 24) AS hours_since_end
      FROM live_sessions ls
      LEFT JOIN merge_groups mg ON mg.id = ls.merge_group_id
      LEFT JOIN member_hls mh ON ls.member_username = mh.username
      LEFT JOIN web_publications p ON p.youtube_video_id = ls.youtube_video_id
      WHERE ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != ''
      AND (ls.member_username LIKE ? OR ls.member_name LIKE ? OR mg.live_title LIKE ? OR mh.display_name LIKE ?)
      GROUP BY ls.youtube_video_id`,
    params: [filter, filter, filter, filter],
  };
}

export function getPublications(
  search = '',
  page = 1,
  status: PublicationStatusFilter = 'all',
): { videos: Publication[]; hasMore: boolean; summary: PublicationSummary } {
  const base = publicationBaseSql(search);
  const where = PUBLICATION_FILTER_SQL[status] || '';
  const rows = getDb().prepare(`${base.sql} ${where} ORDER BY sort_at DESC LIMIT 51 OFFSET ?`)
    .all(...base.params, (page - 1) * 50) as unknown as (Publication & {
      member_username: string;
      display_name: string;
      platform: string;
      started_at: string | null;
      sort_at: string | null;
    })[];
  const videos: Publication[] = rows.map((row) => ({
    ...row,
    title: buildDisplayTitle(
      row.platform,
      cleanDisplayName(row.member_username, row.display_name || row.member_name || undefined),
      row.started_at || '',
    ),
  }));

  const summaryRows = getDb().prepare(`SELECT
    COUNT(*) AS total,
    COALESCE(SUM(CASE WHEN decided = 0 AND visible = 0 THEN 1 ELSE 0 END), 0) AS waiting,
    COALESCE(SUM(CASE WHEN visible = 1 THEN 1 ELSE 0 END), 0) AS visible,
    COALESCE(SUM(CASE WHEN decided = 1 AND published = 0 THEN 1 ELSE 0 END), 0) AS held
    FROM (${base.sql})`).get(...base.params) as unknown as PublicationSummary;

  return {
    videos: videos.slice(0, 50),
    hasMore: rows.length > 50,
    summary: {
      total: summaryRows?.total || 0,
      waiting: summaryRows?.waiting || 0,
      visible: summaryRows?.visible || 0,
      held: summaryRows?.held || 0,
    },
  };
}

export function setPublication(videoId: string, published: boolean): boolean {
  const result = getDb().prepare(`INSERT INTO web_publications (youtube_video_id, published)
    SELECT ?, ? WHERE EXISTS (SELECT 1 FROM live_sessions WHERE youtube_video_id = ?)
    ON CONFLICT(youtube_video_id) DO UPDATE SET published = excluded.published, updated_at = datetime('now')`)
    .run(videoId, published ? 1 : 0, videoId);
  return result.changes > 0;
}

interface StreamerRow {
  username: string;
  display_name: string | null;
  enabled: number | null;
  hls_confirmed: number | null;
  last_live_at: string | null;
  video_count: number | null;
  total_sessions: number | null;
}

export interface Streamer {
  username: string;
  display_name: string;
  enabled: boolean;
  hls_confirmed: boolean;
  last_live_at: string | null;
  video_count: number;
  total_sessions: number;
}

export function getStreamersList(): Streamer[] {
  const db = getDb();
  const sql = `
    SELECT 
      mh.username,
      mh.display_name,
      mh.enabled,
      mh.hls_confirmed,
      mh.last_live_at,
      COUNT(DISTINCT ls.youtube_video_id) as video_count,
      COUNT(DISTINCT ls.id) as total_sessions
    FROM member_hls mh
    LEFT JOIN live_sessions ls ON LOWER(ls.member_username) = LOWER(mh.username) AND ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != ''
    GROUP BY mh.username
    ORDER BY mh.enabled DESC, video_count DESC, mh.display_name ASC
  `;
  const stmt = db.prepare(sql);
  const rows = stmt.all() as unknown as StreamerRow[];

  return rows.map((r) => ({
    username: r.username,
    display_name: cleanDisplayName(r.username, r.display_name || undefined),
    enabled: Boolean(r.enabled),
    hls_confirmed: Boolean(r.hls_confirmed),
    last_live_at: r.last_live_at,
    video_count: r.video_count || 0,
    total_sessions: r.total_sessions || 0,
  }));
}

interface ActiveSessionRow {
  id: number;
  live_id: string;
  member_username: string;
  member_name: string | null;
  status: string;
  platform: string;
  error_message: string | null;
}

interface YouTubeChannelRow {
  id: number;
  channel_label: string;
  uploads_today: number | null;
  last_reset_at: string | null;
}

export interface ActiveSession extends ActiveSessionRow {
  display_name: string;
}

export interface YouTubeChannelStat {
  id: number;
  label: string;
  uploads_today: number;
  quota_limit: number;
  last_reset: string | null;
}

export interface SystemStats {
  bot_status: 'recording' | 'idle';
  active_sessions: ActiveSession[];
  pending_uploads_count: number;
  total_archived_videos: number;
  members_count: { total: number; active: number };
  youtube_channels: YouTubeChannelStat[];
  tiktok: TikTokAdminStats;
}

export function getSystemStats(): SystemStats {
  const db = getDb();

  // Sesi masih berjalan — termasuk uploading_telegram (sama dengan bot/status.py).
  const activeStmt = db.prepare(`
    SELECT id, live_id, member_username, member_name, status,
           COALESCE(NULLIF(platform, ''), 'idn') AS platform,
           error_message
    FROM live_sessions
    WHERE status IN (
      'detected', 'downloading', 'segment_done', 'download_complete',
      'merging', 'uploading_telegram', 'uploading_youtube', 'pending_upload'
    )
    ORDER BY created_at DESC
    LIMIT 50
  `);
  const activeSessions = activeStmt.all() as unknown as ActiveSessionRow[];

  // Pending upload (retry queue)
  const pendingStmt = db.prepare(`
    SELECT COUNT(*) as count FROM live_sessions WHERE status = 'pending_upload'
  `);
  const pendingRow = pendingStmt.get() as { count: number };

  // Total archived youtube videos
  const totalVideosStmt = db.prepare(`
    SELECT COUNT(DISTINCT youtube_video_id) as count FROM live_sessions WHERE youtube_video_id IS NOT NULL AND youtube_video_id != ''
  `);
  const totalVideos = (totalVideosStmt.get() as { count: number })?.count || 0;

  // Total members monitored
  const totalMembersStmt = db.prepare(`
    SELECT
      COUNT(*) as total,
      SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) as active
    FROM member_hls
  `);
  const membersRow = totalMembersStmt.get() as { total: number; active: number };

  // YouTube channels status
  let ytChannels: YouTubeChannelRow[] = [];
  try {
    const ytStmt = db.prepare(`
      SELECT id, channel_label, uploads_today, last_reset_at FROM youtube_channels ORDER BY uploads_today ASC, id ASC
    `);
    ytChannels = ytStmt.all() as unknown as YouTubeChannelRow[];
  } catch {
    ytChannels = [];
  }

  return {
    bot_status: activeSessions.length > 0 ? 'recording' : 'idle',
    active_sessions: activeSessions.map((s) => ({
      ...s,
      display_name: cleanDisplayName(s.member_username, s.member_name || undefined),
    })),
    pending_uploads_count: pendingRow ? pendingRow.count : 0,
    total_archived_videos: totalVideos,
    members_count: {
      total: membersRow ? membersRow.total : 0,
      active: membersRow ? membersRow.active : 0,
    },
    youtube_channels: ytChannels.map((c) => ({
      id: c.id,
      label: c.channel_label,
      uploads_today: c.uploads_today || 0,
      quota_limit: 6, // approximate safe daily video upload limit
      last_reset: c.last_reset_at,
    })),
    tiktok: getTikTokAdminStats(),
  };
}

export function setMemberEnabled(username: string, enabled: boolean): boolean {
  const db = getDb();
  const stmt = db.prepare(`
    UPDATE member_hls SET enabled = ? WHERE LOWER(username) = LOWER(?)
  `);
  const result = stmt.run(enabled ? 1 : 0, username.trim().toLowerCase());
  return result.changes > 0;
}

export function addMember(username: string, displayName: string): boolean {
  const db = getDb();
  const cleanUser = username.trim().toLowerCase();
  const cleanName = displayName.trim() || cleanDisplayName(cleanUser);
  const stmt = db.prepare(`
    INSERT INTO member_hls (username, display_name, enabled, hls_confirmed)
    VALUES (?, ?, 1, 0)
    ON CONFLICT(username) DO UPDATE SET
      display_name = excluded.display_name,
      enabled = 1
  `);
  const result = stmt.run(cleanUser, cleanName);
  return result.changes > 0;
}

// ─── Admin: daftar video + soft-hide (/admin/videos) ─────────────────────────

export interface AdminVideo {
  content_key: string;
  youtube_video_id: string;
  content_uid: string;
  platform: string;
  member_username: string;
  member_name: string;
  title: string;
  started_at: string | null;
  created_at: string | null;
  has_tg: number;
  has_yt: number;
  visible: number;
  /** null = tanpa override; 0 = disembunyikan admin; 1 = diterbitkan admin. */
  override: number | null;
  hours_since_end: number | null;
  row_count: number;
}

/**
 * Semua konten untuk tabel admin /admin/videos — termasuk yang tersembunyi
 * dan arsip TG-first tanpa youtube_video_id. Digroup per content_key.
 */
export function getAdminVideos(
  search = '',
  page = 1,
  visibility: 'all' | 'visible' | 'hidden' = 'all',
): { videos: AdminVideo[]; hasMore: boolean } {
  const db = getDb();
  const filter = `%${search.slice(0, 100)}%`;
  const keyExpr = CONTENT_KEY_SQL;
  const rows = db.prepare(`
    SELECT
      ${keyExpr} AS content_key,
      MAX(ls.youtube_video_id) AS youtube_video_id,
      MAX(COALESCE(NULLIF(ls.content_uid, ''), '')) AS content_uid,
      COALESCE(NULLIF(MAX(ls.platform), ''), 'idn') AS platform,
      ls.member_username,
      COALESCE(NULLIF(MAX(ls.member_name), ''), mh.display_name, ls.member_username) AS member_name,
      COALESCE(NULLIF(MAX(mg.live_title), ''), '') AS live_title,
      MIN(ls.started_at) AS started_at,
      MAX(ls.created_at) AS created_at,
      MAX(CASE WHEN ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != '' THEN 1 ELSE 0 END) AS has_tg,
      MAX(CASE WHEN ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != '' THEN 1 ELSE 0 END) AS has_yt,
      MAX(CASE WHEN vo.published IS NULL THEN NULL ELSE vo.published END) AS override,
      MAX(${publicVisibilitySql('ls')}) AS visible,
      MAX((julianday('now') - julianday(COALESCE(ls.download_ended_at, ls.created_at))) * 24) AS hours_since_end,
      COUNT(*) AS row_count
    FROM live_sessions ls
    LEFT JOIN merge_groups mg ON mg.id = ls.merge_group_id
    LEFT JOIN member_hls mh ON mh.username = ls.member_username
    LEFT JOIN web_video_overrides vo ON vo.content_key = ${keyExpr}
    WHERE (
      (ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != '')
      OR (
        ls.content_uid IS NOT NULL AND ls.content_uid != ''
        AND ls.telegram_message_ids IS NOT NULL AND ls.telegram_message_ids != ''
      )
    )
    AND (ls.member_username LIKE ? OR ls.member_name LIKE ? OR mg.live_title LIKE ? OR mh.display_name LIKE ? OR ${keyExpr} LIKE ?)
    GROUP BY ${keyExpr}
    HAVING (${visibility === 'visible' ? 'visible = 1' : visibility === 'hidden' ? 'visible = 0' : '1 = 1'})
    ORDER BY MAX(COALESCE(ls.download_ended_at, ls.created_at)) DESC
    LIMIT 51 OFFSET ?
  `).all(filter, filter, filter, filter, filter, (page - 1) * 50) as unknown as Array<{
    content_key: string | null;
    youtube_video_id: string | null;
    content_uid: string | null;
    platform: string;
    member_username: string;
    member_name: string | null;
    live_title: string;
    started_at: string | null;
    created_at: string | null;
    has_tg: number;
    has_yt: number;
    override: number | null;
    visible: number;
    hours_since_end: number | null;
    row_count: number;
  }>;

  const videos: AdminVideo[] = rows.slice(0, 50).map((row) => {
    const ytId = (row.youtube_video_id || '').trim();
    const contentUid = (row.content_uid || '').trim();
    const platform = normalizePlatform(row.platform);
    const displayName = cleanDisplayName(row.member_username, row.member_name || undefined);
    const title = row.live_title
      || buildDisplayTitle(platform, displayName, row.started_at || row.created_at || '');
    return {
      content_key: row.content_key || ytId || contentUid,
      youtube_video_id: ytId,
      content_uid: contentUid,
      platform: row.platform,
      member_username: row.member_username,
      member_name: displayName,
      title,
      started_at: row.started_at,
      created_at: row.created_at,
      has_tg: row.has_tg ? 1 : 0,
      has_yt: row.has_yt ? 1 : 0,
      visible: row.visible ? 1 : 0,
      override: row.override,
      hours_since_end: row.hours_since_end,
      row_count: row.row_count,
    };
  });
  return { videos, hasMore: rows.length > 50 };
}

/**
 * Soft-hide / tampilkan konten di situs publik (/admin/videos).
 * Menulis web_video_overrides pada content_key — berlaku untuk YouTube
 * maupun arsip TG-first, dan menang atas aturan auto-publish.
 */
export function setContentVisibility(contentKey: string, published: boolean): boolean {
  const key = (contentKey || '').trim();
  if (!key || key.length > 120) return false;
  const db = getDb();
  const exists = db.prepare(`
    SELECT 1 FROM live_sessions
    WHERE youtube_video_id = ? OR content_uid = ?
    LIMIT 1
  `).get(key, key);
  if (!exists) return false;
  db.prepare(`
    INSERT INTO web_video_overrides (content_key, published, updated_at)
    VALUES (?, ?, datetime('now'))
    ON CONFLICT(content_key) DO UPDATE SET
      published = excluded.published,
      updated_at = datetime('now')
  `).run(key, published ? 1 : 0);
  return true;
}

/** Hapus override admin → konten kembali mengikuti aturan publications/auto. */
export function clearContentVisibility(contentKey: string): boolean {
  const key = (contentKey || '').trim();
  if (!key) return false;
  const result = getDb()
    .prepare(`DELETE FROM web_video_overrides WHERE content_key = ?`)
    .run(key);
  return result.changes > 0;
}

// ─── Admin: antrean terpadu (/admin/queue) ───────────────────────────────────

export type QueueSource = 'live' | 'tiktok';

export interface QueueItem {
  source: QueueSource;
  id: string;
  content_key: string;
  platform: string;
  member_username: string;
  member_name: string;
  title: string;
  status: string;
  /** Tujuan pemrosesan saat ini / terakhir: youtube | telegram | download | merge */
  destination: string;
  error_message: string | null;
  file_size_bytes: number;
  has_yt: number;
  has_tg: number;
  youtube_video_id: string;
  kind: string;
  created_at: string | null;
  age_hours: number | null;
}

export interface QueueSummary {
  live_total: number;
  live_by_status: Record<string, number>;
  live_by_platform: Record<string, number>;
  tiktok_total: number;
  tiktok_by_status: Record<string, number>;
  tiktok_yt_backlog: number;
}

/** Status live_sessions yang dianggap "masih dalam alur kerja" untuk antrean. */
const QUEUE_LIVE_STATUSES = [
  'detected', 'downloading', 'segment_done', 'download_complete',
  'merging', 'uploading_telegram', 'uploading_youtube',
  'pending_upload', 'failed',
] as const;

/** Status tiktok_posts yang masih diproses / menunggu / gagal. */
const QUEUE_TIKTOK_STATUSES = [
  'detected', 'downloading', 'uploading_telegram', 'uploading_youtube',
  'pending_upload', 'failed',
] as const;

function inferLiveDestination(status: string, hasYt: boolean, hasTg: boolean, error: string): string {
  const err = (error || '').toLowerCase();
  if (status === 'uploading_telegram' || status === 'done_telegram') return 'telegram';
  if (status === 'uploading_youtube' || status === 'done_youtube') return 'youtube';
  if (status === 'detected' || status === 'downloading') return 'download';
  if (status === 'segment_done' || status === 'download_complete' || status === 'merging') return 'merge';
  if (status === 'pending_upload' || status === 'failed') {
    if (err.includes('youtube') || err.includes('quota')) return 'youtube';
    if (err.includes('telegram')) return 'telegram';
    if (hasYt && !hasTg) return 'telegram';
    if (!hasYt) return 'youtube';
    return 'youtube';
  }
  if (hasYt) return 'telegram';
  return 'youtube';
}

/**
 * Antrean terpadu: sesi live (IDN/Showroom) + post TikTok, digroup per
 * konten (file/content_uid) agar segmen merge tidak menduplikasi baris.
 */
export function getAdminQueue(options: {
  source?: 'all' | 'live' | 'tiktok';
  platform?: string;
  status?: string;
  limit?: number;
} = {}): { items: QueueItem[]; summary: QueueSummary } {
  const db = getDb();
  const source = options.source || 'all';
  const platformFilter = (options.platform || '').trim().toLowerCase();
  const statusFilter = (options.status || '').trim();
  const limit = Math.max(1, Math.min(200, options.limit || 100));

  const items: QueueItem[] = [];
  const summary: QueueSummary = {
    live_total: 0,
    live_by_status: {},
    live_by_platform: {},
    tiktok_total: 0,
    tiktok_by_status: {},
    tiktok_yt_backlog: 0,
  };

  if (source === 'all' || source === 'live') {
    const placeholders = QUEUE_LIVE_STATUSES.map(() => '?').join(',');
    let sql = `
      SELECT
        ls.id,
        COALESCE(NULLIF(ls.content_uid, ''), ls.live_id) AS content_key,
        COALESCE(NULLIF(ls.platform, ''), 'idn') AS platform,
        ls.member_username,
        COALESCE(NULLIF(ls.member_name, ''), mh.display_name, ls.member_username) AS member_name,
        COALESCE(NULLIF(mg.live_title, ''), '') AS live_title,
        ls.status,
        ls.error_message,
        ls.file_size_bytes,
        ls.youtube_video_id,
        ls.telegram_message_ids,
        ls.created_at,
        MIN(COALESCE(ls.download_ended_at, ls.created_at)) AS last_end,
        MAX(ls.id) AS max_id
      FROM live_sessions ls
      LEFT JOIN merge_groups mg ON mg.id = ls.merge_group_id
      LEFT JOIN member_hls mh ON mh.username = ls.member_username
      WHERE ls.status IN (${placeholders})
    `;
    const params: (string | number)[] = [...QUEUE_LIVE_STATUSES];
    if (platformFilter && platformFilter !== 'all' && platformFilter !== 'tiktok') {
      sql += ` AND COALESCE(NULLIF(ls.platform, ''), 'idn') = ?`;
      params.push(platformFilter === 'showroom' ? 'showroom' : 'idn');
    }
    if (statusFilter && statusFilter !== 'all') {
      sql += ` AND ls.status = ?`;
      params.push(statusFilter);
    }
    sql += `
      GROUP BY COALESCE(NULLIF(ls.content_uid, ''), ls.live_id)
      ORDER BY last_end DESC
      LIMIT ?
    `;
    params.push(limit);

    const rows = db.prepare(sql).all(...params) as unknown as Array<{
      id: number;
      content_key: string | null;
      platform: string;
      member_username: string;
      member_name: string | null;
      live_title: string;
      status: string;
      error_message: string | null;
      file_size_bytes: number | null;
      youtube_video_id: string | null;
      telegram_message_ids: string | null;
      created_at: string | null;
      last_end: string | null;
    }>;

    for (const r of rows) {
      const hasYt = !!(r.youtube_video_id && r.youtube_video_id.trim());
      const hasTg = !!(r.telegram_message_ids && r.telegram_message_ids.trim());
      const displayName = cleanDisplayName(r.member_username, r.member_name || undefined);
      const platform = normalizePlatform(r.platform);
      const title = r.live_title
        || buildDisplayTitle(platform, displayName, r.last_end || r.created_at || '');
      items.push({
        source: 'live',
        id: String(r.id),
        content_key: r.content_key || '',
        platform: r.platform,
        member_username: r.member_username,
        member_name: displayName,
        title,
        status: r.status,
        destination: inferLiveDestination(r.status, hasYt, hasTg, r.error_message || ''),
        error_message: r.error_message,
        file_size_bytes: r.file_size_bytes || 0,
        has_yt: hasYt ? 1 : 0,
        has_tg: hasTg ? 1 : 0,
        youtube_video_id: (r.youtube_video_id || '').trim(),
        kind: 'live',
        created_at: r.created_at,
        age_hours: hoursSince(r.last_end || r.created_at),
      });
    }

    // Ringkasan live (tanpa filter platform/status agar angka tetap global).
    const sumRows = db.prepare(`
      SELECT status, COALESCE(NULLIF(platform, ''), 'idn') AS platform, COUNT(*) AS c
      FROM live_sessions
      WHERE status IN (${QUEUE_LIVE_STATUSES.map(() => '?').join(',')})
      GROUP BY status, platform
    `).all(...QUEUE_LIVE_STATUSES) as unknown as Array<{ status: string; platform: string; c: number }>;
    for (const r of sumRows) {
      summary.live_total += r.c;
      summary.live_by_status[r.status] = (summary.live_by_status[r.status] || 0) + r.c;
      summary.live_by_platform[r.platform] = (summary.live_by_platform[r.platform] || 0) + r.c;
    }
  }

  if (source === 'all' || source === 'tiktok') {
    const placeholders = QUEUE_TIKTOK_STATUSES.map(() => '?').join(',');
    let sql = `
      SELECT
        p.id, p.unique_id, p.kind, p.is_story, p.title, p.status, p.error_message,
        p.media_size_bytes, p.youtube_video_id, p.telegram_message_ids,
        COALESCE(NULLIF(p.created_at, ''), p.added_at) AS created_at,
        a.display_name AS account_name, a.member_username
      FROM tiktok_posts p
      LEFT JOIN tiktok_accounts a ON a.unique_id = p.unique_id
      WHERE p.status IN (${placeholders})
    `;
    const params: (string | number)[] = [...QUEUE_TIKTOK_STATUSES];
    if (platformFilter && platformFilter !== 'all' && platformFilter !== 'tiktok') {
      // Antrean TikTok tidak punya platform idn/showroom — sembunyikan bila filter live.
      sql += ` AND 1 = 0`;
    }
    if (statusFilter && statusFilter !== 'all') {
      sql += ` AND p.status = ?`;
      params.push(statusFilter);
    }
    sql += ` ORDER BY created_at DESC LIMIT ?`;
    params.push(limit);

    try {
      const rows = db.prepare(sql).all(...params) as unknown as Array<{
        id: string;
        unique_id: string;
        kind: string;
        is_story: number;
        title: string | null;
        status: string;
        error_message: string | null;
        media_size_bytes: number | null;
        youtube_video_id: string | null;
        telegram_message_ids: string | null;
        created_at: string | null;
        account_name: string | null;
        member_username: string | null;
      }>;

      for (const r of rows) {
        const hasYt = !!(r.youtube_video_id && r.youtube_video_id.trim());
        const hasTg = !!(r.telegram_message_ids && r.telegram_message_ids.trim());
        const memberUsername = r.member_username || r.unique_id;
        const displayName = cleanDisplayName(memberUsername, r.account_name || undefined);
        let destination = 'download';
        if (r.status === 'uploading_telegram') destination = 'telegram';
        else if (r.status === 'uploading_youtube') destination = 'youtube';
        else if (r.status === 'pending_upload' || r.status === 'failed') {
          const err = (r.error_message || '').toLowerCase();
          if (err.includes('youtube') || (!hasYt && hasTg)) destination = 'youtube';
          else if (err.includes('telegram') || !hasTg) destination = 'telegram';
          else destination = 'youtube';
        } else if (r.status === 'done') destination = hasYt ? 'youtube' : 'telegram';
        else if (r.status === 'detected' || r.status === 'downloading') destination = 'download';

        const kindLabel = r.is_story ? 'story' : (r.kind === 'photo' ? 'photo' : 'video');
        items.push({
          source: 'tiktok',
          id: r.id,
          content_key: `tt_${r.id}`,
          platform: 'tiktok',
          member_username: memberUsername,
          member_name: displayName,
          title: (r.title || '').trim() || `TikTok ${kindLabel} @${r.unique_id}`,
          status: r.status,
          destination,
          error_message: r.error_message,
          file_size_bytes: r.media_size_bytes || 0,
          has_yt: hasYt ? 1 : 0,
          has_tg: hasTg ? 1 : 0,
          youtube_video_id: (r.youtube_video_id || '').trim(),
          kind: kindLabel,
          created_at: r.created_at,
          age_hours: hoursSince(r.created_at),
        });
      }

      const sumRows = db.prepare(`
        SELECT status, COUNT(*) AS c FROM tiktok_posts
        WHERE status IN (${QUEUE_TIKTOK_STATUSES.map(() => '?').join(',')})
        GROUP BY status
      `).all(...QUEUE_TIKTOK_STATUSES) as unknown as Array<{ status: string; c: number }>;
      for (const r of sumRows) {
        summary.tiktok_total += r.c;
        summary.tiktok_by_status[r.status] = (summary.tiktok_by_status[r.status] || 0) + r.c;
      }

      // Backlog TikTok→YouTube: sudah arsip TG, status done, belum punya YT.
      const backlog = db.prepare(`
        SELECT COUNT(*) AS c FROM tiktok_posts
        WHERE COALESCE(telegram_message_ids, '') <> ''
          AND COALESCE(youtube_video_id, '') = ''
          AND status = 'done'
          AND visible = 1
      `).get() as { c: number } | undefined;
      summary.tiktok_yt_backlog = backlog?.c || 0;
    } catch {
      // Tabel tiktok_posts belum ada (database lama) — biarkan kosong.
    }
  }

  return { items, summary };
}

function hoursSince(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(/[: ]/.test(iso) && !iso.endsWith('Z') && !iso.includes('T')
    ? iso.replace(' ', 'T') + (iso.includes('+') || iso.endsWith('Z') ? '' : 'Z')
    : iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, (Date.now() - t) / 3600000);
}

// ─── Admin: ringkasan TikTok untuk /admin/status ─────────────────────────────

export interface TikTokAdminStats {
  accounts_total: number;
  accounts_enabled: number;
  posts_total: number;
  posts_by_status: Record<string, number>;
  pending_upload: number;
  failed: number;
  yt_backlog: number;
  uploading: number;
}

export function getTikTokAdminStats(): TikTokAdminStats {
  const db = getDb();
  const empty: TikTokAdminStats = {
    accounts_total: 0,
    accounts_enabled: 0,
    posts_total: 0,
    posts_by_status: {},
    pending_upload: 0,
    failed: 0,
    yt_backlog: 0,
    uploading: 0,
  };
  try {
    const acc = db.prepare(`
      SELECT COUNT(*) AS total, SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled
      FROM tiktok_accounts
    `).get() as { total: number; enabled: number | null } | undefined;
    const byStatus = db.prepare(`
      SELECT status, COUNT(*) AS c FROM tiktok_posts GROUP BY status
    `).all() as unknown as Array<{ status: string; c: number }>;
    const backlog = db.prepare(`
      SELECT COUNT(*) AS c FROM tiktok_posts
      WHERE COALESCE(telegram_message_ids, '') <> ''
        AND COALESCE(youtube_video_id, '') = ''
        AND status = 'done' AND visible = 1
    `).get() as { c: number } | undefined;

    const posts_by_status: Record<string, number> = {};
    let posts_total = 0;
    for (const r of byStatus) {
      posts_by_status[r.status] = r.c;
      posts_total += r.c;
    }
    return {
      accounts_total: acc?.total || 0,
      accounts_enabled: acc?.enabled || 0,
      posts_total,
      posts_by_status,
      pending_upload: posts_by_status.pending_upload || 0,
      failed: posts_by_status.failed || 0,
      yt_backlog: backlog?.c || 0,
      uploading: (posts_by_status.downloading || 0)
        + (posts_by_status.uploading_telegram || 0)
        + (posts_by_status.uploading_youtube || 0),
    };
  } catch {
    return empty;
  }
}
