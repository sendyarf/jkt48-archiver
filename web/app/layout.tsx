import type { Metadata } from 'next';
import { Outfit } from 'next/font/google';
import './globals.css';
import './portal.css';
import './admin-studio.css';
import Navbar from '@/components/PublicNavbar';
import Footer from '@/components/Footer';
import BottomNav from '@/components/BottomNav';

/** Satu font untuk display & body: Outfit (variable, self-hosted via next/font). */
const outfit = Outfit({
  subsets: ['latin'],
  variable: '--font-outfit',
  display: 'swap',
});

/** Anti-FOUC: terapkan tema tersimpan sebelum paint pertama. */
const themeInit = `(function(){try{var t=localStorage.getItem('jkt48_theme');if(t!=='light'&&t!=='dark'){t=window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';}document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;

export const viewport = {
  width: 'device-width',
  initialScale: 1,
};

// APP_ORIGIN menentukan basis URL absolut untuk metadata. Dibungkus try/catch agar
// nilai yang salah bentuk tidak menggagalkan build.
function siteBaseUrl(): URL {
  const fallback = 'http://localhost:3000';
  try {
    return new URL(process.env.APP_ORIGIN || fallback);
  } catch {
    return new URL(fallback);
  }
}

export const metadata: Metadata = {
  metadataBase: siteBaseUrl(),
  title: {
    default: 'JKT48 Replay | Nonton Ulang Live IDN & Showroom',
    template: '%s | JKT48 Replay',
  },
  description:
    'Kumpulan replay live JKT48 dari IDN Live & Showroom. Cari replay favoritmu lewat nama member, judul, atau tanggal.',
  keywords: [
    'JKT48 Replay',
    'Arsip JKT48',
    'Nonton Ulang JKT48',
    'IDN Live JKT48',
    'Showroom JKT48',
    'Replay Live JKT48',
  ],
  authors: [{ name: 'JKT48 Replay' }],
  // Icon via file convention: app/icon.svg + app/apple-icon.tsx (jangan set
  // metadata.icons — menimpa auto-link apple-touch-icon).
  manifest: '/manifest.webmanifest',
  // Default share card — di-override per-halaman (watch/tiktok) bila perlu.
  openGraph: {
    type: 'website',
    locale: 'id_ID',
    siteName: 'JKT48 Replay',
    title: 'JKT48 Replay | Nonton Ulang Live IDN & Showroom',
    description:
      'Kumpulan replay live JKT48 dari IDN Live & Showroom. Cari replay favoritmu lewat nama member, judul, atau tanggal.',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'JKT48 Replay | Nonton Ulang Live IDN & Showroom',
    description:
      'Kumpulan replay live JKT48 dari IDN Live & Showroom. Cari replay favoritmu lewat nama member, judul, atau tanggal.',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="id"
      data-theme="dark"
      data-scroll-behavior="smooth"
      className={outfit.variable}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
        {/* Prefetch DNS/koneksi ke host YouTube sebelum pemutar/thumbnail dimuat. */}
        <link rel="preconnect" href="https://www.youtube.com" />
        <link rel="preconnect" href="https://i.ytimg.com" />
        <link rel="preconnect" href="https://img.youtube.com" />
        <link rel="dns-prefetch" href="https://www.youtube.com" />
        <link rel="dns-prefetch" href="https://i.ytimg.com" />
        <link rel="dns-prefetch" href="https://img.youtube.com" />
      </head>
      <body>
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
          <a href="#main-content" className="skip-link">Lewati ke konten</a>
          <Navbar />
          <main id="main-content" style={{ flexGrow: 1 }}>{children}</main>
          <Footer />
          <BottomNav />
        </div>
      </body>
    </html>
  );
}
