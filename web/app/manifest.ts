import type { MetadataRoute } from 'next';

/** Manifest PWA — dilayani di /manifest.webmanifest oleh Next. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'JKT48 Replay',
    short_name: 'JKT48 Replay',
    description:
      'Kumpulan replay live JKT48 dari IDN Live & Showroom. Cari replay favoritmu lewat nama member, judul, atau tanggal.',
    start_url: '/',
    display: 'standalone',
    background_color: '#07090e',
    theme_color: '#07090e',
    icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
  };
}
