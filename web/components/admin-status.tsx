/** Label & rasa visual status antrean/bot — dipakai halaman admin Sistem & Antrean. */

export const STATUS_LABEL: Record<string, string> = {
  detected: 'Terdeteksi',
  downloading: 'Mengunduh',
  segment_done: 'Segmen selesai',
  download_complete: 'Unduhan selesai',
  merging: 'Merge',
  uploading_telegram: 'Upload Telegram',
  uploading_youtube: 'Upload YouTube',
  done_telegram: 'Selesai (TG)',
  done_youtube: 'Selesai (YT)',
  done: 'Selesai',
  pending_upload: 'Menunggu retry',
  failed: 'Gagal',
};

export type StatusTone = 'neutral' | 'active' | 'success' | 'warning' | 'danger';

export function statusTone(status: string): StatusTone {
  if (status === 'failed') return 'danger';
  if (status === 'pending_upload') return 'warning';
  if (status === 'done' || status === 'done_telegram' || status === 'done_youtube' || status === 'download_complete') return 'success';
  if (
    status.startsWith('uploading') ||
    status === 'downloading' ||
    status === 'merging' ||
    status === 'segment_done' ||
    status === 'detected'
  ) {
    return 'active';
  }
  return 'neutral';
}

export function StatusBadge({ status }: { status: string }) {
  const tone = statusTone(status);
  return (
    <span className={`status-badge tone-${tone}`}>{STATUS_LABEL[status] || status}</span>
  );
}

export function platformLabel(p: string): string {
  if (p === 'showroom') return 'Showroom';
  if (p === 'tiktok') return 'TikTok';
  return 'IDN';
}
