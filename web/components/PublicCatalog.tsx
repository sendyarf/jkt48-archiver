import Link from 'next/link';
import { getAllVideos, getPublicMembers } from '@/lib/db';
import VideoCard from '@/components/VideoCard';
import { ArrowUpRight, PlayCircle, Search } from 'lucide-react';
export default async function PublicCatalog({ searchParams }: { searchParams: Promise<{ q?: string; member?: string; platform?: string; page?: string }> }) {
  const params = await searchParams;
  const q = (params.q || '').slice(0, 100);
  const member = params.member || '';
  const platform = params.platform || '';
  const result = getAllVideos({ search: q, member, platform, page: parseInt(params.page || '1', 10), limit: 24 });
  const members = getPublicMembers();
  const filtered = !!(q || member || platform);
  const pageUrl = (page: number) => { const p = new URLSearchParams(); if(q) p.set('q', q); if(member) p.set('member', member); if(platform) p.set('platform', platform); p.set('page', String(page)); return `/?${p}#catalog`; };
  return <div className="public-catalog">
    <section className="container catalog-hero"><div><p className="eyebrow">JKT48 LIVE ARCHIVE · KOMUNITAS</p><h1>Momen favorit.<br /><span>Bisa ditonton lagi.</span></h1><p className="hero-copy">Temukan siaran ulang member favoritmu dalam satu koleksi. Pilih rekaman, duduk nyaman, dan nikmati momennya.</p><div className="hero-actions"><a className="primary-button" href="#catalog"><PlayCircle size={18} /> Jelajahi rekaman</a><Link href="/members" className="text-button">Temukan member <ArrowUpRight size={16} /></Link></div></div><div className="hero-note"><span className="hero-note-mark" aria-hidden="true">48</span><p>REPLAY COLLECTION</p><h2>Cerita kecil,<br />kenangan berharga.</h2><span>Arsip komunitas, bukan situs resmi JKT48.</span></div></section>
    <section className="container page-section" id="catalog"><header className="section-heading"><div><p className="eyebrow">KOLEKSI</p><h2>{filtered ? 'Hasil penelusuran' : 'Rekaman terbaru'}</h2></div><p>Diurutkan dari yang terbaru</p></header>
      <form action="/#catalog" method="GET" className="catalog-filters"><label className="catalog-search">Cari rekaman<span className="input-with-icon"><Search size={18} aria-hidden="true" /><input name="q" type="search" id="catalog-search-input" defaultValue={q} maxLength={100} placeholder="Judul atau nama member" /></span></label><label>Member<select name="member" defaultValue={member}><option value="">Semua member</option>{member && !members.some(m => m.username === member) && <option value={member}>{member}</option>}{members.map(m => <option value={m.username} key={m.username}>{m.display_name}</option>)}</select></label><label>Platform<select name="platform" defaultValue={platform}><option value="">Semua platform</option><option value="idn">IDN Live</option><option value="showroom">Showroom</option></select></label><button className="primary-button">Terapkan</button>{filtered && <Link href="/#catalog" className="text-button">Reset</Link>}</form>
      <p className="result-summary">{result.total} rekaman tersedia{q && <> untuk “{q}”</>}</p>
      {result.videos.length ? <div className="video-grid" id="main-video-grid">{result.videos.map(video => <VideoCard key={video.youtube_video_id} video={video} />)}</div> : <div className="empty-state"><PlayCircle size={40} aria-hidden="true" /><h2>{filtered ? 'Tidak ada rekaman yang cocok' : 'Koleksi sedang disiapkan'}</h2><p>{filtered ? 'Coba kata kunci lain atau hapus filter pencarian.' : 'Rekaman yang telah dipublikasikan akan muncul di sini. Silakan kembali lagi nanti.'}</p>{(filtered || result.page > 1) && <Link className="secondary-button" href="/#catalog">Lihat semua rekaman</Link>}</div>}
      {(result.totalPages > 1 || result.page > 1) && <nav className="pagination" aria-label="Halaman katalog">{result.page > 1 ? <Link className="secondary-button" href={pageUrl(result.page - 1)}>← Sebelumnya</Link> : <span /> }<span>Halaman {result.page} / {result.totalPages}</span>{result.page < result.totalPages ? <Link className="secondary-button" href={pageUrl(result.page + 1)}>Berikutnya →</Link> : <span />}</nav>}
    </section>
  </div>;
}
