import Link from 'next/link';
import { getAllVideos, getPublicMembers, getUpcomingVideos } from '@/lib/db';
import VideoCard from '@/components/VideoCard';
import HeroSpotlightThumb from '@/components/HeroSpotlightThumb';
import { ArrowUpRight, MonitorPlay, PlayCircle, Search, User } from 'lucide-react';
export default async function PublicCatalog({ searchParams }: { searchParams: Promise<{ q?: string; member?: string; platform?: string; page?: string }> }) {
  const params = await searchParams;
  const q = (params.q || '').slice(0, 100);
  const member = params.member || '';
  const platform = params.platform || '';
  const result = getAllVideos({ search: q, member, platform, page: parseInt(params.page || '1', 10), limit: 24 });
  const members = getPublicMembers();
  const filtered = !!(q || member || platform);
  // Rekaman pra-rilis ("Segera") digabung ke grid utama (bukan section terpisah)
  // lalu diurutkan bersama rekaman yang sudah terbit. Jumlahnya TIDAK dibatasi —
  // dulu hanya 6 item terdekat yang diambil, sehingga konten pra-rilis terbaru
  // tidak pernah muncul di grid. Pencarian kata kunci (q) tetap murni; filter
  // member/platform tetap menampilkan pra-rilis yang cocok. Hanya dipasang di
  // halaman 1 agar tidak ada kartu ganda saat berpindah halaman.
  const upcoming = !q && result.page === 1 ? getUpcomingVideos({ member, platform }) : [];
  const merged = [...upcoming, ...result.videos].sort(
    (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
  );
  const pageUrl = (page: number) => { const p = new URLSearchParams(); if(q) p.set('q', q); if(member) p.set('member', member); if(platform) p.set('platform', platform); p.set('page', String(page)); return `/?${p}#catalog`; };
  // Spotlight hero: replay terbit terbaru — diambil dari baris pertama hasil
  // getAllVideos halaman 1 TANPA filter, sehingga nol query tambahan. Saat
  // pengunjung memakai filter/pencarian/halaman 2+, panel kanan disembunyikan
  // (hero jadi satu kolom) — spotlight tidak ikut berubah mengikuti filter.
  const hero = !filtered && result.page === 1 ? result.videos[0] : undefined;
  const heroWatchUrl = hero ? `/watch/${hero.watch_id || hero.youtube_video_id}` : '/#catalog';
  return <div className="public-catalog">
    <section className="container catalog-hero"><div><p className="eyebrow">ARSIP REPLAY · KOMUNITAS</p><h1>Momen favorit.<br /><span>Bisa ditonton lagi.</span></h1><p className="hero-copy">Semua replay live JKT48 di satu tempat. Cari berdasarkan member, judul, atau tanggal.</p><div className="hero-actions"><a className="primary-button" href="#catalog"><PlayCircle size={18} /> Jelajahi replay</a><Link href="/members" className="text-button">Lihat member <ArrowUpRight size={16} /></Link></div></div>{hero ? <article className="hero-spotlight"><HeroSpotlightThumb video={hero} watchUrl={heroWatchUrl} /><div className="hero-spotlight-body"><p className="eyebrow">{hero.is_new ? 'BARU TERBIT' : 'REPLAY TERBARU'}</p><Link href={heroWatchUrl}><h2 className="hero-spotlight-title" title={hero.title}>{hero.title}</h2></Link><p className="hero-spotlight-meta">{hero.streamer_name} · {hero.date_display || hero.started_at}</p><Link href={heroWatchUrl} className="primary-button"><PlayCircle size={18} /> Tonton sekarang</Link></div></article> : <div className="hero-note"><p>ARSIP REPLAY</p><span className="hero-note-foot">Replay yang sudah terbit akan tampil di sini.</span></div>}</section>
    <section className="container page-section" id="catalog"><header className="section-heading"><div><p className="eyebrow">REPLAY</p><h2>{filtered ? 'Hasil pencarian' : 'Replay terbaru'}</h2></div><p>Terbaru lebih dulu</p></header>
      <form action="/#catalog" method="GET" className="catalog-filters"><label className="catalog-search">Cari replay<span className="input-with-icon"><Search size={18} aria-hidden="true" /><input name="q" type="search" id="catalog-search-input" defaultValue={q} maxLength={100} placeholder="Judul, member, atau tanggal" /></span></label><label className="catalog-select">Member<span className="select-with-icon"><User size={18} aria-hidden="true" /><select name="member" defaultValue={member}><option value="">Semua member</option>{member && !members.some(m => m.username === member) && <option value={member}>{member}</option>}{members.map(m => <option value={m.username} key={m.username}>{m.display_name}</option>)}</select></span></label><label className="catalog-select">Platform<span className="select-with-icon"><MonitorPlay size={18} aria-hidden="true" /><select name="platform" defaultValue={platform}><option value="">Semua platform</option><option value="idn">IDN Live</option><option value="showroom">Showroom</option></select></span></label><div className="filter-actions"><button className="primary-button">Terapkan</button>{filtered && <Link href="/#catalog" className="text-button">Reset</Link>}</div></form>
      <p className="result-summary">{result.total} replay tersedia{upcoming.length > 0 ? ` (+${upcoming.length} segera hadir)` : ''}{q && <> untuk “{q}”</>}</p>
      {merged.length ? <div className="video-grid" id="main-video-grid">{merged.map(video => <VideoCard key={video.youtube_video_id} video={video} />)}</div> : <div className="empty-state"><PlayCircle size={40} aria-hidden="true" /><h2>{filtered ? 'Replay tidak ditemukan' : 'Arsip masih kosong'}</h2><p>{filtered ? 'Coba kata kunci lain, atau reset filter.' : 'Replay yang sudah terbit akan tampil di sini.'}</p>{(filtered || result.page > 1) && <Link className="secondary-button" href="/#catalog">Lihat semua replay</Link>}</div>}
      {(result.totalPages > 1 || result.page > 1) && <nav className="pagination" aria-label="Halaman katalog">{result.page > 1 ? <Link className="secondary-button" href={pageUrl(result.page - 1)}>← Sebelumnya</Link> : <span /> }<span>Halaman {result.page} / {result.totalPages}</span>{result.page < result.totalPages ? <Link className="secondary-button" href={pageUrl(result.page + 1)}>Berikutnya →</Link> : <span />}</nav>}
    </section>
  </div>;
}
