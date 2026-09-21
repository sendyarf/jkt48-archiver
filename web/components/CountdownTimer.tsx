'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Clock } from 'lucide-react';

interface Props {
  /** ISO timestamp UTC kapan replay terbit. */
  publishAt: string;
  title: string;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/**
 * Panel countdown pra-rilis: menghitung mundur ke publishAt, lalu me-refresh
 * halaman agar server merender ulang dengan player (is_visible berubah).
 */
export default function CountdownTimer({ publishAt, title }: Props) {
  const router = useRouter();
  const target = new Date(publishAt).getTime();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const diffMs = target - now;

  // Waktu habis → refresh agar server menampilkan player.
  useEffect(() => {
    if (diffMs <= 0) {
      const t = setTimeout(() => router.refresh(), 1500);
      return () => clearTimeout(t);
    }
  }, [diffMs, router]);

  if (diffMs <= 0) {
    return (
      <div className="countdown-panel">
        <Clock size={28} aria-hidden="true" />
        <p className="countdown-label">Replay-nya sudah tayang</p>
        <p className="countdown-sub">Lagi menyiapkan pemutar…</p>
      </div>
    );
  }

  const totalSec = Math.floor(diffMs / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const mins = Math.floor((totalSec % 3600) / 60);
  const secs = totalSec % 60;

  return (
    <div className="countdown-panel">
      <Clock size={28} aria-hidden="true" />
      <p className="countdown-label">Replay segera hadir</p>
      <h2 className="countdown-title">{title}</h2>

      <div className="countdown-units" role="timer" aria-live="off">
        {days > 0 && (
          <div className="countdown-unit">
            <span className="countdown-num">{pad(days)}</span>
            <span className="countdown-cap">Hari</span>
          </div>
        )}
        <div className="countdown-unit">
          <span className="countdown-num">{pad(hours)}</span>
          <span className="countdown-cap">Jam</span>
        </div>
        <div className="countdown-unit">
          <span className="countdown-num">{pad(mins)}</span>
          <span className="countdown-cap">Menit</span>
        </div>
        <div className="countdown-unit">
          <span className="countdown-num">{pad(secs)}</span>
          <span className="countdown-cap">Detik</span>
        </div>
      </div>

      <p className="countdown-sub">Pemutar kebuka otomatis begitu waktunya tiba.</p>
    </div>
  );
}
