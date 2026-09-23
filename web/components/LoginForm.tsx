'use client';
import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

interface Props {
  /** Site key Turnstile (server). Kosong = widget & verifikasi tidak aktif. */
  turnstileSiteKey?: string;
}

interface TurnstileApi {
  render(el: HTMLElement, options: Record<string, unknown>): string;
  remove(id: string): void;
  reset(id?: string): void;
  getResponse(id?: string): string | undefined;
}

declare global {
  interface Window { turnstile?: TurnstileApi }
}

let scriptPromise: Promise<void> | null = null;

function loadTurnstile(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => { scriptPromise = null; reject(new Error('Turnstile gagal dimuat.')); };
    document.head.appendChild(s);
  });
  return scriptPromise;
}

export default function LoginForm({ turnstileSiteKey }: Props) {
  const router = useRouter();
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(!turnstileSiteKey);
  const widgetRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const tokenRef = useRef('');
  useEffect(() => { localStorage.removeItem('jkt48_admin_secret'); }, []);

  useEffect(() => {
    if (!turnstileSiteKey || !widgetRef.current) return;
    let cancelled = false;
    (async () => {
      try {
        await loadTurnstile();
        if (cancelled || !widgetRef.current || !window.turnstile) return;
        widgetIdRef.current = window.turnstile.render(widgetRef.current, {
          sitekey: turnstileSiteKey,
          theme: 'dark',
          callback: (token: string) => { tokenRef.current = token; },
          'expired-callback': () => { tokenRef.current = ''; },
          'error-callback': () => { tokenRef.current = ''; },
        });
        setReady(true);
      } catch {
        if (!cancelled) setError('Verifikasi keamanan gagal dimuat. Muat ulang halaman.');
      }
    })();
    return () => {
      cancelled = true;
      if (widgetIdRef.current && window.turnstile) {
        window.turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
      tokenRef.current = '';
    };
  }, [turnstileSiteKey]);

  return <section className="login-panel panel"><span className="eyebrow">AKSES TERBATAS</span><h1>Selamat datang kembali</h1>
    <p>Masuk buat ngatur member dan publikasi replay.</p>
    <form onSubmit={async e => {
      e.preventDefault();
      if (turnstileSiteKey && !tokenRef.current) {
        setError('Centang verifikasi keamanan dulu.');
        if (widgetIdRef.current && window.turnstile) window.turnstile.reset(widgetIdRef.current);
        return;
      }
      setBusy(true); setError('');
      try {
        const res = await fetch('/api/auth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ secret, ...(turnstileSiteKey ? { turnstile: tokenRef.current } : {}) }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || 'Login gagal.');
        setSecret(''); router.replace('/admin'); router.refresh();
      } catch (err) { setError(err instanceof Error ? err.message : 'Koneksi gagal.'); }
      finally {
        setBusy(false);
        if (turnstileSiteKey && widgetIdRef.current && window.turnstile) {
          window.turnstile.reset(widgetIdRef.current);
          tokenRef.current = '';
        }
      }
    }}>
      <label htmlFor="admin-secret-input">Password admin</label>
      <input id="admin-secret-input" type="password" autoComplete="current-password" required maxLength={1024} value={secret} onChange={e => setSecret(e.target.value)} />
      {turnstileSiteKey && <div ref={widgetRef} className="turnstile-box" aria-label="Verifikasi keamanan" />}
      {error && <p role="alert" className="notice error">{error}</p>}
      <button className="primary-button" disabled={busy || !ready}>{busy ? 'Memverifikasi…' : 'Masuk ke Admin Studio'}</button>
    </form><p className="help-text">Sesi berakhir sendiri setelah 8 jam. Jangan bagikan password ini ke siapa pun.</p>
  </section>;
}
