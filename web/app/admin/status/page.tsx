import { redirect } from 'next/navigation';
import { isAdmin } from '@/lib/auth';
import { getSystemStats, type ActiveSession, type YouTubeChannelStat } from '@/lib/db';
import { platformLabel, StatusBadge } from '@/components/admin-status';

export default async function SystemPage() {
  if (!(await isAdmin())) redirect('/login');
  const stats = getSystemStats();
  const tk = stats.tiktok;
  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">OPERASIONAL · PRIVATE</p>
          <h1>Sistem</h1>
          <p>
            Snapshot database saat dibuka — bukan penanda bot hidup. Antrean detail: {' '}
            <a href="/admin/queue">halaman Antrean</a>.
          </p>
        </div>
      </div>

      <div className="metric-grid">
        <div className="panel">
          <span>Sesi dalam proses</span>
          <strong>{stats.active_sessions.length}</strong>
        </div>
        <div className="panel">
          <span>Menunggu upload</span>
          <strong>{stats.pending_uploads_count}</strong>
        </div>
        <div className="panel">
          <span>Member dipantau</span>
          <strong>{stats.members_count.active}</strong>
        </div>
      </div>

      <div className="panel table-scroll">
        <h2>Sesi aktif (live)</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Platform</th>
              <th>Member</th>
              <th>Tahap</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {stats.active_sessions.map((s: ActiveSession) => (
              <tr key={s.id}>
                <td>
                  <span className={`platform-badge inline-badge ${s.platform === 'showroom' ? 'showroom' : 'idn'}`}>
                    {platformLabel(s.platform)}
                  </span>
                </td>
                <td>{s.display_name}</td>
                <td>
                  <StatusBadge status={s.status} />
                </td>
                <td>
                  <span className="help-text" style={s.error_message ? { color: '#fecdd3' } : undefined}>
                    {s.error_message || '—'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {stats.active_sessions.length === 0 && (
          <p className="help-text">Belum ada sesi yang sedang berjalan.</p>
        )}
      </div>

      <div className="panel table-scroll">
        <h2>Channel upload YouTube</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Upload hari ini</th>
              <th>Reset terakhir</th>
            </tr>
          </thead>
          <tbody>
            {stats.youtube_channels.map((c: YouTubeChannelStat) => (
              <tr key={c.id}>
                <td>{c.label}</td>
                <td>{c.uploads_today}</td>
                <td>{c.last_reset || 'Belum tersedia'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {stats.youtube_channels.length === 0 && (
          <p className="help-text">Belum ada channel tersedia.</p>
        )}
      </div>

      <div className="panel">
        <h2>TikTok</h2>
        <div className="metric-grid">
          <div>
            <span className="help-text">Akun aktif</span>
            <strong>
              {tk.accounts_enabled}/{tk.accounts_total}
            </strong>
          </div>
          <div>
            <span className="help-text">Sedang diproses</span>
            <strong>{tk.uploading}</strong>
          </div>
          <div>
            <span className="help-text">Pending / gagal</span>
            <strong>
              {tk.pending_upload} / {tk.failed}
            </strong>
          </div>
        </div>
        <p className="help-text" style={{ marginTop: '12px' }}>
          Backlog TikTok → YouTube: <strong>{tk.yt_backlog}</strong> post sudah diarsip
          Telegram tetapi belum terupload ke YouTube. Total post: <strong>{tk.posts_total}</strong>.
        </p>
        <p className="help-text">
          <a href="/admin/queue">Buka antrean terpadu →</a>
        </p>
      </div>
    </section>
  );
}
