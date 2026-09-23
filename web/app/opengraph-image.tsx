import { ImageResponse } from 'next/og';

export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';
export const alt = 'JKT48 Replay — Nonton ulang live IDN & Showroom';

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'flex-start',
          padding: '80px',
          background: 'linear-gradient(135deg, #07090e 0%, #1a0a12 55%, #3b0d1c 100%)',
          color: '#fff',
          fontFamily: 'sans-serif',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 24,
            marginBottom: 36,
          }}
        >
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: 16,
              background: '#be123c',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <svg width="40" height="40" viewBox="0 0 64 64">
              <path d="M24 19v26l18-13z" fill="#fff" />
            </svg>
          </div>
          <div style={{ fontSize: 36, fontWeight: 700, letterSpacing: -1 }}>
            JKT48 Replay
          </div>
        </div>
        <div
          style={{
            fontSize: 64,
            fontWeight: 800,
            lineHeight: 1.1,
            letterSpacing: -2,
            maxWidth: 980,
          }}
        >
          Nonton ulang live IDN &amp; Showroom
        </div>
        <div
          style={{
            marginTop: 28,
            fontSize: 32,
            color: '#f9a8d4',
            fontWeight: 500,
          }}
        >
          Arsip komunitas — cari per member, judul, atau tanggal
        </div>
      </div>
    ),
    size,
  );
}
