import type { Metadata } from 'next';
import { Bricolage_Grotesque, Plus_Jakarta_Sans } from 'next/font/google';
import './globals.css';
import './portal.css';
import './admin-studio.css';
import Navbar from '@/components/PublicNavbar';
import Footer from '@/components/Footer';

/** Display font (judul/hero): ekspresif, playful — vibe idol/fandom.
 * next/font menyuntik nilainya langsung ke variabel --font-display di bawah. */
const displayFont = Bricolage_Grotesque({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700', '800'],
  variable: '--font-display',
  display: 'swap',
});

/** Body font: hangat & legibel untuk teks Indonesia.
 * next/font menyuntik nilainya langsung ke variabel --font-body di bawah. */
const bodyFont = Plus_Jakarta_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-body',
  display: 'swap',
});

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
  icons: { icon: '/icon.svg', apple: '/icon.svg' },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="id" className={`${displayFont.variable} ${bodyFont.variable}`}>
      <body>
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
          <a href="#main-content" className="skip-link">Lewati ke konten</a>
          <Navbar />
          <main id="main-content" style={{ flexGrow: 1 }}>{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
