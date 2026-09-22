'use client';

import { useEffect, useRef, useState } from 'react';
import { Share2 } from 'lucide-react';

/** Tombol bagikan: Web Share API bila ada, fallback salin ke clipboard. */
export default function ShareButton({ title, text }: { title: string; text?: string }) {
  const [message, setMessage] = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  const flash = (msg: string) => {
    setMessage(msg);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setMessage(''), 2200);
  };

  const share = async () => {
    const url = typeof window !== 'undefined' ? window.location.href : '';
    try {
      if (typeof navigator !== 'undefined' && navigator.share) {
        await navigator.share({ title, text: text || title, url });
        return;
      }
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
        flash('Link tersalin');
        return;
      }
      flash('Tidak bisa membagikan di browser ini');
    } catch {
      // User membatalkan share sheet — biarkan senyap.
    }
  };

  return (
    <>
      <button type="button" className="secondary-button" onClick={share}>
        <Share2 size={16} aria-hidden="true" /> Bagikan
      </button>
      <span className="sr-only" role="status" aria-live="polite">{message}</span>
    </>
  );
}
