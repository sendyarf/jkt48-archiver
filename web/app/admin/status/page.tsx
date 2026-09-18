import { redirect } from 'next/navigation';
import { isAdmin } from '@/lib/auth';
import { getSystemStats, type ActiveSession, type YouTubeChannelStat } from '@/lib/db';
export default async function SystemPage() {
  if (!(await isAdmin())) redirect('/login');
  const stats = getSystemStats();
  return <section><div className="section-heading"><div><p className="eyebrow">OPERASIONAL · PRIVATE</p><h1>Sistem & antrean</h1><p>Snapshot dari database saat halaman dibuka. Bukan indikator kesehatan proses bot.</p></div></div>
    <div className="metric-grid"><div className="panel"><span>Sesi dalam proses</span><strong>{stats.active_sessions.length}</strong></div><div className="panel"><span>Menunggu upload</span><strong>{stats.pending_uploads_count}</strong></div><div className="panel"><span>Member dipantau</span><strong>{stats.members_count.active}</strong></div></div>
    <div className="panel table-scroll"><h2>Sesi aktif</h2><table className="admin-table"><thead><tr><th>Member</th><th>Tahap pemrosesan</th></tr></thead><tbody>{stats.active_sessions.map((s: ActiveSession) => <tr key={s.id}><td>{s.display_name}</td><td>{s.status}</td></tr>)}</tbody></table>{stats.active_sessions.length === 0 && <p className="help-text">Tidak ada sesi dalam proses.</p>}</div>
    <div className="panel table-scroll"><h2>Channel upload</h2><table className="admin-table"><thead><tr><th>Label</th><th>Upload hari ini</th><th>Reset terakhir</th></tr></thead><tbody>{stats.youtube_channels.map((c: YouTubeChannelStat) => <tr key={c.id}><td>{c.label}</td><td>{c.uploads_today}</td><td>{c.last_reset || 'Belum tersedia'}</td></tr>)}</tbody></table>{stats.youtube_channels.length === 0 && <p className="help-text">Belum ada channel tersedia.</p>}</div>
  </section>;
}
