import { notFound } from 'next/navigation';
import Link from 'next/link';
import type { Metadata } from 'next';
import { getVideoById, getAllVideos } from '@/lib/db';
import VideoPlayer from '@/components/VideoPlayer';

export const dynamic = 'force-dynamic';


interface WatchPageProps {

  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: WatchPageProps): Promise<Metadata> {
  const { id } = await params;
  const video = getVideoById(id);
  if (!video) {
    return { title: 'Video Tidak Ditemukan' };
  }
  return {
    title: `${video.title} - ${video.streamer_name}`,
    description: `Nonton siaran ulang ${video.title} oleh ${video.streamer_name}. Direkam dari ${video.platform === 'idn' ? 'IDN Live' : 'Showroom'}.`,
    openGraph: {
      title: `${video.title} - ${video.streamer_name}`,
      description: `Arsip siaran ulang JKT48 Replay`,
      images: [video.thumbnail_url],
    },
  };
}

export default async function WatchPage({ params }: WatchPageProps) {
  const { id } = await params;
  const video = getVideoById(id);

  if (!video) {
    notFound();
  }

  // Fetch related videos from the same member or recent
  const { videos: relatedVideos } = getAllVideos({
    member: video.streamer_username,
    limit: 6,
  });

  // Filter out the current video from related
  const filteredRelated = relatedVideos.filter(
    (v) => v.youtube_video_id !== video.youtube_video_id
  );

  let dateFormatted = video.started_at;
  try {
    const d = new Date(video.started_at);
    if (!isNaN(d.getTime())) {
      dateFormatted = d.toLocaleDateString('id-ID', {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }
  } catch {
    // fallback
  }

  const avatarUrl = `https://ui-avatars.com/api/?name=${encodeURIComponent(video.streamer_name)}&background=1e293b&color=f43f5e&size=128&bold=true`;

  return (
    <div className="container" style={{ paddingTop: '24px' }}>
      <div className="watch-layout">
        {/* Left Column: Player & Video Details */}
        <div>
          <div id="video-player-container">
            <VideoPlayer
              youtubeId={video.youtube_video_id}
              title={video.title}
              poster={video.thumbnail_url}
              platform={video.platform}
            />
          </div>

          <div className="watch-details-card">
            <h1 className="watch-title">{video.title}</h1>

            <div className="watch-streamer-row">
              <Link href={`/?member=${video.streamer_username}`} className="streamer-profile-link">
                <img src={avatarUrl} alt="" className="streamer-avatar-md" />
                <div>
                  <h3 style={{ fontSize: '1rem', color: '#fff' }}>{video.streamer_name}</h3>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>
                    {dateFormatted}
                  </p>
                </div>
              </Link>

              <span className={`platform-badge ${video.platform}`} style={{ position: 'static' }}>
                {video.platform === 'idn' ? 'IDN' : 'Showroom'}
              </span>
            </div>
          </div>
        </div>

        {/* Right Column: Related Videos */}
        <aside>
          <h2 className="sidebar-title">
            Lainnya dari {video.streamer_name}
          </h2>

          {filteredRelated.length === 0 ? (
            <p style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>
              Belum ada rekaman lain.
            </p>
          ) : (
            <div className="related-list">
              {filteredRelated.map((rel) => (
                <Link
                  key={rel.youtube_video_id || rel.id}
                  href={`/watch/${rel.youtube_video_id || rel.id}`}
                  className="related-card"
                >
                  <div className="related-thumb-box">
                    <img
                      src={rel.thumbnail_url}
                      alt={rel.title}
                      className="related-thumb"
                    />
                  </div>
                  <div className="related-info">
                    <h4 className="related-title">{rel.title}</h4>
                    <span className="related-meta">{rel.streamer_name}</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
