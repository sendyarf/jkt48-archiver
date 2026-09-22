'use client';

import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { Play, Calendar, Clock } from 'lucide-react';
import type { VideoItem } from '@/lib/db';
import MiniCountdown from '@/components/MiniCountdown';

export default function VideoCard({ video }: { video: VideoItem }) {
  const watchUrl = `/watch/${video.watch_id || video.content_uid || video.youtube_video_id || video.id}`;

  // Tanggal sudah dikonversi ke WIB di server (lib/wib.ts via VideoItem
  // date_display) — kartu tidak boleh memformat sendiri di browser viewer,
  // agar tidak bergantung zona waktu perangkat.
  const dateFormatted = video.date_display || video.started_at;

  // Fallback ke hqdefault bila maxresdefault (kartu memakai hqdefault) tidak ada.
  const [thumbSrc, setThumbSrc] = useState(video.thumbnail_url);

  return (
    <div className="video-card">
      <Link href={watchUrl} className="video-thumbnail-box">
        {thumbSrc ? (
          <Image
            src={thumbSrc}
            alt={video.title}
            fill
            sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
            className="video-thumbnail"
            onError={() => {
              if (!video.youtube_video_id) return;
              const fb = `https://img.youtube.com/vi/${video.youtube_video_id}/hqdefault.jpg`;
              if (thumbSrc !== fb) setThumbSrc(fb);
            }}
          />
        ) : (
          // Arsip TG-first: belum ada YouTube → tanpa thumbnail remote.
          <span className="video-thumbnail-placeholder" aria-hidden="true" />
        )}
        <span className={`platform-badge ${video.platform}`}>
          {video.platform === 'idn' ? 'IDN' : 'Showroom'}
        </span>
        {video.is_visible === false ? (
          <>
            <span className="upcoming-badge">Segera</span>
            {video.publish_at ? (
              <span className="upcoming-countdown">
                <Clock size={11} style={{ display: 'inline', marginRight: '4px', verticalAlign: '-1px' }} />
                <MiniCountdown publishAt={video.publish_at} />
              </span>
            ) : null}
          </>
        ) : video.is_new ? (
          <span className="new-badge">Baru</span>
        ) : null}
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
