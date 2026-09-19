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

/**
 * Kandidat tanggal (YYYY-MM-DD) yang mungkin dimaksud oleh kata kunci pencarian.
 *
 * Pengguna mengetik tanggal dalam bahasa sehari-hari ("18 september",
 * "september 2026", "18/09/2026", "2026-09-18"), sementara database menyimpan
 * timestamp mentah. Fungsi ini mengekstrak komponen tanggal dari kata kunci
 * dan mengembalikannya sebagai daftar tanggal ISO agar bisa dicocokkan dengan
 * `date(started_at)` di SQL. Bila kata kunci tidak memuat komponen tanggal
 * sama sekali, kembalikan [] (pencarian teks biasa tidak berubah).
 */
export function extractSearchDates(keyword: string): string[] {
  const text = (keyword || '').toLowerCase().trim().replace(/\s+/g, ' ');
  if (!text) return [];

  const monthFull: Record<string, number> = {
    januari: 1, februari: 2, maret: 3, april: 4, mei: 5, juni: 6,
    juli: 7, agustus: 8, september: 9, oktober: 10, november: 11, desember: 12,
  };
  const monthShort: Record<string, number> = {
    jan: 1, feb: 2, mar: 3, apr: 4, mei: 5, jun: 6,
    jul: 7, agu: 8, ags: 8, sep: 9, sept: 9, okt: 10, nov: 11, des: 12,
  };

  let month = 0;
  for (const [name, num] of Object.entries(monthFull)) {
    if (text.includes(name)) { month = num; break; }
  }
  if (!month) {
    const tokens = text.split(/[^a-z]+/).filter(Boolean);
    for (const token of tokens) {
      if (monthShort[token]) { month = monthShort[token]; break; }
    }
  }

  // Format angka: 18/09/2026, 18-09-2026, 2026-09-18, 18/09, 09/2026.
  const nums = (text.match(/\d{1,4}/g) || []).map(Number);
  let day = 0;
  let year = 0;
  const slashLike = text.match(/(\d{1,2})[\/\-.](\d{1,2})(?:[\/\-.](\d{2,4}))?/);
  const isoLike = text.match(/(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/);
  if (isoLike) {
    year = Number(isoLike[1]);
    if (!month) month = Number(isoLike[2]);
    day = Number(isoLike[3]);
  } else if (slashLike) {
    day = Number(slashLike[1]);
    if (!month) month = Number(slashLike[2]);
    if (slashLike[3]) {
      year = Number(slashLike[3]);
      if (year < 100) year += 2000;
    }
  } else {
    for (const n of nums) {
      if (n >= 1000 && n <= 2100 && !year) year = n;
      else if (n >= 1 && n <= 31 && !day) day = n;
    }
  }

  if (!month) return [];
  if (month < 1 || month > 12) return [];
  if (day && (day < 1 || day > 31)) return [];

  const pad = (n: number) => String(n).padStart(2, '0');
  // Tanpa tahun: cocokkan bulan itu di tahun berjalan dan tahun sebelumnya
  // (arsip bisa berisi bulan yang sama di dua tahun berbeda).
  const years = year ? [year] : (() => {
    const now = new Date();
    const y = now.getFullYear();
    return [y, y - 1];
  })();

  const out: string[] = [];
  if (day) {
    for (const y of years) out.push(`${y}-${pad(month)}-${pad(day)}`);
  } else {
    for (const y of years) out.push(`${y}-${pad(month)}`);
  }
  return out;
}

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
