'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

export default function LoginForm() {
  const router = useRouter();
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { localStorage.removeItem('jkt48_admin_secret'); }, []);
  return <section className="login-panel panel"><span className="eyebrow">AKSES TERBATAS</span><h1>Selamat datang kembali</h1>
    <p>Masuk buat ngatur member dan publikasi replay.</p>
    <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError('');
      try {
        const res = await fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ secret }) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || 'Login gagal.');
        setSecret(''); router.replace('/admin'); router.refresh();
      } catch (err) { setError(err instanceof Error ? err.message : 'Koneksi gagal.'); }
      finally { setBusy(false); }
    }}>
      <label htmlFor="admin-secret-input">Password admin</label>
      <input id="admin-secret-input" type="password" autoComplete="current-password" required maxLength={1024} value={secret} onChange={e => setSecret(e.target.value)} />
      {error && <p role="alert" className="notice error">{error}</p>}
      <button className="primary-button" disabled={busy}>{busy ? 'Memverifikasi…' : 'Masuk ke Admin Studio'}</button>
    </form><p className="help-text">Sesi berakhir sendiri setelah 8 jam. Jangan bagikan password ini ke siapa pun.</p>
  </section>;
}
