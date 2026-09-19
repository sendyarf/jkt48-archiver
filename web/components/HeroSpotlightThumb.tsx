'use client';

import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { PlayCircle } from 'lucide-react';
import type { VideoItem } from '@/lib/db';

/**
 * Thumbnail hero dengan fallback berlapis — pola yang sama dengan VideoCard:
 * maxresdefault → hqdefault. Tanpa ini, video tanpa varian maxres menampilkan
 * ikon gambar rusak di spotlight (next/image tidak punya onError global).
 */
export default function HeroSpotlightThumb({ video, watchUrl }: { video: VideoItem; watchUrl: string }) {
  const maxres = video.youtube_video_id ? `https://img.youtube.com/vi/${video.youtube_video_id}/maxresdefault.jpg` : video.thumbnail_url;
  const fallback = video.youtube_video_id ? `https://img.youtube.com/vi/${video.youtube_video_id}/hqdefault.jpg` : video.thumbnail_url;
  const [src, setSrc] = useState(maxres || fallback);
  return (
    <Link href={watchUrl} className="hero-spotlight-thumb" aria-label={`Tonton ${video.title}`}>
      {src ? (
        <Image
          src={src}
          alt={video.title}
          fill
          sizes="(max-width: 720px) 100vw, 420px"
          className="hero-spotlight-img"
          onError={() => { if (src !== fallback) setSrc(fallback); }}
        />
      ) : null}
      <span className={`platform-badge ${video.platform}`}>
        {video.platform === 'idn' ? 'IDN' : 'Showroom'}
      </span>
      {video.duration_formatted ? (
        <span className="duration-badge">{video.duration_formatted}</span>
      ) : null}
      <span className="hero-spotlight-play" aria-hidden="true"><PlayCircle size={44} /></span>
    </Link>
  );
}
