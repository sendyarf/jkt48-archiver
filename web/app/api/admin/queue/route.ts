import { NextResponse } from 'next/server';
import { isAdmin } from '@/lib/auth';
import { getAdminQueue } from '@/lib/db';

export const dynamic = 'force-dynamic';
const json = (body: object, status = 200) =>
  NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } });

export async function GET(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  const params = new URL(request.url).searchParams;
  const sourceRaw = params.get('source') || 'all';
  const source: 'all' | 'live' | 'tiktok' =
    sourceRaw === 'live' ? 'live' : sourceRaw === 'tiktok' ? 'tiktok' : 'all';
  const limit = Math.max(1, Math.min(200, parseInt(params.get('limit') || '100', 10) || 100));
  try {
    return json({
      success: true,
      ...getAdminQueue({
        source,
        platform: params.get('platform') || '',
        status: params.get('status') || '',
        limit,
      }),
      source,
    });
  } catch {
    return json({ success: false, message: 'Antrean tidak tersedia.' }, 503);
  }
}
