/**
 * codec.ts — penyamaran YouTube video ID di URL publik (/watch/{id}).
 *
 * Memakai Base64URL (tanpa padding) agar ID tidak terlihat sebagai ID YouTube
 * mentah. Ini OBFUSCATION, bukan enkripsi/proteksi — siapa pun bisa decode.
 * Tujuannya hanya kerapian tampilan URL; kontrol akses tetap di sisi server.
 *
 * YouTube ID asli (11 char, alphabet [A-Za-z0-9_-]) → Base64URL.
 */

const YT_ID_RE = /^[A-Za-z0-9_-]{11}$/;

/** True bila string tampak seperti YouTube video ID mentah (11 char). */
export function isRawYoutubeId(s: string): boolean {
  return YT_ID_RE.test(s);
}

/** Encode YouTube ID mentah → token Base64URL tersamar untuk URL. */
export function encodeWatchId(youtubeId: string): string {
  if (!YT_ID_RE.test(youtubeId)) return youtubeId;
  // Buffer tersedia di Node (server) & edge; di browser fallback ke btoa.
  const b64 =
    typeof Buffer !== 'undefined'
      ? Buffer.from(youtubeId, 'utf8').toString('base64')
      : btoa(youtubeId);
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * Decode token URL → YouTube ID mentah.
 * Menerima: token Base64URL ATAU ID mentah (backward-compat untuk link lama).
 */
export function decodeWatchId(token: string): string {
  const t = (token || '').trim();
  if (!t) return '';
  if (YT_ID_RE.test(t)) return t; // sudah mentah
  try {
    const b64 = t.replace(/-/g, '+').replace(/_/g, '/');
    const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
    const decoded =
      typeof Buffer !== 'undefined'
        ? Buffer.from(b64 + pad, 'base64').toString('utf8')
        : atob(b64 + pad);
    return YT_ID_RE.test(decoded) ? decoded : decoded;
  } catch {
    return t; // bukan base64 valid → kembalikan apa adanya (biarkan lookup gagal → 404)
  }
}
