const SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
/** Sentinel: '' kosong dihapus OS (Windows) lalu .env di-load lagi — pakai 'off'. */
const DISABLED = 'off';

function secretKey(): string | null {
  const secret = process.env.TURNSTILE_SECRET_KEY;
  if (!secret || secret === DISABLED) return null;
  return secret;
}

/** Turnstile aktif hanya bila secret key di-set (site key alone = widget tanpa verifikasi). */
export function turnstileEnabled(): boolean {
  return !!secretKey();
}

/**
 * Verifikasi token Turnstile ke Cloudflare siteverify.
 * - Belum dikonfigurasi / 'off' → lolos (login tanpa Turnstile; verify scripts tetap jalan).
 * - Token kosong / siteverify gagal / success !== true → tolak (fail-closed).
 */
export async function verifyTurnstile(token: unknown, remoteip?: string): Promise<boolean> {
  const secret = secretKey();
  if (!secret) return true;
  if (typeof token !== 'string' || token.length === 0 || token.length > 4096) return false;
  try {
    const form = new URLSearchParams({ secret, response: token });
    if (remoteip) form.set('remoteip', remoteip);
    const res = await fetch(SITEVERIFY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form,
      cache: 'no-store',
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { success?: boolean };
    return data.success === true;
  } catch {
    return false;
  }
}
