import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Halaman Tidak Ditemukan' };

export default function NotFound() {
  return (
    <div className="container page-section not-found">
      <div className="not-found-card" role="alert">
        <p className="eyebrow">ERROR 404</p>
        <p className="not-found-code" aria-hidden="true">404</p>
        <h1>Halaman tidak ditemukan</h1>
        <p className="not-found-text">
          Replay yang kamu cari tidak ada di arsip — mungkin tautannya salah,
          sudah dipindah, atau belum pernah diunggah.
        </p>
        <div className="not-found-actions">
          <Link href="/" className="primary-button">
            Kembali ke beranda
          </Link>
          <Link href="/members" className="secondary-button">
            Jelajahi member
          </Link>
          <Link href="/tiktok" className="text-button">
            Arsip TikTok
          </Link>
        </div>
      </div>
    </div>
  );
}
