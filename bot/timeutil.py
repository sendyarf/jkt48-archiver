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
