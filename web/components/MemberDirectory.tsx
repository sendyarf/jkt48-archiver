import Link from 'next/link';
import Image from 'next/image';
import type { Metadata } from 'next';
import { Search } from 'lucide-react';
import { getPublicMembers } from '@/lib/db';
export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Member', description: 'Cari replay dari member favoritmu.' };
export default async function MemberDirectory({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const { q = '' } = await searchParams;
  const members = getPublicMembers().filter(m => `${m.display_name} ${m.username}`.toLowerCase().includes(q.toLowerCase()));
  return <div className="container page-section"><header className="section-heading"><div><p className="eyebrow">DAFTAR MEMBER</p><h1>Temukan member favoritmu</h1><p>Klik membernya buat lihat semua replay-nya.</p></div></header>
    <form className="catalog-filters" action="/members"><label className="catalog-search">Cari member<span className="input-with-icon"><Search size={18} aria-hidden="true" /><input type="search" name="q" defaultValue={q} placeholder="Nama atau username" maxLength={100} /></span></label><div className="filter-actions"><button className="primary-button">Cari</button>{q && <Link className="secondary-button" href="/members">Reset</Link>}</div></form>
    <p className="result-summary">{members.length} member punya replay di arsip</p>
    <div className="members-grid" id="members-list-grid">{members.map(m => <Link key={m.username} href={`/members/${encodeURIComponent(m.username)}`} className="member-card">{m.avatar_url ? <span className="member-avatar-lg member-avatar"><Image src={m.avatar_url} alt="" width={76} height={76} sizes="76px" /></span> : <span className="member-initial" aria-hidden="true">{m.display_name.slice(0, 2).toUpperCase()}</span>}<div><h2 className="member-card-title">{m.display_name}</h2><p className="member-card-username">@{m.username}</p></div><div className="member-card-stats">{m.video_count} replay <span aria-hidden="true">↗</span></div></Link>)}</div>
    {members.length === 0 && <div className="empty-state"><h2>Membernya belum ketemu</h2><p>Coba nama lain atau lihat semua member.</p><Link className="secondary-button" href="/">Lihat semua replay</Link></div>}
  </div>;
}
