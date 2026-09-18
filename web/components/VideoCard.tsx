'use client';

import Link from 'next/link';
import { Play, Calendar } from 'lucide-react';
import type { VideoItem } from '@/lib/db';

export default function VideoCard({ video }: { video: VideoItem }) {
  const watchUrl = `/watch/${video.youtube_video_id || video.id}`;
  
  // Format date nicely
  let dateFormatted = video.started_at;
  try {
    const d = new Date(video.started_at);
    if (!isNaN(d.getTime())) {
      dateFormatted = d.toLocaleDateString('id-ID', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      });
    }
  } catch {
    // fallback
  }

  const avatarUrl = `https://ui-avatars.com/api/?name=${encodeURIComponent(video.streamer_name)}&background=1e293b&color=f43f5e&size=64&bold=true`;

  return (
    <div className="video-card">
      <Link href={watchUrl} className="video-thumbnail-box">
        <img
          src={video.thumbnail_url}
          alt={video.title}
          className="video-thumbnail"
          loading="lazy"
          onError={(e) => {
            // fallback if maxresdefault doesn't exist
            (e.target as HTMLImageElement).src = `https://img.youtube.com/vi/${video.youtube_video_id}/hqdefault.jpg`;
          }}
        />
        <span className={`platform-badge ${video.platform}`}>
          {video.platform === 'idn' ? 'IDN' : 'Showroom'}
        </span>
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
          <img
            src={avatarUrl}
            alt={video.streamer_name}
            className="streamer-avatar-sm"
          />
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
