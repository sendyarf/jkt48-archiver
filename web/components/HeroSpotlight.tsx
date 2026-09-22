'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Layers, PlayCircle } from 'lucide-react';
import type { VideoItem } from '@/lib/db';

/**
 * Hero bergaya poster film: thumbnail replay terbaru mengisi SELURUH panel
 * (full-bleed) sebagai latar, teks ditumpuk di atasnya dengan scrim gelap agar
 * tetap terbaca. Poster dipakai TAJAM — tanpa blur sinematik (pengguna menolak
 * blur) — karena kolase 3x2 dari bot sudah punya komposisi wajah sendiri.
 *
 * Judul memakai judul replay apa adanya (`buildDisplayTitle` → "LIVE IDN
 * <MEMBER> - <tanggal> | <jam> WIB") sehingga banner selalu membawa informasi
 * nyata (member + tanggal + jam), bukan semboyan pemasaran. Kicker mengabarkan
 * status: BARU RILIS / REPLAY TERBARU / SEGERA HADIR.
 *
 * Satu aksi utama ("Tonton sekarang") + satu aksi sekunder ("Lihat semua replay"),
 * memakai kelas tombol yang sudah ada supaya tidak ada istilah/gaya baru.
 */
export default function HeroSpotlight({ video, watchUrl }: { video: VideoItem; watchUrl: string }) {
  const maxres = video.youtube_video_id ? `https://img.youtube.com/vi/${video.youtube_video_id}/maxresdefault.jpg` : video.thumbnail_url;
  const fallback = video.youtube_video_id ? `https://img.youtube.com/vi/${video.youtube_video_id}/hqdefault.jpg` : video.thumbnail_url;
  const [src, setSrc] = useState(maxres || fallback);
  const pending = video.is_visible === false;
  const kicker = pending ? 'SEGERA HADIR' : video.is_new ? 'BARU RILIS' : 'REPLAY TERBARU';
  // Member, platform, tanggal, dan jam sudah menempel di judul, jadi baris meta
  // hanya memuat info yang belum ada di judul: label platform + durasi.
  const meta = [video.platform === 'idn' ? 'IDN Live' : 'Showroom', video.duration_formatted].filter(Boolean);

  return (
    <article className="hero-feature" data-platform={video.platform}>
      <div className="hero-feature-media">
        {src ? (
          <Image
            src={src}
            alt={video.title}
            fill
            priority
            sizes="100vw"
            className="hero-feature-img"
            onError={() => { if (src !== fallback) setSrc(fallback); }}
          />
        ) : (
          <span className="video-thumbnail-placeholder" aria-hidden="true" />
        )}
        <span className="hero-feature-scrim" aria-hidden="true" />
      </div>

      <div className="hero-feature-body">
        <p className="hero-feature-kicker">
          <span className="hero-feature-brand">JKT48 REPLAY</span>
          <span aria-hidden="true">·</span>
          {kicker}
        </p>
        <h1 className="hero-feature-title" title={video.title}>
          <Link href={watchUrl}>{video.title}</Link>
        </h1>
        {meta.length ? <p className="hero-feature-meta">{meta.join(' · ')}</p> : null}
        <div className="hero-actions">
          <Link href={watchUrl} className="primary-button">
            <PlayCircle size={18} aria-hidden="true" /> {pending ? 'Lihat jadwal tayang' : 'Tonton sekarang'}
          </Link>
          <a className="secondary-button" href="#catalog">
            <Layers size={18} aria-hidden="true" /> Lihat semua replay
          </a>
        </div>
      </div>
    </article>
  );
}
