import type { Metadata } from 'next';
import Link from 'next/link';
import { getTikTokAccounts, getTikTokPosts, getTikTokSummary } from '@/lib/tiktok';
import TikTokArchive from '@/components/TikTokArchive';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Arsip TikTok member JKT48',
  description:
    'Kumpulan video, foto, dan story TikTok member JKT48 — diarsipkan otomatis dan bisa diunduh lewat bot Telegram.',
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

  return (
    <div className="page-section">
      <div className="container">
        <div className="section-heading">
          <div>
            <p className="eyebrow">TIKTOK · ARSIP</p>
            <h1>Arsip TikTok member JKT48</h1>
            <p>
              Postingan, foto, dan story yang sudah diamankan bot. Pilih member di kiri,
              lalu pilih arsipnya di kanan. Foto bisa diunduh sebagai foto lewat bot
              Telegram — bukan cuma videonya.
            </p>
          </div>
          <p className="result-summary">
            {summary.posts > 0
              ? `${summary.posts} arsip siap ditonton dari ${summary.accounts} akun`
              : `${summary.accounts} akun dipantau`}
            {summary.posts > 0
              ? ` · ${summary.videos} video · ${summary.photos} foto · ${summary.stories} story`
              : ''}
          </p>
        </div>

        {summary.latest_display && (
          <p className="help-text" style={{ marginBottom: '14px' }}>
            Update terakhir: {summary.latest_display}
          </p>
        )}

        <TikTokArchive
          accounts={accounts}
          initialPosts={posts}
          initialTotal={total}
          initialSelectedId={posts[0]?.id || ''}
        />

        <p className="help-text" style={{ marginTop: '18px' }}>
          Belum nemu akun membernya? Cek juga <Link href="/members">direktori member</Link>{' '}
          atau kembali ke <Link href="/">replay live</Link>.
        </p>
      </div>
    </div>
  );
}
