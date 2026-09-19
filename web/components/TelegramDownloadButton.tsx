'use client';

import { useEffect, useState } from 'react';
import { Download, X } from 'lucide-react';

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
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <button type="button" className="secondary-button" onClick={() => setOpen(true)}>
        <Download size={16} aria-hidden="true" style={{ verticalAlign: '-2px', marginRight: 6 }} />
        Download
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Download via Bot Telegram"
          onClick={() => setOpen(false)}
          style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            background: 'rgba(2,6,23,0.75)', backdropFilter: 'blur(4px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%', maxWidth: 440, background: '#0f172a',
              border: '1px solid #1e293b', borderRadius: 14, padding: 24,
              boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <h2 style={{ margin: 0, fontSize: '1.1rem', color: '#fff' }}>Download via Bot Telegram</h2>
              <button
                type="button"
                aria-label="Tutup"
                onClick={() => setOpen(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', padding: 4 }}
              >
                <X size={20} aria-hidden="true" />
              </button>
            </div>

            <p style={{ color: '#cbd5e1', fontSize: '0.9rem', lineHeight: 1.6, margin: '14px 0' }}>
              Video <strong style={{ color: '#fff' }}>{title}</strong> akan dikirim ke kamu
              lewat bot Telegram resmi kami. Klik tombol di bawah, lalu tekan{' '}
              <strong>Start</strong> / izinkan di Telegram — video otomatis terkirim.
            </p>

            {deepLink ? (
              <a
                href={deepLink}
                target="_blank"
                rel="noopener noreferrer"
                className="primary-button"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 8, textDecoration: 'none' }}
              >
                Buka Bot Telegram ↗
              </a>
            ) : (
              <p className="notice">Bot download belum dikonfigurasi. Coba lagi nanti.</p>
            )}

            <p style={{ color: '#64748b', fontSize: '0.78rem', marginTop: 16, marginBottom: 0 }}>
              Video dikirim sebagai file yang bisa diputar & disimpan dari aplikasi Telegram.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
