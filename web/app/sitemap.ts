import type { MetadataRoute } from 'next';
import { getAllVideos, getPublicMembers } from '@/lib/db';

// ISR 5 menit — sitemap tidak perlu fresh tiap request, hemat query DB saat crawl.
export const revalidate = 300;

function siteUrl(): URL {
  try {
    return new URL(process.env.APP_ORIGIN || 'http://localhost:3000');
  } catch {
    return new URL('http://localhost:3000');
  }
}

export default function sitemap(): MetadataRoute.Sitemap {
  const base = siteUrl();
  const url = (path: string) => new URL(path, base).toString();

  const entries: MetadataRoute.Sitemap = [
    { url: url('/'), changeFrequency: 'daily', priority: 1 },
    { url: url('/members'), changeFrequency: 'weekly', priority: 0.8 },
    { url: url('/tiktok'), changeFrequency: 'daily', priority: 0.7 },
    { url: url('/about'), changeFrequency: 'monthly', priority: 0.3 },
  ];

  for (const member of getPublicMembers()) {
    entries.push({
      url: url(`/members/${encodeURIComponent(member.username)}`),
      changeFrequency: 'weekly',
      priority: 0.6,
    });
  }

  // getAllVideos sudah memakai publicVisibilitySql — hanya replay yang tampil.
  let page = 1;
  let totalPages = 1;
  do {
    const batch = getAllVideos({ page, limit: 100 });
    totalPages = batch.totalPages;
    for (const video of batch.videos) {
      const id = video.watch_id || video.content_uid || video.youtube_video_id || String(video.id);
      entries.push({
        url: url(`/watch/${id}`),
        lastModified: video.created_at || undefined,
        changeFrequency: 'yearly',
        priority: 0.5,
      });
    }
    page += 1;
  } while (page <= totalPages && page <= 50);

  return entries;
}
