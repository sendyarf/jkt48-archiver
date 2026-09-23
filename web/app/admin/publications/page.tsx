'use client';
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import type { Publication, PublicationStatusFilter, PublicationSummary } from '@/lib/db';

function platformBadge(platform: string) {
  const cls = platform === 'showroom' ? 'showroom' : 'idn';
  const label = platform === 'showroom' ? 'Showroom' : 'IDN';
  return <span className={`platform-badge inline-badge ${cls}`}>{label}</span>;
}

function statusBadge(v: Publication) {
  if (v.visible) {
    return <span className="visibility-badge published">{v.decided ? 'Tayang (manual)' : 'Tayang'}</span>;
  }
  if (v.decided && !v.published) {
    return <span className="visibility-badge held">Ditahan</span>;
  }
  return <span className="visibility-badge waiting">Menunggu</span>;
}

function statusHint(v: Publication, autoHours: number): string {
  if (v.visible) return v.decided ? 'Keputusan admin' : 'Rilis otomatis';
  if (v.decided && !v.published) return 'Ditahan — tidak tayang otomatis';
  if (v.hours_since_end === null) return 'Menunggu jam tayang';
  const elapsed = Math.floor(v.hours_since_end);
  if (autoHours <= 0) return 'Perlu persetujuan manual';
  const left = Math.max(0, autoHours - elapsed);
  if (left === 0) return 'Ambang lewat — belum dipublikasi';
  return `Otomatis dalam ±${left} jam`;
}

export default function PublicationsPage() {
  const router = useRouter();
  const [videos, setVideos] = useState<Publication[]>([]);
  const [summary, setSummary] = useState<PublicationSummary | null>(null);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<PublicationStatusFilter>('all');
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [autoHours, setAutoHours] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const qs = new URLSearchParams({ q: query, page: String(page), status });
    fetch(`/api/admin/publications?${qs}`, { cache: 'no-store', signal: controller.signal })
      .then(async (res) => {
        if (res.status === 401) router.replace('/login-r3pl4y');
        if (!res.ok) throw new Error('Gagal memuat replay.');
        return res.json();
      })
      .then((data) => {
        setVideos(data.videos);
        setMore(data.hasMore);
        setSummary(data.summary ?? null);
        setAutoHours(data.autoPublishAfterHours ?? 0);
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setMessage(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, page, status, revision, router]);

  const changeTab = useCallback((next: PublicationStatusFilter) => {
    setLoading(true);
    setMessage('');
    setPage(1);
    setStatus(next);
  }, []);

  async function mutate(video: Publication, body: Record<string, unknown>, confirmText: string, successText: string) {
    if (!window.confirm(confirmText)) return;
    setBusy(true);
    setMessage('');
    try {
      const res = await fetch('/api/admin/publications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ youtube_video_id: video.youtube_video_id, ...body }),
      });
      if (res.status === 401) router.replace('/login-r3pl4y');
      if (!res.ok) throw new Error('Gagal memperbarui publikasi.');
      setMessage(successText);
      setRevision((r) => r + 1);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Koneksi gagal.');
    } finally {
      setBusy(false);
    }
  }

  function rowActions(v: Publication) {
    if (v.visible) {
      return (
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => mutate(v, { published: false }, 'Tarik replay ini dari semua halaman publik?', 'Replay ditarik dari situs.')}
        >
          Tarik
        </button>
      );
    }
    if (v.decided && !v.published) {
      return (
        <div className="row-actions">
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => mutate(v, { published: true }, 'Tayangkan replay ini sekarang?', 'Replay ditayangkan.')}
          >
            Terbitkan
          </button>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => mutate(v, { action: 'reset' }, 'Lepas tahan? Replay kembali mengikuti aturan otomatis.', 'Tahan dilepas — aturan otomatis berlaku lagi.')}
          >
            Lepas tahan
          </button>
        </div>
      );
    }
    return (
      <div className="row-actions">
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => mutate(v, { published: true }, 'Tayangkan replay ini sekarang?', 'Replay ditayangkan.')}
        >
          Terbitkan
        </button>
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => mutate(v, { published: false }, 'Tahan replay ini? Tidak akan tayang otomatis setelah jeda rilis.', 'Replay ditahan — tidak akan tayang otomatis.')}
        >
          Tahan
        </button>
      </div>
    );
  }

  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">KURASI ARSIP</p>
          <h1>Publikasi</h1>
          <p>
            {autoHours > 0
              ? `IDN tayang otomatis setelah ${autoHours} jam; Showroom langsung. Kamu bisa terbitkan lebih cepat atau menahan.`
              : 'Tayang hanya setelah persetujuan; Showroom langsung tayang.'}
          </p>
        </div>
      </div>

      {summary && (
        <div className="stat-chips" role="group" aria-label="Ringkasan publikasi">
          <button type="button" className={`stat-chip ${status === 'all' ? 'active' : ''}`} onClick={() => changeTab('all')}>
            <strong>{summary.total}</strong>
            <span>Semua</span>
          </button>
          <button type="button" className={`stat-chip ${status === 'waiting' ? 'active' : ''}`} onClick={() => changeTab('waiting')}>
            <strong>{summary.waiting}</strong>
            <span>Menunggu</span>
          </button>
          <button type="button" className={`stat-chip ${status === 'visible' ? 'active' : ''}`} onClick={() => changeTab('visible')}>
            <strong>{summary.visible}</strong>
            <span>Tampil</span>
          </button>
          <button type="button" className={`stat-chip ${status === 'held' ? 'active' : ''}`} onClick={() => changeTab('held')}>
            <strong>{summary.held}</strong>
            <span>Ditahan</span>
          </button>
        </div>
      )}

      <p className="notice">
        Terbitkan hanya konten yang boleh dibagikan publik. Status unlisted di YouTube bukan
        berarti otomatis boleh tayang di sini.
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
          Cari replay
          <input name="q" type="search" maxLength={100} placeholder="Judul atau member" />
        </label>
        <button className="secondary-button">Cari</button>
      </form>

      <div role="status" aria-live="polite">
        {message && <p className="notice">{message}</p>}
      </div>

      <div className="panel table-scroll">
        <table className="admin-table">
          <caption className="sr-only">Persetujuan publikasi replay</caption>
          <thead>
            <tr>
              <th>Replay</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!loading &&
              videos.map((v) => (
                <tr key={v.youtube_video_id}>
                  <td>
                    <div className="cell-title">{v.title}</div>
                    <div className="cell-meta">
                      {platformBadge(v.platform)}
                      <span>{v.member_name}</span>
                      <span>{v.hours_since_end === null ? 'Waktu tidak diketahui' : `${Math.floor(v.hours_since_end)} jam lalu`}</span>
                      <a href={`https://www.youtube.com/watch?v=${v.youtube_video_id}`} target="_blank" rel="noopener noreferrer">
                        YouTube ↗
                      </a>
                    </div>
                  </td>
                  <td>
                    {statusBadge(v)}
                    <div className="cell-meta">{statusHint(v, autoHours)}</div>
                  </td>
                  <td>{rowActions(v)}</td>
                </tr>
              ))}
          </tbody>
        </table>
        <p className="help-text">
          {loading
            ? 'Memuat replay…'
            : videos.length === 0
              ? status === 'all'
                ? 'Belum ada replay yang cocok.'
                : 'Tidak ada replay dengan status ini.'
              : ''}
        </p>
      </div>

      <nav className="pagination" aria-label="Halaman publikasi">
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
