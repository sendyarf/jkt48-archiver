import { NextResponse } from 'next/server';
import { allowLogin, clientIp, configuredSecret, createSession, destroySession, isAdmin, sameOrigin, validSecret } from '@/lib/auth';
import { verifyTurnstile } from '@/lib/turnstile';

export const dynamic = 'force-dynamic';
const json = (data: object, status = 200) => NextResponse.json(data, { status, headers: { 'Cache-Control': 'no-store' } });
export async function GET() { return json({ success: await isAdmin() }); }

export async function POST(request: Request) {
  if (!sameOrigin(request)) return json({ success: false, message: 'Permintaan tidak diizinkan.' }, 403);
  if (!configuredSecret()) return json({ success: false, message: 'Login admin belum dikonfigurasi.' }, 503);
  if (!allowLogin(request)) return json({ success: false, message: 'Terlalu banyak percobaan. Coba lagi dalam 15 menit.' }, 429);
  try {
    const body = await request.json();
    if (!(await verifyTurnstile(body?.turnstile, clientIp(request)))) {
      return json({ success: false, message: 'Verifikasi keamanan gagal. Muat ulang halaman lalu coba lagi.' }, 401);
    }
    if (!validSecret(body?.secret)) return json({ success: false, message: 'Kredensial tidak valid.' }, 401);
    await createSession();
    return json({ success: true });
  } catch { return json({ success: false, message: 'Tidak dapat memproses login.' }, 400); }
}

export async function DELETE(request: Request) {
  if (!sameOrigin(request)) return json({ success: false }, 403);
  await destroySession();
  return json({ success: true });
}
