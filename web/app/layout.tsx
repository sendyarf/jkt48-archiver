import type { Metadata } from 'next';
import './globals.css';
import './portal.css';
import './admin-studio.css';
import Navbar from '@/components/PublicNavbar';
import Footer from '@/components/Footer';

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
  title: 'JKT48 Live Archival & Web Portal | Nonton Siaran Ulang IDN & Showroom',
  description:
    'Jelajahi arsip komunitas siaran ulang member JKT48. Temukan rekaman berdasarkan member dan judul.',
  keywords: [
    'JKT48 Live',
    'Arsip JKT48',
    'IDN Live JKT48',
    'Showroom JKT48',
    'Rekaman Live JKT48',
  ],
  authors: [{ name: 'JKT48 Live Stream Bot Engine' }],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="id">
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
