import { readFileSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { NextResponse } from 'next/server';

import { getTikTokPost } from '@/lib/tiktok';

export const dynamic = 'force-dynamic';

/**
 * Salinan lokal sampul unggahan TikTok.
 *
 * Cover asli di CDN TikTok bertanda tangan dan kedaluwarsa dalam hitungan jam,
 * jadi thumbnail yang di-cache browser atau yang diambil ulang berbulan-bulan
 * kemudian akan mati. Bot menyimpan salinan ke
 * `<DOWNLOAD_DIR>/tiktok/covers/<post_id>.jpg`; route inilah yang menyajikannya
 * supaya kartu di website tidak pernah jadi kotak kosong.
 *
 * Query `id` selalu dibaca dari database — path tidak pernah datang dari
 * permintaan pengguna, sehingga tidak ada celah path traversal dari sisi
 * pengguna.
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const id = (url.searchParams.get('id') || '').trim();
  if (!id || !/^\d{1,32}$/.test(id)) {
    return NextResponse.json({ success: false }, { status: 400 });
  }

  try {
    const post = getTikTokPost(id);
    const coverPath = String((post as { cover_path?: string } | null)?.cover_path || '').trim();
    if (!coverPath) {
      return NextResponse.json({ success: false }, { status: 404 });
    }

    // Tetap Batasi ke folder cover meski path berasal dari database: file
    // boleh saja sudah tidak ada karena prune, dan path bisa menunjuk ke
    // berkas di luar folder bila data pernah diubah manual.
    const base = path.resolve(process.env.DOWNLOAD_DIR || '/tmp/jkt48-lives', 'tiktok', 'covers');
    const resolved = path.resolve(coverPath);
    if (resolved !== base && !resolved.startsWith(base + path.sep)) {
      return NextResponse.json({ success: false }, { status: 403 });
    }
    if (!existsSync(resolved) || !statSync(resolved).isFile()) {
      return NextResponse.json({ success: false }, { status: 404 });
    }

    const body = readFileSync(resolved);
    return new NextResponse(new Uint8Array(body), {
      headers: {
        'Content-Type': 'image/jpeg',
        'Content-Length': String(body.length),
        'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
      },
    });
  } catch {
    return NextResponse.json({ success: false }, { status: 404 });
  }
}
