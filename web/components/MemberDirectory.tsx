import Link from 'next/link';
import type { Metadata } from 'next';
import { getPublicMembers } from '@/lib/db';
export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Member', description: 'Temukan rekaman publik berdasarkan member favorit Anda.' };
export default async function MemberDirectory({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const { q = '' } = await searchParams;
  const members = getPublicMembers().filter(m => `${m.display_name} ${m.username}`.toLowerCase().includes(q.toLowerCase()));
  return <div className="container page-section"><header className="section-heading"><div><p className="eyebrow">DIREKTORI ARSIP</p><h1>Temukan member favoritmu</h1><p>Jelajahi koleksi rekaman yang tersedia untuk ditonton.</p></div></header>
    <form className="catalog-filters" action="/members"><label>Cari member<input type="search" name="q" defaultValue={q} placeholder="Nama atau username" maxLength={100} /></label><button className="primary-button">Cari</button>{q && <Link className="secondary-button" href="/members">Reset</Link>}</form>
    <p className="result-summary">{members.length} member dengan arsip publik</p>
    <div className="members-grid" id="members-list-grid">{members.map(m => <Link key={m.username} href={`/?member=${encodeURIComponent(m.username)}#catalog`} className="member-card"><span className="member-initial" aria-hidden="true">{m.display_name.slice(0, 2).toUpperCase()}</span><div><h2 className="member-card-title">{m.display_name}</h2><p className="member-card-username">@{m.username}</p></div><div className="member-card-stats">{m.video_count} rekaman <span aria-hidden="true">↗</span></div></Link>)}</div>
    {members.length === 0 && <div className="empty-state"><h2>Belum ada member ditemukan</h2><p>Coba nama lain atau kembali ke seluruh koleksi.</p><Link className="secondary-button" href="/">Jelajahi rekaman</Link></div>}
  </div>;
}
