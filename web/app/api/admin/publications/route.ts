import { NextResponse } from 'next/server';
import { isAdmin, sameOrigin } from '@/lib/auth';
import { AUTO_PUBLISH_AFTER_HOURS, AUTO_PUBLISH_AFTER_HOURS_SHOWROOM, getPublications, setPublication } from '@/lib/db';
export const dynamic = 'force-dynamic';
const json = (body: object, status = 200) => NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
export async function GET(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  const params = new URL(request.url).searchParams;
  const page = Math.max(1, Math.min(100000, parseInt(params.get('page') || '1', 10) || 1));
  try { return json({ success: true, autoPublishAfterHours: AUTO_PUBLISH_AFTER_HOURS, autoPublishAfterHoursShowroom: AUTO_PUBLISH_AFTER_HOURS_SHOWROOM, ...getPublications(params.get('q') || '', page) }); }
  catch { return json({ success: false, message: 'Data tidak tersedia.' }, 503); }
}
export async function POST(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  if (!sameOrigin(request)) return json({ success: false }, 403);
  try {
    const { youtube_video_id, published } = await request.json();
    if (typeof youtube_video_id !== 'string' || !/^[a-zA-Z0-9_-]{11}$/.test(youtube_video_id) || typeof published !== 'boolean') return json({ success: false, message: 'Data tidak valid.' }, 400);
    const ok = setPublication(youtube_video_id, published);
    return json({ success: ok, message: ok ? 'Publikasi diperbarui.' : 'Rekaman tidak ditemukan.' }, ok ? 200 : 404);
  } catch { return json({ success: false, message: 'Publikasi gagal diperbarui.' }, 400); }
}
