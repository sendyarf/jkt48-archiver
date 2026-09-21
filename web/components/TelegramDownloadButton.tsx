'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Download, X } from 'lucide-react';

interface Props {
  youtubeVideoId: string;
  title: string;
}

/**
 * Tombol Download + modal yang mengarahkan user ke bot Telegram via deep-link
 * (https://t.me/<bot>?start=<youtube_video_id>). Bot meng-copyMessage video dari
 * channel arsip privat — tanpa re-upload, tanpa membebani server.
 *
 * Username bot dibaca dari env server NEXT_PUBLIC_REPLAY_BOT_USERNAME.
 */
export default function TelegramDownloadButton({ youtubeVideoId, title }: Props) {
  const [open, setOpen] = useState(false);
  const [launched, setLaunched] = useState(false);
  const [copied, setCopied] = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  const botUsername = (process.env.NEXT_PUBLIC_REPLAY_BOT_USERNAME || '').replace(/^@/, '');
  const deepLink = botUsername
    ? `https://t.me/${botUsername}?start=${encodeURIComponent(youtubeVideoId)}`
    : '';

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    // Fokus awal ke tombol tutup agar mudah dikontrol keyboard / screen reader.
    closeBtnRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(deepLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API bisa gagal bila bukan secure-context; abaikan saja.
    }
  };

  return (
    <>
      <button type="button" className="secondary-button" onClick={() => setOpen(true)}>
        <Download size={16} aria-hidden="true" />
        Download
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Download lewat Bot Telegram"
          className="modal-overlay"
          onClick={() => setOpen(false)}
        >
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">Download lewat Bot Telegram</h2>
              <button
                type="button"
                aria-label="Tutup"
                ref={closeBtnRef}
                onClick={() => setOpen(false)}
                className="modal-close"
              >
                <X size={20} aria-hidden="true" />
              </button>
            </div>

            <p className="modal-body">
              Video <strong style={{ color: '#fff' }}>{title}</strong> bakal dikirim ke kamu
              lewat bot Telegram kami. Tekan tombol di bawah, lalu tap{' '}
              <strong>Start</strong> di Telegram — videonya langsung masuk.
            </p>

            {deepLink ? (
              <div className="modal-actions">
                <a
                  href={deepLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="primary-button"
                  onClick={() => setLaunched(true)}
                >
                  Buka Bot Telegram ↗
                </a>
                <button type="button" className="secondary-button" onClick={handleCopy}>
                  {copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
                  {copied ? 'Tersalin' : 'Salin link'}
                </button>
              </div>
            ) : (
              <p className="notice">Bot download belum dikonfigurasi. Coba lagi nanti.</p>
            )}

            {launched && !copied && (
              <p className="modal-feedback" role="status">
                ✓ Membuka Telegram… tap <strong>Start</strong> supaya videonya terkirim.
              </p>
            )}

            <p className="modal-hint">
              Videonya dikirim sebagai file — bisa langsung diputar atau disimpan dari Telegram.
            </p>
          </div>
        </div>
      )}
    </>
  );
}

