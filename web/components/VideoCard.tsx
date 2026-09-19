'use client';

import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { Play, Calendar } from 'lucide-react';
import type { VideoItem } from '@/lib/db';

export default function VideoCard({ video }: { video: VideoItem }) {
  const watchUrl = `/watch/${video.youtube_video_id || video.id}`;

  // Tanggal sudah dikonversi ke WIB di server (lib/wib.ts via VideoItem
  // date_display) — kartu tidak boleh memformat sendiri di browser viewer,
  // agar tidak bergantung zona waktu perangkat.
  const dateFormatted = video.date_display || video.started_at;

  // Fallback ke hqdefault bila maxresdefault (kartu memakai hqdefault) tidak ada.
  const [thumbSrc, setThumbSrc] = useState(video.thumbnail_url);

  return (
    <div className="video-card">
      <Link href={watchUrl} className="video-thumbnail-box">
        <Image
          src={thumbSrc}
          alt={video.title}
          fill
          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
          className="video-thumbnail"
          onError={() => {
            const fb = `https://img.youtube.com/vi/${video.youtube_video_id}/hqdefault.jpg`;
            if (thumbSrc !== fb) setThumbSrc(fb);
          }}
        />
        <span className={`platform-badge ${video.platform}`}>
          {video.platform === 'idn' ? 'IDN' : 'Showroom'}
        </span>
        {video.is_new ? <span className="new-badge">Baru</span> : null}
        {video.duration_formatted ? (
          <span className="duration-badge">{video.duration_formatted}</span>
        ) : null}

        <div className="play-hover-overlay">
          <div className="play-btn-circle">
            <Play size={20} fill="#fff" />
          </div>
        </div>
      </Link>

      <div className="video-info">
        <Link href={watchUrl}>
          <h3 className="video-title" title={video.title}>
            {video.title}
          </h3>
        </Link>

        <div className="video-meta">
          <div className="streamer-details">
            <span className="streamer-name">{video.streamer_name}</span>
            <span className="video-date">
              <Calendar size={11} style={{ display: 'inline', marginRight: '4px', verticalAlign: '-1px' }} />
              {dateFormatted}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
