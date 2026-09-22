'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import type { QueueItem, QueueSummary } from '@/lib/db';
import { platformLabel, StatusBadge } from '@/components/admin-status';

const SOURCES = [
  { value: 'all', label: 'Semua sumber' },
  { value: 'live', label: 'Live (IDN + Showroom)' },
  { value: 'tiktok', label: 'TikTok' },
] as const;

const PLATFORMS = [
  { value: '', label: 'Semua platform' },
  { value: 'idn', label: 'IDN' },
  { value: 'showroom', label: 'Showroom' },
  { value: 'tiktok', label: 'TikTok' },
] as const;

const STATUS_OPTIONS = [
  { value: 'all', label: 'Semua status' },
  { value: 'failed', label: 'Gagal' },
  { value: 'pending_upload', label: 'Menunggu retry' },
  { value: 'downloading', label: 'Mengunduh' },
  { value: 'merging', label: 'Merge' },
  { value: 'uploading_telegram', label: 'Upload Telegram' },
  { value: 'uploading_youtube', label: 'Upload YouTube' },
];

const DEST_LABEL: Record<string, string> = {
  download: 'Unduh',
  merge: 'Merge',
  telegram: 'Telegram',
  youtube: 'YouTube',
};

function formatSize(bytes: number): string {
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

export default function AdminQueuePage() {
  const router = useRouter();
  const [items, setItems] = useState<QueueItem[]>([]);
  const [summary, setSummary] = useState<QueueSummary | null>(null);
  const [source, setSource] = useState<string>('all');
  const [platform, setPlatform] = useState<string>('');
  const [status, setStatus] = useState<string>('all');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const qs = new URLSearchParams({ source, platform, status, limit: '150' });
    fetch(`/api/admin/queue?${qs}`, { cache: 'no-store', signal: controller.signal })
      .then(async (res) => {
        if (res.status === 401) router.replace('/login');
        if (!res.ok) throw new Error('Gagal memuat antrean.');
        return res.json();
      })
      .then((data) => {
        setItems(data.items);
        setSummary(data.summary);
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setMessage(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [source, platform, status, revision, router]);

  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">OPERASIONAL · PRIVATE</p>
          <h1>Antrean upload</h1>
          <p>Satu tabel untuk live IDN, Showroom, dan TikTok — platform, tujuan, status, dan error per baris.</p>
        </div>
      </div>

      {summary && (
        <div className="stat-chips">
          <div className="stat-chip static">
            <strong>{summary.live_total}</strong>
            <span>Live</span>
            <small>IDN {summary.live_by_platform.idn || 0} · SR {summary.live_by_platform.showroom || 0}</small>
          </div>
          <div className="stat-chip static">
            <strong>{summary.tiktok_total}</strong>
            <span>TikTok</span>
            <small>Pending {summary.tiktok_by_status.pending_upload || 0} · Gagal {summary.tiktok_by_status.failed || 0}</small>
          </div>
          <div className="stat-chip static">
            <strong>{summary.tiktok_yt_backlog}</strong>
            <span>TG → YT</span>
            <small>Backlog TikTok ke YouTube</small>
          </div>
        </div>
      )}

      <div className="form-row">
        <label>
          Sumber
          <select
            value={source}
            onChange={(e) => {
              setLoading(true);
              setSource(e.target.value);
            }}
          >
            {SOURCES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Platform
          <select
            value={platform}
            onChange={(e) => {
              setLoading(true);
              setPlatform(e.target.value);
              if (e.target.value === 'tiktok') setSource('tiktok');
              else if (e.target.value === 'idn' || e.target.value === 'showroom') setSource('live');
            }}
          >
            {PLATFORMS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(e) => {
              setLoading(true);
              setStatus(e.target.value);
            }}
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondary-button"
          onClick={() => {
            setLoading(true);
            setRevision((r) => r + 1);
          }}
        >
          Muat ulang
        </button>
      </div>

      <div role="status" aria-live="polite">
        {message && <p className="notice error">{message}</p>}
      </div>

      <div className="panel table-scroll">
        <table className="admin-table">
          <caption className="sr-only">Antrean upload terpadu</caption>
          <thead>
            <tr>
              <th>Konten</th>
              <th>Status</th>
              <th>Tujuan</th>
              <th>Ukuran</th>
              <th>Umur</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {!loading &&
              items.map((item) => (
                <tr key={`${item.source}:${item.id}`}>
                  <td>
                    <div className="cell-title">{item.member_name}</div>
                    <div className="cell-meta">
                      <span className={`platform-badge inline-badge ${item.platform === 'showroom' ? 'showroom' : 'idn'}`}>
                        {platformLabel(item.platform)}
                      </span>
                      <span>{item.source === 'tiktok' ? 'TikTok' : 'Live'}</span>
                      <span className="truncate-meta">{item.title}</span>
                      {item.youtube_video_id && (
                        <a
                          href={`https://www.youtube.com/watch?v=${item.youtube_video_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          YT ↗
                        </a>
                      )}
                    </div>
                  </td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>{DEST_LABEL[item.destination] || item.destination}</td>
                  <td>{formatSize(item.file_size_bytes)}</td>
                  <td>
                    {item.age_hours === null
                      ? '—'
                      : item.age_hours < 1
                        ? `${Math.max(1, Math.round(item.age_hours * 60))} mnt`
                        : `${Math.floor(item.age_hours)} jam`}
                  </td>
                  <td>
                    <span className="help-text" style={{ color: item.error_message ? '#fecdd3' : undefined }}>
                      {item.error_message
                        ? item.error_message.length > 120
                          ? `${item.error_message.slice(0, 120)}…`
                          : item.error_message
                        : '—'}
                    </span>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        <p className="help-text">
          {loading
            ? 'Memuat antrean…'
            : items.length === 0
              ? 'Antrean kosong — tidak ada yang menunggu atau sedang diproses.'
              : `Menampilkan ${items.length} entri (digroup per konten).`}
        </p>
      </div>
    </section>
  );
}
