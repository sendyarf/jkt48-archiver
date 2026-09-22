'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import type { AdminVideo } from '@/lib/db';

const VISIBILITY_OPTIONS = [
  { value: 'all', label: 'Semua' },
  { value: 'visible', label: 'Tampil' },
  { value: 'hidden', label: 'Tersembunyi' },
] as const;

export default function AdminVideosPage() {
  const router = useRouter();
  const [videos, setVideos] = useState<AdminVideo[]>([]);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [visibility, setVisibility] = useState<'all' | 'visible' | 'hidden'>('all');
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const qs = new URLSearchParams({ q: query, page: String(page), visibility });
    fetch(`/api/admin/videos?${qs}`, { cache: 'no-store', signal: controller.signal })
      .then(async (res) => {
        if (res.status === 401) router.replace('/login');
        if (!res.ok) throw new Error('Gagal memuat daftar video.');
        return res.json();
      })
      .then((data) => {
        setVideos(data.videos);
        setMore(data.hasMore);
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setMessage(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, page, visibility, revision, router]);

  async function setVisibilityAction(video: AdminVideo, nextVisible: boolean, reset = false) {
    const confirmText = reset
      ? 'Hapus override untuk konten ini? Kembali mengikuti aturan otomatis.'
      : nextVisible
        ? 'Tampilkan konten ini di situs publik?'
        : 'Sembunyikan konten ini dari semua halaman publik (katalog, watch, pencarian)?';
    if (!window.confirm(confirmText)) return;
    setBusy(true);
    setMessage('');
    try {
      const res = await fetch('/api/admin/videos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          reset
            ? { content_key: video.content_key, action: 'reset' }
            : { content_key: video.content_key, visible: nextVisible },
        ),
      });
      if (res.status === 401) router.replace('/login');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || 'Gagal memperbarui visibilitas.');
      setMessage(data.message || 'Diperbarui.');
      setRevision((r) => r + 1);
      router.refresh();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Koneksi gagal.');
    } finally {
      setBusy(false);
    }
  }

  const platformLabel = (p: string) =>
    p === 'showroom' ? 'Showroom' : p === 'tiktok' ? 'TikTok' : 'IDN';

  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">KURASI ARSIP</p>
          <h1>Sembunyikan video</h1>
          <p>
            Soft-hide per konten: hilang dari katalog, watch, pencarian, dan direktori
            member — baris database & file tetap aman. Berlaku untuk video YouTube
            maupun arsip Telegram-first. Bisa di-undo.
          </p>
        </div>
      </div>
      <p className="notice">
        Duplikat hasil sebelum perbaikan bot dapat disembunyikan di sini tanpa menghapus
        data. Video di kanal YouTube tidak ikut terhapus — kelola terpisah di YouTube Studio.
      </p>

      <form
        className="form-row"
        onSubmit={(e) => {
          e.preventDefault();
          setLoading(true);
          setMessage('');
          setPage(1);
          setQuery(String(new FormData(e.currentTarget).get('q') || ''));
          setRevision((r) => r + 1);
        }}
      >
        <label>
          Cari konten
          <input name="q" type="search" maxLength={100} placeholder="Judul, member, atau content key" />
        </label>
        <label>
          Tampilan
          <select
            value={visibility}
            onChange={(e) => {
              setLoading(true);
              setPage(1);
              setVisibility(e.target.value as typeof visibility);
            }}
          >
            {VISIBILITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button className="secondary-button">Cari</button>
      </form>

      <div role="status" aria-live="polite">
        {message && <p className="notice">{message}</p>}
      </div>

      <div className="panel table-scroll">
        <table className="admin-table">
          <caption className="sr-only">Daftar video untuk disembunyikan</caption>
          <thead>
            <tr>
              <th>Konten</th>
              <th>Platform</th>
              <th>Sumber</th>
              <th>Visibilitas</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!loading &&
              videos.map((v) => (
                <tr key={v.content_key}>
                  <td>
                    <strong>{v.title}</strong>
                    <br />
                    <span className="help-text">{v.member_name}</span>
                    <br />
                    <span className="help-text">
                      Key: <code>{v.content_key}</code>
                      {v.row_count > 1 ? ` · ${v.row_count} baris` : ''}
                    </span>
                    {v.youtube_video_id && (
                      <>
                        <br />
                        <a
                          href={`https://www.youtube.com/watch?v=${v.youtube_video_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          YouTube ↗
                        </a>
                      </>
                    )}
                  </td>
                  <td>
                    <span className={`platform-badge ${v.platform === 'showroom' ? 'showroom' : 'idn'}`}>
                      {platformLabel(v.platform)}
                    </span>
                  </td>
                  <td>
                    <span className="help-text">
                      {v.has_yt ? 'YouTube' : ''}
                      {v.has_yt && v.has_tg ? ' + ' : ''}
                      {v.has_tg ? 'Telegram' : ''}
                      {!v.has_yt && !v.has_tg ? '—' : ''}
                    </span>
                    <br />
                    <span className="help-text">
                      {v.hours_since_end === null
                        ? 'Waktu tidak diketahui'
                        : `${Math.floor(v.hours_since_end)} jam lalu`}
                    </span>
                  </td>
                  <td>
                    <span className={`visibility-badge ${v.visible ? 'published' : ''}`}>
                      {v.visible ? 'Tampil' : 'Tersembunyi'}
                    </span>
                    <br />
                    <span className="help-text">
                      {v.override === 1
                        ? 'Override admin: terbit'
                        : v.override === 0
                          ? 'Override admin: tahan'
                          : 'Ikut aturan otomatis'}
                    </span>
                  </td>
                  <td>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      {v.visible ? (
                        <button
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => setVisibilityAction(v, false)}
                        >
                          Sembunyikan
                        </button>
                      ) : (
                        <button
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => setVisibilityAction(v, true)}
                        >
                          Tampilkan
                        </button>
                      )}
                      {v.override !== null && (
                        <button
                          className="secondary-button"
                          disabled={busy}
                          onClick={() => setVisibilityAction(v, true, true)}
                        >
                          Reset otomatis
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        <p className="help-text">
          {loading
            ? 'Memuat video…'
            : videos.length === 0
              ? 'Belum ada konten yang cocok.'
              : ''}
        </p>
      </div>

      <nav className="pagination" aria-label="Halaman video">
        <button
          className="secondary-button"
          disabled={page === 1 || loading || busy}
          onClick={() => {
            setLoading(true);
            setPage((p) => p - 1);
          }}
        >
          Sebelumnya
        </button>
        <span>Halaman {page}</span>
        <button
          className="secondary-button"
          disabled={!more || loading || busy}
          onClick={() => {
            setLoading(true);
            setPage((p) => p + 1);
          }}
        >
          Berikutnya
        </button>
      </nav>
    </section>
  );
}
