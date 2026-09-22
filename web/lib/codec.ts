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
 * Token non-YouTube (content_uid / live_id) dikembalikan apa adanya — jangan
 * dipaksa di-decode karena hasil base64 semu bisa merusak lookup.
 */
export function decodeWatchId(token: string): string {
  const t = (token || '').trim();
  if (!t) return '';
  if (YT_ID_RE.test(t)) return t; // sudah mentah
  // Bukan pola YouTube: content_uid (merged_… / user_… / sr_…) atau live_id
  // lain — pakai langsung sebagai kunci lookup.
  if (!/^[A-Za-z0-9_-]{11}$/.test(t)) {
    // Coba decode HANYA bila tampak seperti Base64URL tersamar yang menghasilkan
    // YouTube ID valid; selain itu kembalikan token asli.
    try {
      const b64 = t.replace(/-/g, '+').replace(/_/g, '/');
      const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
      const decoded =
        typeof Buffer !== 'undefined'
          ? Buffer.from(b64 + pad, 'base64').toString('utf8')
          : atob(b64 + pad);
      if (YT_ID_RE.test(decoded)) return decoded;
    } catch {
      // bukan base64 valid
    }
    return t;
  }
  try {
    const b64 = t.replace(/-/g, '+').replace(/_/g, '/');
    const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
    const decoded =
      typeof Buffer !== 'undefined'
        ? Buffer.from(b64 + pad, 'base64').toString('utf8')
        : atob(b64 + pad);
    return YT_ID_RE.test(decoded) ? decoded : t;
  } catch {
    return t; // bukan base64 valid → kembalikan apa adanya (biar lookup gagal → 404)
  }
}

