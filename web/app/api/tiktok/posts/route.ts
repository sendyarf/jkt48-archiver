import { NextResponse } from 'next/server';
import { getTikTokPosts } from '@/lib/tiktok';

export const dynamic = 'force-dynamic';

/**
 * Daftar arsip TikTok untuk halaman /tiktok.
 *
 * Query: `account` (kosong = semua akun), `limit` (maks 200), `offset`.
 * Dipakai oleh komponen klien saat pengunjung berganti akun — supaya tidak
 * perlu memuat ulang seluruh halaman.
 */
export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const account = url.searchParams.get('account') || '';
    const limit = Number(url.searchParams.get('limit') || 120);
    const offset = Number(url.searchParams.get('offset') || 0);

    const { posts, total } = getTikTokPosts({ account, limit, offset });
    return NextResponse.json(
      { success: true, account, posts, total },
      { headers: { 'Cache-Control': 'no-store' } }
    );
  } catch {
    return NextResponse.json(
      { success: false, message: 'Arsip TikTok sementara tidak tersedia.' },
      { status: 503 }
    );
  }
}
