import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const SESSION_COOKIE = 'jkt48_admin_session';

// Optimistic check only — verifikasi sesi penuh (isAdmin) tetap di
// app/admin/layout.tsx dan route handler /api/admin/* (defense in depth).
// Proxy tidak boleh membuka DB / membaca session penuh (lihat Next.js docs).
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
    return NextResponse.redirect(new URL('/login', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/admin/:path*', '/api/admin/:path*'],
};
