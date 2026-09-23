import type { Metadata } from 'next';
import Link from 'next/link';
import { getTikTokAccounts, getTikTokPosts, getTikTokSummary } from '@/lib/tiktok';
import TikTokArchive from '@/components/TikTokArchive';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Arsip TikTok member JKT48',
  description:
    'Kumpulan video, foto, dan story TikTok member JKT48 — diarsipkan otomatis dan bisa diunduh lewat bot Telegram.',
  alternates: { canonical: '/tiktok' },
  openGraph: {
    title: 'Arsip TikTok JKT48',
    description: 'Video, foto, dan story TikTok member JKT48 dalam satu tempat.',
  },
};

const MAX_POSTS = 120;

export default function TikTokPage() {
  const accounts = getTikTokAccounts();
  const { posts, total } = getTikTokPosts({ limit: MAX_POSTS });
  const summary = getTikTokSummary();
  // Sambutan pertama harus arsip yang BISA DIPUTAR, bukan sekadar yang terbaru
  // — arsip terbaru bisa jadi belum punya video YouTube (hanya unduh via bot).
  const firstPlayable = posts.find((p) => p.youtube_video_id);

  return (
    <div className="page-section tiktok-page">
      <div className="container">
        {/* Judul tetap ada untuk SEO & pembaca layar, tetapi header besar sengaja
            dilepas — pengunjung langsung masuk ke arsip tanpa blok perkenalan. */}
        <h1 className="sr-only">Arsip TikTok member JKT48</h1>

        <TikTokArchive
          accounts={accounts}
          initialPosts={posts}
          initialTotal={total}
          initialSelectedId={(firstPlayable || posts[0])?.id || ''}
        />

        <footer className="tiktok-footer">
          <p className="tiktok-stats">
            {summary.posts > 0
              ? `${summary.posts} arsip dari ${summary.accounts} akun — ${summary.playable} bisa diputar · ${summary.posts - summary.playable} unduh via bot · ${summary.videos} video · ${summary.photos} foto · ${summary.stories} story`
              : `${summary.accounts} akun dipantau`}
          </p>
          {summary.latest_display && (
            <p className="tiktok-updated">Update terakhir: {summary.latest_display}</p>
          )}
          <p className="help-text">
            Belum nemu akun membernya? Cek juga <Link href="/members">direktori member</Link>{' '}
            atau kembali ke <Link href="/">replay live</Link>.
          </p>
        </footer>
      </div>
    </div>
  );
}
