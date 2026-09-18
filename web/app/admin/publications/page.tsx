'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import type { Publication } from '@/lib/db';
export default function PublicationsPage() {
  const router = useRouter();
  const [videos, setVideos] = useState<Publication[]>([]);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [autoHours, setAutoHours] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/admin/publications?q=${encodeURIComponent(query)}&page=${page}`, { cache: 'no-store', signal: controller.signal })
      .then(async res => { if (res.status === 401) router.replace('/login'); if (!res.ok) throw new Error('Gagal memuat rekaman.'); return res.json(); })
      .then(data => { setVideos(data.videos); setMore(data.hasMore); setAutoHours(data.autoPublishAfterHours ?? 0); })
      .catch(e => { if (e.name !== 'AbortError') setMessage(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, page, revision, router]);
  const label = (v: Publication) => {
    if (v.decided && v.published) return 'Disetujui manual';
    if (v.decided && !v.published) return 'Ditahan manual';
    if (v.visible) return `Otomatis (lewat ${autoHours} jam)`;
    return 'Menunggu waktu rilis';
  };
  async function toggle(video: Publication) {
    const nextPublished = !video.visible;
    if (!window.confirm(nextPublished ? 'Pastikan konten ini diizinkan untuk publik. Terbitkan rekaman?' : 'Tarik rekaman ini dari seluruh halaman publik?')) return;
    setBusy(true); setMessage('');
    try {
      const res = await fetch('/api/admin/publications', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ youtube_video_id: video.youtube_video_id, published: nextPublished }) });
      if (res.status === 401) router.replace('/login');
      if (!res.ok) throw new Error('Gagal memperbarui publikasi.');
      setVideos(items => items.map(v => v.youtube_video_id === video.youtube_video_id ? { ...v, published: nextPublished ? 1 : 0, decided: 1, visible: nextPublished ? 1 : 0 } : v));
      setMessage('Publikasi diperbarui.'); router.refresh();
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Koneksi gagal.'); }
    finally { setBusy(false); }
  }
  return <section><div className="section-heading"><div><p className="eyebrow">KURASI ARSIP</p><h1>Publikasi</h1><p>{autoHours > 0 ? `Rekaman tampil otomatis ${autoHours} jam setelah live selesai. Anda tetap bisa menerbitkan lebih cepat atau menahannya.` : 'Rekaman baru tersembunyi sampai Anda menyetujuinya.'}</p></div></div>
    <p className="notice">Hanya terbitkan konten yang memiliki izin distribusi publik. Status unlisted YouTube bukan persetujuan publikasi.</p>
    <form className="form-row" onSubmit={e => { e.preventDefault(); setRevision(r => r + 1); setLoading(true); setMessage(''); setPage(1); setQuery(String(new FormData(e.currentTarget).get('q') || '')); }}><label>Cari rekaman<input name="q" type="search" maxLength={100} placeholder="Judul atau member" /></label><button className="secondary-button">Cari</button></form>
    <div role="status" aria-live="polite">{message && <p className="notice">{message}</p>}</div>
    <div className="panel table-scroll"><table className="admin-table"><caption className="sr-only">Persetujuan publikasi rekaman</caption><thead><tr><th>Rekaman</th><th>Visibilitas situs</th><th>Tindakan</th></tr></thead><tbody>
      {!loading && videos.map(v => <tr key={v.youtube_video_id}><td><strong>{v.title}</strong><br /><span className="help-text">{v.member_name}</span><br /><span className="help-text">{v.hours_since_end === null ? 'Waktu tidak diketahui' : `${Math.floor(v.hours_since_end)} jam sejak live selesai`}</span><br /><a href={`https://www.youtube.com/watch?v=${v.youtube_video_id}`} target="_blank" rel="noopener noreferrer">Tinjau di YouTube ↗</a></td><td><span className={`visibility-badge ${v.visible ? 'published' : ''}`}>{v.visible ? 'Publik' : 'Tersembunyi'}</span><br /><span className="help-text">{label(v)}</span></td><td><button className="secondary-button" disabled={busy} onClick={() => toggle(v)}>{v.visible ? 'Tarik publikasi' : 'Terbitkan'}</button></td></tr>)}
    </tbody></table><p className="help-text">{loading ? 'Memuat rekaman…' : videos.length === 0 ? 'Tidak ada rekaman ditemukan.' : ''}</p></div>
    <nav className="pagination" aria-label="Halaman publikasi"><button className="secondary-button" disabled={page === 1 || loading || busy} onClick={() => { setLoading(true); setPage(p => p - 1); }}>Sebelumnya</button><span>Halaman {page}</span><button className="secondary-button" disabled={!more || loading || busy} onClick={() => { setLoading(true); setPage(p => p + 1); }}>Berikutnya</button></nav>
  </section>;
}
