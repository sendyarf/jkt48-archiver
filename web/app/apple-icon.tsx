import { ImageResponse } from 'next/og';

export const size = { width: 180, height: 180 };
export const contentType = 'image/png';

/** apple-touch-icon 180×180 — iOS tidak memakai SVG; latar solid tanpa transparansi. */
export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#be123c',
        }}
      >
        <svg width="110" height="110" viewBox="0 0 64 64">
          <path d="M24 19v26l18-13z" fill="#fff" />
        </svg>
      </div>
    ),
    size,
  );
}
