import Link from 'next/link';
import { getAllVideos, getPublicMembers, getUpcomingVideos } from '@/lib/db';
import VideoCard from '@/components/VideoCard';
import HeroSpotlight from '@/components/HeroSpotlight';
import CatalogFilterForm from '@/components/CatalogFilterForm';
import ContinueWatching from '@/components/ContinueWatching';
import { PlayCircle } from 'lucide-react';
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
  // getAllVideos halaman 1 TANPA filter, sehingga nol query tambahan. Hero
  // hanya tampil di halaman depan tanpa filter: saat pengunjung memakai
  // filter/pencarian/halaman 2+, panel hero tidak dirender sama sekali supaya
  // banner tidak menampilkan replay yang tidak sesuai hasil.
  // Judul hero sekaligus jadi h1 halaman; saat hero tidak ada, h1 diambil alih
  // judul section katalog.
  const hero = !filtered && result.page === 1 ? result.videos[0] : undefined;
  const heroWatchUrl = hero ? `/watch/${hero.watch_id || hero.content_uid || hero.youtube_video_id || hero.id}` : '/#catalog';
  const catalogHeading = filtered ? 'Hasil pencarian' : 'Replay terbaru';
  return <div className="public-catalog">
    {hero ? <section className="container catalog-hero-block"><HeroSpotlight video={hero} watchUrl={heroWatchUrl} /></section> : null}
    <ContinueWatching />
    <section className="container page-section" id="catalog"><header className="section-heading"><div><p className="eyebrow">REPLAY</p>{hero ? <h2>{catalogHeading}</h2> : <h1>{catalogHeading}</h1>}</div><p>Terbaru dulu</p></header>
      <CatalogFilterForm q={q} member={member} platform={platform} members={members} />
      <p className="result-summary">{result.total} replay siap ditonton{upcoming.length > 0 ? ` (+${upcoming.length} segera hadir)` : ''}{q && <> untuk “{q}”</>}</p>
      {merged.length ? <div className="video-grid" id="main-video-grid">{merged.map(video => <VideoCard key={video.content_uid || video.youtube_video_id || video.id} video={video} />)}</div> : <div className="empty-state"><PlayCircle size={40} aria-hidden="true" /><h2>{filtered ? 'Belum ada replay yang cocok' : 'Arsipnya masih kosong'}</h2><p>{filtered ? 'Coba kata kunci lain atau ubah filternya.' : 'Replay yang sudah tayang bakal muncul di sini.'}</p>{(filtered || result.page > 1) && <Link className="secondary-button" href="/#catalog">Lihat semua replay</Link>}</div>}
      {(result.totalPages > 1 || result.page > 1) && <nav className="pagination" aria-label="Halaman katalog">{result.page > 1 ? <Link className="secondary-button" href={pageUrl(result.page - 1)}>← Sebelumnya</Link> : <span /> }<span>Halaman {result.page} / {result.totalPages}</span>{result.page < result.totalPages ? <Link className="secondary-button" href={pageUrl(result.page + 1)}>Berikutnya →</Link> : <span />}</nav>}
    </section>
  </div>;
}
