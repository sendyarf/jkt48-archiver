import { NextResponse } from 'next/server';
import { isAdmin, sameOrigin } from '@/lib/auth';
import {
  getAdminVideos,
  setContentVisibility,
  clearContentVisibility,
} from '@/lib/db';

export const dynamic = 'force-dynamic';
const json = (body: object, status = 200) =>
  NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } });

export async function GET(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  const params = new URL(request.url).searchParams;
  const page = Math.max(1, Math.min(100000, parseInt(params.get('page') || '1', 10) || 1));
  const visibilityRaw = params.get('visibility') || 'all';
  const visibility: 'all' | 'visible' | 'hidden' =
    visibilityRaw === 'visible' ? 'visible' : visibilityRaw === 'hidden' ? 'hidden' : 'all';
  try {
    return json({
      success: true,
      ...getAdminVideos(params.get('q') || '', page, visibility),
      page,
      visibility,
    });
  } catch {
    return json({ success: false, message: 'Data tidak tersedia.' }, 503);
  }
}

export async function POST(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  if (!sameOrigin(request)) return json({ success: false }, 403);
  try {
    const body = await request.json();
    const contentKey = body.content_key;
    if (typeof contentKey !== 'string' || !contentKey || contentKey.length > 120) {
      return json({ success: false, message: 'Data tidak valid.' }, 400);
    }
    if (body.action === 'reset') {
      const ok = clearContentVisibility(contentKey);
      return json({
        success: ok,
        message: ok ? 'Override dihapus — mengikuti aturan otomatis.' : 'Konten tidak ditemukan.',
      }, ok ? 200 : 404);
    }
    if (typeof body.visible !== 'boolean') {
      return json({ success: false, message: 'Data tidak valid.' }, 400);
    }
    const ok = setContentVisibility(contentKey, body.visible);
    return json({
      success: ok,
      message: ok
        ? (body.visible ? 'Konten ditampilkan di situs.' : 'Konten disembunyikan dari situs.')
        : 'Konten tidak ditemukan.',
    }, ok ? 200 : 404);
  } catch {
    return json({ success: false, message: 'Perubahan gagal disimpan.' }, 400);
  }
}
