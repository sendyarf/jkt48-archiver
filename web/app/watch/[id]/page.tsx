import { notFound, redirect } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { getVideoById, getAllVideos } from '@/lib/db';
import { encodeWatchId, isRawYoutubeId } from '@/lib/codec';
import { formatWibLong } from '@/lib/wib';
import VideoPlayer from '@/components/VideoPlayer';
import TelegramDownloadButton from '@/components/TelegramDownloadButton';
import CountdownTimer from '@/components/CountdownTimer';
import ShareButton from '@/components/ShareButton';
import WatchTracker from '@/components/WatchTracker';

export const dynamic = 'force-dynamic';


interface WatchPageProps {

  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: WatchPageProps): Promise<Metadata> {
  const { id } = await params;
  const video = getVideoById(id);
  if (!video) {
    return { title: 'Replay tidak ditemukan' };
  }
  return {
    title: `${video.title} - ${video.streamer_name}`,
    description: video.is_visible
      ? `Nonton replay ${video.title} dari ${video.streamer_name} — ${video.platform === 'idn' ? 'IDN Live' : 'Showroom'}.`
      : `Replay ${video.title} dari ${video.streamer_name} segera tayang.`,
    // Pra-rilis tidak boleh diindex mesin pencari sampai benar-benar terbit.
    robots: video.is_visible ? undefined : { index: false, follow: false },
    openGraph: {
      title: `${video.title} - ${video.streamer_name}`,
      description: `Replay live JKT48 — IDN & Showroom`,
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

  // URL kanonis memakai ID tersamar. Bila dibuka pakai YouTube ID mentah
  // (link lama / dibagikan manual), arahkan permanen ke bentuk tersamar.
  if (isRawYoutubeId(id) && video.youtube_video_id === id) {
    redirect(`/watch/${encodeWatchId(id)}`);
  }

  // Related: same member first, pad with recent other-member videos if <6.
  const { videos: memberVideos } = getAllVideos({
    member: video.streamer_username,
    limit: 6,
  });
  let filteredRelated = memberVideos.filter(
    (v) => v.youtube_video_id !== video.youtube_video_id
  );
  if (filteredRelated.length < 6) {
    const { videos: recent } = getAllVideos({ limit: 24 });
    const seen = new Set(filteredRelated.map((v) => v.youtube_video_id));
    seen.add(video.youtube_video_id);
    for (const v of recent) {
      if (filteredRelated.length >= 6) break;
      if (seen.has(v.youtube_video_id)) continue;
      seen.add(v.youtube_video_id);
      filteredRelated = [...filteredRelated, v];
    }
  }

  // Satu sumber konversi WIB (lib/wib.ts) — sama seperti yang membentuk judul,
  // sehingga baris detail tidak lagi berbeda 1 jam dari judul.
  const dateFormatted = formatWibLong(video.started_at) || video.started_at;

  // Pra-rilis: video belum boleh ditonton. Tampilkan countdown bila ada jadwal
  // rilis; jika tidak ada jadwal (mis. ambang dinonaktifkan) tetap tampilkan
  // panel "segera hadir" tanpa angka.
  const isPrerelease = !video.is_visible;

  return (
    <div className="container" style={{ paddingTop: '24px' }}>
      <div className="watch-layout">
        {/* Left Column: Player & Video Details */}
        <div>
          <div id="video-player-container">
            {isPrerelease ? (
              <CountdownTimer
                publishAt={video.publish_at || ''}
                title={video.title}
              />
            ) : (
              <VideoPlayer
                youtubeId={video.youtube_video_id}
                title={video.title}
                poster={video.thumbnail_url}
                platform={video.platform}
              />
            )}
          </div>
          {!isPrerelease && (
            <WatchTracker
              watchId={`/watch/${video.watch_id || video.youtube_video_id || video.id}`}
              title={video.title}
              thumbnailUrl={video.thumbnail_url}
              streamerName={video.streamer_name}
            />
          )}

          <div className="watch-details-card">
            <h1 className="watch-title">{video.title}</h1>

            <div className="watch-streamer-row">
              <Link href={`/?member=${video.streamer_username}`} className="streamer-profile-link">
                <div>
                  <h3 style={{ fontSize: '1rem', color: '#fff' }}>{video.streamer_name}</h3>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>
                    {dateFormatted}
                    {video.duration_formatted ? ` • ${video.duration_formatted}` : ''}
                  </p>
                </div>
              </Link>

              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                <span className={`platform-badge ${video.platform}`} style={{ position: 'static' }}>
                  {video.platform === 'idn' ? 'IDN' : 'Showroom'}
                </span>
                <ShareButton title={video.title} text={`Nonton ${video.title} di JKT48 Replay`} />
              </div>
            </div>

            {!isPrerelease && video.telegram_archived && (
              <div style={{ marginTop: '14px' }}>
                <TelegramDownloadButton
                  youtubeVideoId={video.youtube_video_id}
                  title={video.title}
                />
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Related Videos */}
        <aside>
          <h2 className="sidebar-title">
            Replay lain dari {video.streamer_name}
          </h2>
          <Link href={`/members/${encodeURIComponent(video.streamer_username)}`} className="text-button" style={{ marginBottom: '8px', display: 'inline-flex' }}>
            Semua dari {video.streamer_name} →
          </Link>

          {filteredRelated.length === 0 ? (
            <p style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>
              Belum ada replay lain dari member ini.
            </p>
          ) : (
            <div className="related-list">
              {filteredRelated.map((rel) => (
                <Link
                  key={rel.youtube_video_id || rel.id}
                  href={`/watch/${rel.watch_id || rel.youtube_video_id || rel.id}`}
                  className="related-card"
                >
                  <div className="related-thumb-box">
                    <Image
                      src={rel.thumbnail_url}
                      alt={rel.title}
                      fill
                      sizes="(max-width: 900px) 82vw, 120px"
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
