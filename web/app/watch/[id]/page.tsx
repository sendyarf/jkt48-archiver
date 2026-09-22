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

  // Kunci konten: YouTube ID bila ada; selain itu content_uid (arsip TG-first).
  const contentKey = video.youtube_video_id || video.content_uid || '';

  // Related: same member first, pad with recent other-member videos if <6.
  const { videos: memberVideos } = getAllVideos({
    member: video.streamer_username,
    limit: 6,
  });
  const relKey = (v: typeof video) => v.content_uid || v.youtube_video_id || String(v.id);
  let filteredRelated = memberVideos.filter(
    (v) => relKey(v) !== relKey(video)
  );
  if (filteredRelated.length < 6) {
    const { videos: recent } = getAllVideos({ limit: 24 });
    const seen = new Set(filteredRelated.map(relKey));
    seen.add(relKey(video));
    for (const v of recent) {
      if (filteredRelated.length >= 6) break;
      if (seen.has(relKey(v))) continue;
      seen.add(relKey(v));
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
            ) : video.youtube_video_id ? (
              <VideoPlayer
                youtubeId={video.youtube_video_id}
                title={video.title}
                poster={video.thumbnail_url}
                platform={video.platform}
              />
            ) : (
              /* Arsip TG-first: YouTube belum ada — panel placeholder + unduh. */
              <div className="countdown-panel" role="status">
                <p className="countdown-label">Arsip Telegram siap</p>
                <h2 className="countdown-title">{video.title}</h2>
                <p className="countdown-sub">
                  Pemutar YouTube sedang disiapkan. Sementara itu, unduh lewat bot
                  Telegram di bawah — atau tunggu permukaan terbit otomatis.
                </p>
              </div>
            )}
          </div>
          {!isPrerelease && video.youtube_video_id && (
            <WatchTracker
              watchId={`/watch/${video.watch_id || contentKey}`}
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

            {video.telegram_archived && (
              <div style={{ marginTop: '14px' }}>
                <TelegramDownloadButton
                  youtubeVideoId={video.youtube_video_id}
                  payload={contentKey}
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
                  key={rel.content_uid || rel.youtube_video_id || rel.id}
                  href={`/watch/${rel.watch_id || rel.content_uid || rel.youtube_video_id || rel.id}`}
                  className="related-card"
                >
                  <div className="related-thumb-box">
                    {rel.thumbnail_url ? (
                      <Image
                        src={rel.thumbnail_url}
                        alt={rel.title}
                        fill
                        sizes="(max-width: 900px) 82vw, 120px"
                        className="related-thumb"
                      />
                    ) : (
                      <span className="video-thumbnail-placeholder" aria-hidden="true" />
                    )}
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
