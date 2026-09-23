import { notFound } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { getPublicMember, getAllVideos } from '@/lib/db';
import { getMemberProfileLinks } from '@/lib/member-roster';
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
    alternates: { canonical: `/members/${encodeURIComponent(username)}` },
  };
}

interface LinkChip {
  href: string;
  label: string;
  tone?: 'live' | 'showroom' | 'social';
}

export default async function MemberDetailPage({ params }: Props) {
  const { username } = await params;
  const member = getPublicMember(username);
  if (!member) notFound();

  const { videos, total } = getAllVideos({ member: member.username, limit: 48 });
  const { socials, links, photo } = getMemberProfileLinks(
    member.username,
    member.display_name,
  );

  const avatar = photo || member.avatar_url;
  const officialName = socials?.name || member.display_name;
  const nickname = socials?.nickname || '';
  const type = socials?.type || '';

  const chips: LinkChip[] = [];
  if (links.idn) chips.push({ href: links.idn, label: 'Live IDN', tone: 'live' });
  if (links.showroom) chips.push({ href: links.showroom, label: 'Live Showroom', tone: 'showroom' });
  if (links.jkt48) chips.push({ href: links.jkt48, label: 'jkt48.com' });
  if (links.tiktok) chips.push({ href: links.tiktok, label: 'TikTok', tone: 'social' });
  if (links.instagram) chips.push({ href: links.instagram, label: 'Instagram', tone: 'social' });
  if (links.twitter) chips.push({ href: links.twitter, label: 'X / Twitter', tone: 'social' });

  return (
    <div className="container page-section">
      <header className="section-heading">
        <div>
          <p className="eyebrow">MEMBER</p>
          <h1>{officialName}</h1>
          <p>
            @{member.username} · {total || member.video_count} replay
            {nickname && nickname.toLowerCase() !== officialName.toLowerCase()
              ? ` · “${nickname}”`
              : ''}
            {type ? ` · ${type}` : ''}
          </p>
        </div>
        <Link href="/members" className="text-button">← Semua member</Link>
      </header>

      <div className="member-detail-hero">
        {avatar ? (
          <Image
            src={avatar}
            alt={officialName}
            width={72}
            height={72}
            className="member-avatar-lg member-detail-avatar"
            sizes="72px"
            priority
          />
        ) : (
          <span className="member-initial member-detail-avatar" aria-hidden="true">
            {officialName.slice(0, 2).toUpperCase()}
          </span>
        )}
        <div className="member-detail-meta">
          <p className="member-detail-meta-label">Kanal &amp; sosial</p>
          {chips.length > 0 ? (
            <nav className="member-links" aria-label={`Tautan ${officialName}`}>
              {chips.map((chip) => (
                <a
                  key={chip.href}
                  className={`member-link-chip${chip.tone ? ` ${chip.tone}` : ''}`}
                  href={chip.href}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {chip.label} <span aria-hidden="true">↗</span>
                </a>
              ))}
            </nav>
          ) : (
            <p className="help-text">Profil luar belum tersedia untuk member ini.</p>
          )}
        </div>
      </div>

      <p className="result-summary">{total} replay siap ditonton</p>
      {videos.length ? (
        <div className="video-grid" id="member-replays">
          {videos.map((video) => (
            <VideoCard key={video.content_uid || video.youtube_video_id || video.id} video={video} />
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
