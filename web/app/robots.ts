import type { MetadataRoute } from 'next';

function siteUrl(): URL {
  try {
    return new URL(process.env.APP_ORIGIN || 'http://localhost:3000');
  } catch {
    return new URL('http://localhost:3000');
  }
}

export default function robots(): MetadataRoute.Robots {
  const base = siteUrl();
  return {
    rules: {
      userAgent: '*',
      allow: '/',
      // Admin & API tidak perlu di-crawl; halaman noindex tetap boleh di-crawl
      // agar tag robots noindex terbaca mesin pencari.
      disallow: ['/admin', '/api/'],
    },
    sitemap: new URL('/sitemap.xml', base).toString(),
  };
}
