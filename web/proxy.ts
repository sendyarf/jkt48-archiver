import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const SESSION_COOKIE = 'jkt48_admin_session';

// Optimistic check only — verifikasi sesi penuh (isAdmin) tetap di
// app/admin/layout.tsx dan route handler /api/admin/* (defense in depth).
// Proxy tidak boleh membuka DB / membaca session penuh (lihat Next.js docs).
// Halaman /admin tanpa sesi TIDAK diarahkan ke login (bocor path admin);
// biarkan layout memanggil notFound() → 404.
export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (!hasSession) {
    const { pathname } = request.nextUrl;
    if (pathname.startsWith('/api/')) {
      return NextResponse.json(
        { success: false, message: 'Unauthorized.' },
        { status: 401, headers: { 'Cache-Control': 'no-store' } },
      );
    }
    // Halaman admin tanpa sesi → 404 (jangan redirect ke login: bocor path).
    // Rewrite ke route not-found bawaan App Router (status 404).
    return NextResponse.rewrite(new URL('/_not-found', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/admin/:path*', '/api/admin/:path*'],
};
