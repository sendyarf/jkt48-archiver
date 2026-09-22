import { notFound } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { getPublicMember, getAllVideos } from '@/lib/db';
import { getTikTokAccounts } from '@/lib/tiktok';
import VideoCard from '@/components/VideoCard';

export const dynamic = 'force-dynamic';

interface Props {
  params: Promise<{ username: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { username } = await params;
  const member = getPublicMember(username);
  if (!member) return { title: 'Member tidak ditemukan' };
  return {
    title: `${member.display_name}`,
    description: `Replay live ${member.display_name} di arsip JKT48 Replay.`,
  };
}

export default async function MemberDetailPage({ params }: Props) {
  const { username } = await params;
  const member = getPublicMember(username);
  if (!member) notFound();

  const { videos, total } = getAllVideos({ member: member.username, limit: 48 });
  const accounts = getTikTokAccounts().filter(
    (a) => a.member_username?.toLowerCase() === member.username.toLowerCase()
  );

  return (
    <div className="container page-section">
      <header className="section-heading">
        <div>
          <p className="eyebrow">MEMBER</p>
          <h1>{member.display_name}</h1>
          <p>@{member.username} · {total || member.video_count} replay</p>
        </div>
        <Link href="/members" className="text-button">← Semua member</Link>
      </header>

      <div className="member-detail-hero">
        {member.avatar_url ? (
          <Image
            src={member.avatar_url}
            alt={member.display_name}
            width={96}
            height={96}
            className="member-avatar-lg"
            sizes="96px"
          />
        ) : (
          <span className="member-initial" aria-hidden="true">
            {member.display_name.slice(0, 2).toUpperCase()}
          </span>
        )}
        <div>
          <h2 className="member-card-title">{member.display_name}</h2>
          <p className="member-card-username">@{member.username}</p>
          {accounts.length > 0 && (
            <a
              className="text-button"
              href={`https://www.tiktok.com/@${accounts[0].unique_id}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              TikTok: @{accounts[0].unique_id} ↗
            </a>
          )}
        </div>
      </div>

      <p className="result-summary">{total} replay siap ditonton</p>
      {videos.length ? (
        <div className="video-grid" id="member-replays">
          {videos.map((video) => (
            <VideoCard key={video.youtube_video_id} video={video} />
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <h2>Belum ada replay</h2>
          <p>Replay member ini bakal muncul di sini setelah terbit.</p>
          <Link className="secondary-button" href="/#catalog">Lihat semua replay</Link>
        </div>
      )}
    </div>
  );
}
