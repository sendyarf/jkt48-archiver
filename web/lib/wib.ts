/**
 * Konversi & format waktu WIB — satu sumber kebenaran untuk seluruh tampilan.
 *
 * Nilai waktu di database ditulis bot sebagai jam dinding server (naive, tanpa
 * penanda zona) atau UTC berpenanda +00:00 (baris baru). Konversi ke WIB memakai
 * getTimezoneOffset() server sehingga hasilnya konsisten di server zona mana
 * pun. HANYA dipakai di sisi server (RSC/lib) — jangan diimpor dari client
 * component, karena offset zona di perangkat viewer berbeda dari server.
 */

export const ID_MONTHS = [
  'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
  'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
];

const ID_MONTHS_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun',
  'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des',
];

const ID_DAYS = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];

export function parseStoredDate(value: string): Date | null {
  const text = (value || '').trim().replace(/Z$/i, '+00:00');
  if (!text) return null;
  const parsed = new Date(text);
  return isNaN(parsed.getTime()) ? null : parsed;
}

/** Pecahan waktu WIB dari nilai tersimpan. */
export function getWibParts(value: string) {
  const parsed = parseStoredDate(value);
  if (!parsed) return null;
  const wib = new Date(parsed.getTime() + (7 * 60 + parsed.getTimezoneOffset()) * 60000);
  return {
    weekday: ID_DAYS[wib.getDay()],
    day: wib.getDate(),
    month: wib.getMonth(),
    year: wib.getFullYear(),
    hours: String(wib.getHours()).padStart(2, '0'),
    minutes: String(wib.getMinutes()).padStart(2, '0'),
  };
}

/**
 * Judul tampilan yang konsisten untuk seluruh halaman publik & admin:
 *   "LIVE IDN NAMA MEMBER - 15 September 2026 | 16:37 WIB"
 *
 * Judul asli live (`live_title`) tidak dipakai karena diketik member secara
 * bebas: tidak konsisten, bisa berubah di tengah live, bisa kosong, dan tidak
 * memuat tanggal. Format seragam membuat user langsung tahu platform, member,
 * dan waktu replay tanpa membuka halaman watch.
 */
export function buildDisplayTitle(
  platform: string,
  streamerName: string,
  startedAt: string,
): string {
  const platformLabel = platform === 'showroom' ? 'SHOWROOM' : 'IDN';
  const name = (streamerName || '').trim().toUpperCase() || 'JKT48';
  const parts = getWibParts(startedAt);
  if (!parts) {
    return `LIVE ${platformLabel} ${name}`.slice(0, 100);
  }
  const datePart = `${parts.day} ${ID_MONTHS[parts.month]} ${parts.year}`;
  return `LIVE ${platformLabel} ${name} - ${datePart} | ${parts.hours}:${parts.minutes} WIB`.slice(0, 100);
}

/** Baris detail halaman watch: "Jumat, 18 September 2026 pukul 22:18 WIB". */
export function formatWibLong(value: string): string {
  const parts = getWibParts(value);
  if (!parts) return '';
  return `${parts.weekday}, ${parts.day} ${ID_MONTHS[parts.month]} ${parts.year} pukul ${parts.hours}:${parts.minutes} WIB`;
}

/** Tanggal pendek untuk kartu katalog: "18 Sep 2026". */
export function formatWibCardDate(value: string): string {
  const parts = getWibParts(value);
  if (!parts) return '';
  return `${parts.day} ${ID_MONTHS_SHORT[parts.month]} ${parts.year}`;
}
