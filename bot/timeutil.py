"""
timeutil.py - Waktu kanonik (UTC) dan tampilan zona waktu.

Mengapa modul ini ada
---------------------
VPS dapat berjalan di zona waktu apa pun (mis. Seoul = UTC+9). Jika timestamp
ditulis memakai `datetime.now()` (waktu dinding lokal server), maka nilai di
database bergantung pada lokasi server dan **tidak punya penanda zona waktu**.
Akibatnya dua server berbeda menghasilkan angka berbeda untuk kejadian yang
sama, dan pembaca mana pun tidak dapat mengetahui arti angkanya.

Aturan proyek:
  * Semua waktu yang DISIMPAN ke database = UTC, ISO-8601, DENGAN penanda
    zona waktu ('+00:00'). Gunakan `utc_now_iso()`.
  * Konversi ke zona waktu penonton (WIB) HANYA dilakukan saat menampilkan
    atau saat menyusun judul/keterangan, memakai `format_display()`.

Nilai lama yang tidak punya penanda zona waktu (ditulis sebelum perbaikan ini)
diperlakukan sebagai UTC. Bila offset server lama diketahui, set
`LEGACY_NAIVE_TIME_OFFSET_HOURS` agar nilai lama dibaca dengan benar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Offset default untuk tampilan (WIB = UTC+7).
DEFAULT_DISPLAY_OFFSET_HOURS = 7


def utc_now() -> datetime:
    """Waktu sekarang, sadar zona waktu (UTC)."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Waktu sekarang dalam UTC ISO-8601 dengan penanda '+00:00'."""
    return utc_now().isoformat()


def has_timezone_marker(value: str) -> bool:
    """Apakah string waktu sudah membawa penanda zona waktu?"""
    if not value:
        return False
    text = value.strip()
    if text.endswith(("Z", "z")):
        return True
    # Cari '+' / '-' setelah bagian jam (hindari salah baca pada tanggal).
    for index in range(10, len(text)):
        if text[index] in "+-":
            return True
    return False


def parse_stored(value: Optional[str], legacy_offset_hours: float = 0.0) -> Optional[datetime]:
    """
    Ubah nilai waktu yang tersimpan menjadi datetime sadar zona waktu.

    - Bermarker ('Z' atau '+07:00') -> dihormati apa adanya.
    - Naif -> dianggap UTC, kecuali `legacy_offset_hours` diberikan, yang
      berarti nilai naif itu adalah waktu dinding pada offset tersebut.
    """
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        return parsed
    if legacy_offset_hours:
        return parsed.replace(tzinfo=timezone(timedelta(hours=legacy_offset_hours)))
    return parsed.replace(tzinfo=timezone.utc)


def to_display(
    value: Optional[str],
    offset_hours: float = DEFAULT_DISPLAY_OFFSET_HOURS,
    legacy_offset_hours: float = 0.0,
) -> Optional[datetime]:
    """Ubah nilai tersimpan menjadi datetime di zona waktu tampilan."""
    parsed = parse_stored(value, legacy_offset_hours)
    if parsed is None:
        return None
    return parsed.astimezone(timezone(timedelta(hours=offset_hours)))


def format_display(
    value: Optional[str],
    fmt: str,
    offset_hours: float = DEFAULT_DISPLAY_OFFSET_HOURS,
    legacy_offset_hours: float = 0.0,
) -> str:
    """
    Format nilai tersimpan untuk ditampilkan di zona waktu penonton.
    Mengembalikan string kosong bila nilai tidak dapat dibaca.
    """
    converted = to_display(value, offset_hours, legacy_offset_hours)
    if converted is None:
        return ""
    return converted.strftime(fmt)


# ─── Waktu Pasifik (jendela reset kuota Google/YouTube) ──────────────────────
#
# Kuota harian Google Cloud / YouTube Data API di-reset pada tengah malam waktu
# Pasifik — "Daily quotas reset at midnight Pacific Time (PT)" (YouTube Data API,
# Quota Calculator). Waktu Pasifik sendiri berpindah antara PST (UTC-8) dan PDT
# (UTC-7, Maret–November), sehingga offset-nya HARUS dihitung dari tanggalnya;
# offset tetap akan meleset satu jam selama setengah tahun.
_QUOTA_RESET_TZ_NAME = "America/Los_Angeles"

try:  # pragma: no cover - hasilnya bergantung pada tz database mesin
    from zoneinfo import ZoneInfo

    _PACIFIC = ZoneInfo(_QUOTA_RESET_TZ_NAME)
except Exception:  # tz database tidak tersedia (container minim / Windows tanpa tzdata)
    _PACIFIC = None


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> datetime:
    """Tanggal ke-`nth` yang jatuh pada `weekday` (Senin=0 … Minggu=6) di bulan itu."""
    first = datetime(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (nth - 1))


def _pacific_offset_by_dst_rule(moment_utc: datetime) -> int:
    """
    Fallback saat tz database tidak ada: aturan DST Amerika Serikat sejak 2007.

      * mulai   : Minggu kedua Maret      pukul 02.00 PST = 10.00 UTC → UTC-7
      * selesai : Minggu pertama November pukul 02.00 PDT = 09.00 UTC → UTC-8

    `moment_utc` harus sadar zona waktu (UTC).
    """
    year = moment_utc.year
    dst_start = _nth_weekday(year, 3, 6, 2).replace(hour=10, tzinfo=timezone.utc)
    dst_end = _nth_weekday(year, 11, 6, 1).replace(hour=9, tzinfo=timezone.utc)
    return -7 if dst_start <= moment_utc < dst_end else -8


def pacific_offset_hours(now: Optional[datetime] = None) -> int:
    """
    Offset jam UTC→waktu Pasifik yang berlaku pada `now` (−7 saat PDT, −8 saat PST).

    `now` boleh naif (dianggap UTC) atau sadar zona waktu: yang dibandingkan adalah
    INSTAN-nya, jadi hasilnya sama berapa pun zona waktu server — sesuai aturan
    proyek bahwa seluruh waktu disimpan dalam UTC.

    Dipakai `bot.database.reset_channel_counters_if_new_day()` agar pergantian
    "hari kuota" YouTube (tengah malam PT) tidak dimajukan 7–8 jam ke tengah
    malam UTC.
    """
    moment = utc_now() if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment_utc = moment.astimezone(timezone.utc)

    if _PACIFIC is not None:
        try:
            offset = moment_utc.astimezone(_PACIFIC).utcoffset()
        except Exception:
            offset = None
        if offset is not None:
            return int(offset.total_seconds() // 3600)
    return _pacific_offset_by_dst_rule(moment_utc)
