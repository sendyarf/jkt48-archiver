import { NextResponse } from 'next/server';
import { isAdmin, sameOrigin } from '@/lib/auth';
import { AUTO_PUBLISH_AFTER_HOURS, AUTO_PUBLISH_AFTER_HOURS_SHOWROOM, clearPublication, getPublications, setPublication, type PublicationStatusFilter } from '@/lib/db';
export const dynamic = 'force-dynamic';
const json = (body: object, status = 200) => NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
const STATUS_FILTERS: PublicationStatusFilter[] = ['all', 'waiting', 'visible', 'held'];
const validVideoId = (id: unknown): id is string => typeof id === 'string' && /^[a-zA-Z0-9_-]{11}$/.test(id);
export async function GET(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  const params = new URL(request.url).searchParams;
  const page = Math.max(1, Math.min(100000, parseInt(params.get('page') || '1', 10) || 1));
  const statusRaw = params.get('status') || 'all';
  const status = (STATUS_FILTERS as string[]).includes(statusRaw) ? statusRaw as PublicationStatusFilter : 'all';
  try { return json({ success: true, autoPublishAfterHours: AUTO_PUBLISH_AFTER_HOURS, autoPublishAfterHoursShowroom: AUTO_PUBLISH_AFTER_HOURS_SHOWROOM, ...getPublications(params.get('q') || '', page, status) }); }
  catch { return json({ success: false, message: 'Data tidak tersedia.' }, 503); }
}
export async function POST(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  if (!sameOrigin(request)) return json({ success: false }, 403);
  try {
    const body = await request.json();
    const { youtube_video_id: videoId } = body;
    if (!validVideoId(videoId)) return json({ success: false, message: 'Data tidak valid.' }, 400);
    if (body.action === 'reset') {
      const ok = clearPublication(videoId);
      return json({ success: ok, message: ok ? 'Keputusan dihapus — kembali otomatis.' : 'Replay tidak ditemukan.' }, ok ? 200 : 404);
    }
    if (typeof body.published !== 'boolean') return json({ success: false, message: 'Data tidak valid.' }, 400);
    const ok = setPublication(videoId, body.published);
    return json({ success: ok, message: ok ? 'Publikasi diperbarui.' : 'Replay tidak ditemukan.' }, ok ? 200 : 404);
  } catch { return json({ success: false, message: 'Publikasi gagal diperbarui.' }, 400); }
}
