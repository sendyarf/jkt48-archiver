import { getDb } from './db';
import { formatWibCardDate, formatWibLong } from './wib';

/**
 * Query arsip TikTok (postingan, foto, story member JKT48).
 *
 * Tabel `tiktok_accounts` & `tiktok_posts` diisi BOT (`bot/tiktok_monitor.py`);
 * web hanya membaca. Halaman /tiktok memakai:
 *   - sidebar kiri  → `getTikTokAccounts()`
 *   - pemutar + detail → `getTikTokPost()` / `getLatestTikTokPost()`
 *   - sidebar kanan → `getTikTokPosts({ account })`
 */

export interface TikTokAccount {
  unique_id: string;
  /** Nama member (dari member_hls) atau nama tampilan akun; fallback username. */
  display_name: string;
  member_username?: string;
  /** Foto member dari roster resmi jkt48.com (kosong bila belum ada). */
  avatar_url?: string;
  post_count: number;
  last_post_at?: string;
}

export interface TikTokPost {
  id: string;
  account: string;
  member_username?: string;
  kind: 'video' | 'photo';
  is_story: boolean;
  /** Deskripsi asli postingan TikTok (bisa panjang). */
  title: string;
  /** Judul siap tampil: "Video TikTok Indah JKT48 — 16 Sep 2026". */
  display_title: string;
  created_at: string;
  date_display: string;
  duration_seconds: number;
  duration_formatted: string;
  image_count: number;
  /** Thumbnail YouTube bila ada, jika tidak baru pakai cover TikTok. */
  thumbnail_url: string;
  cover_url: string;
  youtube_video_id: string;
  source_url: string;
  /** True bila media sudah tersimpan di channel arsip Telegram (bisa diunduh). */
  telegram_archived: boolean;
  /** Payload deep-link bot Telegram untuk arsip TikTok ini. */
  download_payload: string;
  /** URL foto publik (https) untuk postingan foto — [] bila tidak ada. */
  images: string[];
}

interface AccountRow {
  unique_id: string;
  display_name: string | null;
  member_username: string | null;
  member_display_name: string | null;
  avatar_url: string | null;
  last_post_at: string | null;
  post_count: number | null;
}

interface PostRow {
  id: string;
  unique_id: string;
  kind: string | null;
  is_story: number | null;
  title: string | null;
  created_at: string | null;
  duration_seconds: number | null;
  image_count: number | null;
  cover_url: string | null;
  source_url: string | null;
  youtube_video_id: string | null;
  telegram_message_ids: string | null;
  images_json: string | null;
  account_name: string | null;
  member_username: string | null;
  member_display_name: string | null;
}

/** "indahjkt48" → "Indahjkt48" untuk akun yang belum ketemu membernya. */
function prettifyUsername(value: string): string {
  const cleaned = (value || '').replace(/[._]+/g, ' ').trim();
  if (!cleaned) return '';
  return cleaned.replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (!s) return '';
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

/** Label jenis arsip: "Video TikTok" / "Foto TikTok" / "Story TikTok". */
export function tiktokKindLabel(post: { kind: string; is_story: boolean }): string {
  if (post.is_story) return post.kind === 'photo' ? 'Story TikTok (foto)' : 'Story TikTok';
  return post.kind === 'photo' ? 'Foto TikTok' : 'Video TikTok';
}

function mapPost(row: PostRow): TikTokPost {
  const kind: 'video' | 'photo' = (row.kind || 'video') === 'photo' ? 'photo' : 'video';
  const isStory = Boolean(row.is_story);
  const account = row.unique_id;
  const name =
    row.member_display_name?.trim() ||
    row.account_name?.trim() ||
    prettifyUsername(account);
  const dateDisplay = formatWibCardDate(row.created_at || '');
  const label = tiktokKindLabel({ kind, is_story: isStory });
  const ytId = row.youtube_video_id || '';
  const imageCount = Number(row.image_count || 0);

  return {
    id: row.id,
    account,
    member_username: row.member_username || undefined,
    kind,
    is_story: isStory,
    title: row.title || '',
    // Judul pemutar selalu memuat informasi nyata (jenis arsip + member + waktu).
    display_title: `${label} ${name}${dateDisplay ? ` — ${dateDisplay}` : ''}${
      kind === 'photo' && imageCount ? ` (${imageCount} foto)` : ''
    }`,
    created_at: row.created_at || '',
    date_display: dateDisplay,
    duration_seconds: Number(row.duration_seconds || 0),
    duration_formatted: formatDuration(Number(row.duration_seconds || 0)),
    image_count: imageCount,
    // Thumbnail YouTube lebih awet daripada cover CDN TikTok (URL CDN kedaluwarsa).
    thumbnail_url: ytId
      ? `https://img.youtube.com/vi/${ytId}/hqdefault.jpg`
      : row.cover_url || '',
    cover_url: row.cover_url || '',
    youtube_video_id: ytId,
    source_url: row.source_url || '',
    telegram_archived: Boolean((row.telegram_message_ids || '').trim()),
    download_payload: `tt_${row.id}`,
    images: parseImages(row.images_json),
  };
}

/** Parse kolom images_json → hanya URL http(s) publik (local_images_json diabaikan). */
function parseImages(json: string | null): string[] {
  if (!json) return [];
  try {
    const parsed = JSON.parse(json);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is string =>
        typeof item === 'string' && /^https?:\/\//.test(item)
    );
  } catch {
    return [];
  }
}

const POST_SELECT = `
  SELECT
    p.id, p.unique_id, p.kind, p.is_story, p.title, p.created_at,
    p.duration_seconds, p.image_count, p.cover_url, p.source_url,
    p.youtube_video_id, p.telegram_message_ids, p.images_json,
    a.display_name as account_name,
    a.member_username as member_username,
    mh.display_name as member_display_name
  FROM tiktok_posts p
  LEFT JOIN tiktok_accounts a ON a.unique_id = p.unique_id
  LEFT JOIN member_hls mh ON mh.username = a.member_username
`;

/**
 * Predikat SQL "postingan boleh tampil di halaman publik".
 *
 * Hanya arsip yang benar-benar sudah tersimpan yang ditampilkan: punya video
 * YouTube ATAU media di channel arsip Telegram (bisa diunduh lewat bot).
 * Ini mencegah kartu kosong/tanpa pemutar muncul saat bot masih memproses.
 */
const PUBLIC_POST_SQL = `
  p.visible = 1
  AND (COALESCE(p.youtube_video_id, '') <> '' OR COALESCE(p.telegram_message_ids, '') <> '')
`;

/** Daftar akun TikTok yang dipantau (untuk sidebar kiri). */
export function getTikTokAccounts(): TikTokAccount[] {
  const db = getDb();
  const rows = db.prepare(`
    SELECT
      a.unique_id,
      a.display_name,
      a.member_username,
      mh.display_name as member_display_name,
      a.avatar_url,
      a.last_post_at,
      (SELECT COUNT(*) FROM tiktok_posts p
        WHERE p.unique_id = a.unique_id AND p.visible = 1) as post_count
    FROM tiktok_accounts a
    LEFT JOIN member_hls mh ON mh.username = a.member_username
    WHERE a.enabled = 1
    ORDER BY COALESCE(NULLIF(mh.display_name, ''), NULLIF(a.display_name, ''), a.unique_id)
      COLLATE NOCASE ASC
  `).all() as unknown as AccountRow[];

  return rows.map((row) => ({
    unique_id: row.unique_id,
    display_name:
      row.member_display_name?.trim() ||
      row.display_name?.trim() ||
      prettifyUsername(row.unique_id),
    member_username: row.member_username || undefined,
    // Foto profil member dari roster resmi; sumbernya kolom bot, bukan
    // member_hls (roster memuat member yang belum ada baris member_hls-nya).
    avatar_url: row.avatar_url?.trim() || undefined,
    post_count: Number(row.post_count || 0),
    last_post_at: row.last_post_at || '',
  }));
}

/** Postingan TikTok terbaru, opsional dibatasi satu akun. */
export function getTikTokPosts(options: {
  account?: string;
  limit?: number;
  offset?: number;
} = {}): { posts: TikTokPost[]; total: number } {
  const db = getDb();
  const limit = Math.max(1, Math.min(200, Math.floor(options.limit || 60)));
  const offset = Math.max(0, Math.floor(options.offset || 0));
  const account = (options.account || '').trim();

  const where = account
    ? `WHERE ${PUBLIC_POST_SQL} AND p.unique_id = ?`
    : `WHERE ${PUBLIC_POST_SQL}`;
  const params: (string | number)[] = account ? [account] : [];

  const rows = db.prepare(`
    ${POST_SELECT}
    ${where}
    ORDER BY COALESCE(NULLIF(p.created_at, ''), p.added_at) DESC, p.id DESC
    LIMIT ? OFFSET ?
  `).all(...params, limit, offset) as unknown as PostRow[];

  const totalRow = db.prepare(`
    SELECT COUNT(*) as total FROM tiktok_posts p ${where}
  `).get(...params) as unknown as { total: number } | undefined;

  return { posts: rows.map(mapPost), total: Number(totalRow?.total || 0) };
}

/** Satu postingan TikTok (null bila belum siap tayang / tidak ada). */
export function getTikTokPost(id: string): TikTokPost | null {
  if (!id) return null;
  const db = getDb();
  const row = db.prepare(`
    ${POST_SELECT}
    WHERE ${PUBLIC_POST_SQL} AND p.id = ?
  `).get(String(id)) as unknown as PostRow | undefined;
  return row ? mapPost(row) : null;
}

/** Postingan TikTok paling baru yang siap tayang (pemutar awal halaman). */
export function getLatestTikTokPost(): TikTokPost | null {
  const { posts } = getTikTokPosts({ limit: 1 });
  return posts[0] || null;
}

/** Ringkasan arsip TikTok untuk header halaman. */
export function getTikTokSummary(): {
  posts: number;
  accounts: number;
  videos: number;
  photos: number;
  stories: number;
  /** Arsip yang punya video YouTube → bisa diputar langsung di halaman. */
  playable: number;
  latest_display: string;
} {
  const db = getDb();
  const row = db.prepare(`
    SELECT
      COUNT(*) as total,
      SUM(CASE WHEN p.kind = 'video' AND p.is_story = 0 THEN 1 ELSE 0 END) as videos,
      SUM(CASE WHEN p.kind = 'photo' AND p.is_story = 0 THEN 1 ELSE 0 END) as photos,
      SUM(CASE WHEN p.is_story = 1 THEN 1 ELSE 0 END) as stories,
      SUM(CASE WHEN COALESCE(p.youtube_video_id, '') <> '' THEN 1 ELSE 0 END) as playable,
      MAX(COALESCE(NULLIF(p.created_at, ''), p.added_at)) as latest
    FROM tiktok_posts p
    WHERE ${PUBLIC_POST_SQL}
  `).get() as unknown as {
    total: number | null; videos: number | null; photos: number | null;
    stories: number | null; playable: number | null; latest: string | null;
  } | undefined;

  const accountsRow = db.prepare(
    'SELECT COUNT(*) as total FROM tiktok_accounts WHERE enabled = 1'
  ).get() as unknown as { total: number } | undefined;

  const latest = row?.latest || '';
  return {
    posts: Number(row?.total || 0),
    accounts: Number(accountsRow?.total || 0),
    videos: Number(row?.videos || 0),
    photos: Number(row?.photos || 0),
    stories: Number(row?.stories || 0),
    playable: Number(row?.playable || 0),
    latest_display: latest ? formatWibLong(latest) : '',
  };
}

