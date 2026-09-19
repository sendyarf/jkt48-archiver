import { existsSync } from 'node:fs';
import path from 'path';
import { DatabaseSync } from 'node:sqlite';
import { decodeWatchId, encodeWatchId, isRawYoutubeId } from './codec';

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

    _db = new DatabaseSync(dbPath);

    // Initialize media_catalog table if not exists
    _db.exec(`
      CREATE TABLE IF NOT EXISTS web_publications (
        youtube_video_id TEXT PRIMARY KEY,
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
    `);

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
        if (!existing.has('platform')) {
          _db.exec("ALTER TABLE live_sessions ADD COLUMN platform TEXT NOT NULL DEFAULT 'idn'");
        }
        if (!existing.has('telegram_message_ids')) {
          _db.exec('ALTER TABLE live_sessions ADD COLUMN telegram_message_ids TEXT');
        }
      }
    } catch {
      // Tabel live_sessions belum ada (mis. database baru) — biarkan query
      // pemanggil yang menangani.
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
 */
function publicVisibilitySql(alias = 'ls'): string {
  const explicitOn = `EXISTS (SELECT 1 FROM web_publications p WHERE p.youtube_video_id = ${alias}.youtube_video_id AND p.published = 1)`;
  const withheld = `EXISTS (SELECT 1 FROM web_publications p0 WHERE p0.youtube_video_id = ${alias}.youtube_video_id AND p0.published = 0)`;
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
  return `(${explicitOn} OR (NOT ${withheld} AND (CASE WHEN COALESCE(${alias}.platform, 'idn') = 'showroom' THEN ${showroomAuto} ELSE ${idnAuto} END)))`;
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
  dur_start: string | null;
  dur_end: string | null;
}

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
      MIN(ls.download_started_at) as dur_start,
      MAX(ls.download_ended_at) as dur_end
    FROM live_sessions ls
    LEFT JOIN merge_groups mg ON ls.merge_group_id = mg.id
    LEFT JOIN member_hls mh ON ls.member_username = mh.username
    WHERE ls.youtube_video_id IS NOT NULL
      AND ls.youtube_video_id != ''
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

  baseQuery += ` GROUP BY ls.youtube_video_id`;

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
    const ytId = r.youtube_video_id;
    const durSec = durationFromRange(r.dur_start, r.dur_end);
    return {
      id: ytId,
      platform: platform,
      streamer_username: r.member_username,
      streamer_name: dispName,
      title: title,
      started_at: startedAt,
      date_display: formatWibCardDate(startedAt),
      duration_seconds: durSec,
      duration_formatted: durSec > 0 ? formatDuration(durSec) : '',
      youtube_video_id: ytId,
      watch_id: ytId ? encodeWatchId(ytId) : '',
      thumbnail_url: `https://img.youtube.com/vi/${ytId}/hqdefault.jpg`,
      created_at: r.created_at || '',
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
    `ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != ''`,
    // Ditahan admin (published = 0) / terbit manual (published = 1) bukan pra-rilis.
    `NOT EXISTS (SELECT 1 FROM web_publications pw WHERE pw.youtube_video_id = ls.youtube_video_id AND pw.published = 0)`,
    `NOT EXISTS (SELECT 1 FROM web_publications po WHERE po.youtube_video_id = ls.youtube_video_id AND po.published = 1)`,
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
    GROUP BY ls.youtube_video_id
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
    const ytId = r.youtube_video_id;
    return {
      id: ytId,
      platform,
      streamer_username: r.member_username,
      streamer_name: dispName,
      title,
      started_at: startedAt,
      date_display: formatWibCardDate(startedAt),
      duration_seconds: 0,
      duration_formatted: '',
      youtube_video_id: ytId,
      watch_id: ytId ? encodeWatchId(ytId) : '',
      thumbnail_url: `https://img.youtube.com/vi/${ytId}/hqdefault.jpg`,
      created_at: r.created_at || '',
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
    WHERE (ls.youtube_video_id = ? OR ls.live_id = ? OR ls.id = ?)
      AND NOT EXISTS (
        SELECT 1 FROM web_publications pw
        WHERE pw.youtube_video_id = ls.youtube_video_id AND pw.published = 0
      )
    ORDER BY ls.id DESC
    LIMIT 1
  `;
  const stmt = db.prepare(sql);
  const r = stmt.get(lookupId, videoIdOrLiveId, videoIdOrLiveId) as unknown as VideoDetailRow | undefined;
  if (!r) return null;

  const dispName = cleanDisplayName(r.member_username, r.streamer_name || undefined);
  const startedAt = r.started_at || r.created_at || '';
  const platform = normalizePlatform(r.platform);
  const title = buildDisplayTitle(platform, dispName, startedAt);
  const ytId = r.youtube_video_id || '';
  const durSec = durationFromRange(r.download_started_at, r.download_ended_at);

  // publish_at dari datetime() berbentuk "YYYY-MM-DD HH:MM:SS" (UTC) — tambahkan 'Z'
  // agar diparse sebagai UTC, bukan waktu lokal browser.
  const publishAt = r.publish_at ? r.publish_at.replace(' ', 'T') + 'Z' : '';

  return {
    id: ytId,
    platform: platform,
    streamer_username: r.member_username,
    streamer_name: dispName,
    title: title,
    started_at: startedAt,
    date_display: formatWibCardDate(startedAt),
    duration_seconds: durSec,
    duration_formatted: durSec > 0 ? formatDuration(durSec) : '',
    youtube_video_id: ytId,
    watch_id: ytId ? encodeWatchId(ytId) : '',
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
}

export function getPublicMembers(): PublicMember[] {
  const rows = getDb().prepare(`SELECT ls.member_username AS username,
    COALESCE(NULLIF(mh.display_name, ''), ls.member_username) AS display_name,
    COUNT(DISTINCT ls.youtube_video_id) AS video_count
    FROM live_sessions ls
    LEFT JOIN member_hls mh ON mh.username = ls.member_username
    WHERE ls.youtube_video_id IS NOT NULL AND ls.youtube_video_id != ''
      AND ${publicVisibilitySql('ls')}
    GROUP BY ls.member_username ORDER BY display_name COLLATE NOCASE`).all() as unknown as PublicMember[];
  return rows.map(r => ({ ...r, display_name: cleanDisplayName(r.username, r.display_name) }));
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

export function getPublications(search = '', page = 1) {
  const filter = `%${search.slice(0, 100)}%`;
  const rows = getDb().prepare(`SELECT ls.youtube_video_id,
    COALESCE(NULLIF(MAX(ls.member_name), ''), ls.member_username) AS member_name,
    COALESCE(MAX(mh.display_name), '') AS display_name,
    ls.member_username AS member_username,
    COALESCE(NULLIF(MAX(ls.platform), ''), 'idn') AS platform,
    MIN(ls.started_at) AS started_at,
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
    GROUP BY ls.youtube_video_id ORDER BY MAX(ls.created_at) DESC LIMIT 51 OFFSET ?`)
    .all(filter, filter, filter, filter, (page - 1) * 50) as unknown as (Publication & {
      member_username: string;
      display_name: string;
      platform: string;
      started_at: string | null;
    })[];
  const videos: Publication[] = rows.map((row) => ({
    ...row,
    title: buildDisplayTitle(
      row.platform,
      cleanDisplayName(row.member_username, row.display_name || row.member_name || undefined),
      row.started_at || '',
    ),
  }));
  return { videos: videos.slice(0, 50), hasMore: rows.length > 50 };
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
  member_username: string;
  member_name: string | null;
  status: string;
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
}

export function getSystemStats(): SystemStats {
  const db = getDb();

  // Active recording sessions
  const activeStmt = db.prepare(`
    SELECT id, member_username, member_name, status FROM live_sessions 
    WHERE status IN ('detected', 'downloading', 'segment_done', 'download_complete', 'merging', 'uploading_youtube', 'pending_upload')
    ORDER BY created_at DESC
  `);
  const activeSessions = activeStmt.all() as unknown as ActiveSessionRow[];

  // Pending YouTube uploads
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
