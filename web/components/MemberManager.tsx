'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
interface Member { username: string; display_name: string; enabled: boolean; video_count: number }
export default function MemberManager() {
  const router = useRouter();
  const [members, setMembers] = useState<Member[]>([]);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  async function load() {
    const res = await fetch('/api/admin/members', { cache: 'no-store' });
    if (res.status === 401) { router.replace('/login'); throw new Error('Sesi berakhir. Silakan masuk kembali.'); }
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Gagal memuat member.');
    setMembers(data.members);
  }
  useEffect(() => {
    localStorage.removeItem('jkt48_admin_secret');
    const controller = new AbortController();
    fetch('/api/admin/members', { cache: 'no-store', signal: controller.signal })
      .then(async res => {
        if (res.status === 401) router.replace('/login');
        if (!res.ok) throw new Error('Gagal memuat member.');
        return res.json();
      }).then(data => setMembers(data.members))
      .catch(e => { if (e.name !== 'AbortError') setMessage(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [router]);
  async function mutate(body: object) {
    setBusy(true); setMessage('');
    try {
      const res = await fetch('/api/admin/members', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (res.status === 401) router.replace('/login');
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'Perubahan gagal.');
      await load(); setMessage('Perubahan berhasil disimpan.'); return true;
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Koneksi gagal.'); return false; }
    finally { setBusy(false); }
  }
  return <section><div className="section-heading"><div><p className="eyebrow">PENGELOLAAN</p><h1>Member</h1><p>Pengaturan pemantauan ini hanya terlihat oleh admin.</p></div>
    <button className="secondary-button" disabled={busy} onClick={async () => {
      setBusy(true);
      try { const res = await fetch('/api/auth', { method: 'DELETE' }); if (!res.ok) throw new Error(); router.replace('/login'); router.refresh(); }
      catch { setMessage('Logout gagal. Coba lagi.'); setBusy(false); }
    }}>Keluar</button></div>
    <div role="status" aria-live="polite">{message && <p className="notice">{message}</p>}</div>
    <form className="panel form-row" onSubmit={async e => {
      e.preventDefault(); const form = e.currentTarget; const data = new FormData(form);
      if (await mutate({ action: 'add', username: data.get('username'), displayName: data.get('displayName') })) form.reset();
    }}><label>Username<input name="username" required pattern="[a-zA-Z0-9_.-]{1,80}" maxLength={80} placeholder="jkt48_member" /></label>
      <label>Nama tampilan<input name="displayName" maxLength={120} placeholder="Nama member" /></label>
      <button className="primary-button" disabled={busy}>Tambah member</button></form>
    <label className="search-field">Cari member<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Nama atau username" /></label>
    <div className="panel table-scroll"><table className="admin-table"><caption className="sr-only">Pengaturan pemantauan member</caption><thead><tr><th>Member</th><th>Replay terunggah</th><th>Pemantauan</th></tr></thead><tbody>
      {members.filter(m => `${m.username} ${m.display_name}`.toLowerCase().includes(query.toLowerCase())).map(m => <tr key={m.username}><td><strong>{m.display_name}</strong><br /><span className="help-text">@{m.username}</span></td><td>{m.video_count}</td><td><button className="secondary-button" aria-pressed={m.enabled} aria-label={`Pemantauan ${m.display_name}`} disabled={busy} onClick={() => mutate({ action: 'toggle', username: m.username, enabled: !m.enabled })}>{m.enabled ? 'Aktif · Nonaktifkan' : 'Nonaktif · Aktifkan'}</button></td></tr>)}
    </tbody></table>{loading ? <p className="notice">Memuat member…</p> : members.filter(m => `${m.username} ${m.display_name}`.toLowerCase().includes(query.toLowerCase())).length === 0 && <p className="notice">Tidak ada member ditemukan.</p>}</div>
  </section>;
}
