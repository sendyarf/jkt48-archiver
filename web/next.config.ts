import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // /status → /admin/status permanen (308) — path lama tidak akan balik.
  // HTTP redirect (bukan redirect() di page) agar tidak ter-streaming jadi
  // meta tag client-side.
  redirects: async () => [
    { source: '/status', destination: '/admin/status', permanent: true },
  ],
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
      // Cover room Showroom (showroom_rooms.json) — fallback foto profil member.
      { protocol: 'https', hostname: 'static.showroom-live.com' },
    ],
  },
};

export default nextConfig;
