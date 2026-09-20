'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { PlayCircle } from 'lucide-react';
import type { VideoItem } from '@/lib/db';

/**
 * Kartu spotlight replay terbaru di hero.
 *
 * Poster memakai rasio asli thumbnail (16:9) dengan pola fallback yang sama
 * dengan VideoCard — maxresdefault → hqdefault — sehingga wajah member tajam
 * (bukan backdrop blur). Thumbnail 3x2 dari bot mengisi penuh bingkai ini.
 * Keterangan + CTA utama ("Tonton replay") ada DI DALAM kartu supaya hero
 * hanya punya satu aksi utama dan tidak bersaing dengan tombol kiri.
 */
export default function HeroSpotlight({ video, watchUrl }: { video: VideoItem; watchUrl: string }) {
  const maxres = video.youtube_video_id ? `https://img.youtube.com/vi/${video.youtube_video_id}/maxresdefault.jpg` : video.thumbnail_url;
  const fallback = video.youtube_video_id ? `https://img.youtube.com/vi/${video.youtube_video_id}/hqdefault.jpg` : video.thumbnail_url;
  const [src, setSrc] = useState(maxres || fallback);
  const kicker = video.is_visible === false ? 'SEGERA HADIR' : video.is_new ? 'BARU TERBIT' : 'REPLAY TERBARU';
  return (
    <article className="hero-card" data-platform={video.platform}>
      <Link href={watchUrl} className="hero-card-thumb" aria-label={`Tonton replay: ${video.title}`}>
        {src ? (
          <Image
            src={src}
            alt={video.title}
            fill
            sizes="(max-width: 900px) 92vw, 440px"
            className="hero-card-img"
            onError={() => { if (src !== fallback) setSrc(fallback); }}
          />
        ) : null}
        <span className={`platform-badge ${video.platform}`}>
          {video.platform === 'idn' ? 'IDN' : 'Showroom'}
        </span>
        {video.duration_formatted ? (
          <span className="duration-badge">{video.duration_formatted}</span>
        ) : null}
        <span className="hero-card-play" aria-hidden="true">
          <PlayCircle size={28} />
        </span>
      </Link>

      <div className="hero-card-body">
        <p className="hero-card-kicker">{kicker}</p>
        <Link href={watchUrl}>
          <h2 className="hero-card-title" title={video.title}>{video.title}</h2>
        </Link>
        <p className="hero-card-meta">
          <span>{video.streamer_name}</span>
          <span aria-hidden="true">·</span>
          <span>{video.date_display || video.started_at}</span>
        </p>
        <Link href={watchUrl} className="primary-button hero-card-cta">
          <PlayCircle size={18} aria-hidden="true" /> Tonton replay
        </Link>
      </div>
    </article>
  );
}
