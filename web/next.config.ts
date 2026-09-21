import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      // Thumbnail YouTube (img.youtube.com & i.ytimg.com)
      { protocol: 'https', hostname: 'img.youtube.com' },
      { protocol: 'https', hostname: 'i.ytimg.com' },
      // Cover arsip TikTok: URL CDN TikTok berganti host (p16/p19-…tiktokcdn.com)
      // dan bertanda tangan (kedaluwarsa), jadi hanya dipakai sebagai FALLBACK
      // bila postingan belum punya video YouTube.
      { protocol: 'https', hostname: '**.tiktokcdn.com' },
      { protocol: 'https', hostname: '**.tiktokcdn-us.com' },
      { protocol: 'https', hostname: '**.tiktokcdn-eu.com' },
      // Foto member arsip TikTok: URL roster resmi jkt48.com (bot/jkt48_members.py).
      { protocol: 'https', hostname: 'jkt48.com' },
    ],
  },
};

export default nextConfig;
