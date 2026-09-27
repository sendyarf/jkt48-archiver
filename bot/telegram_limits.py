"""
telegram_limits.py - Batas upload Telegram yang ditetapkan SERVER (bukan Telethon).

Modul kecil tanpa dependency internal agar bisa dipakai ``bot.telegram_sender``
(pembaca batas akun) maupun ``bot.video_splitter`` (ambang split) tanpa import
melingkar.

Sumber angka, diverifikasi live pada akun bot ini, 26 Sep 2026:
- ``help.getAppConfig``: ``upload_max_fileparts_default=4000`` dan
  ``upload_max_fileparts_premium=8000`` (core.telegram.org/api/config).
- ``upload.saveBigFilePart`` dengan ``file_total_parts=4000`` DITERIMA server,
  sedangkan 4001 dan 8000 DITOLAK dengan ``FILE_PARTS_INVALID`` pada akun biasa.
- Ukuran part maksimum Telethon 512 KiB (``utils.get_appropriated_part_size``).
  4000 × 512 KiB = 2.097.152.000 B ≈ 2 GB — sama dengan pernyataan resmi
  telegram.org/faq: "up to 2 GB in size each (or 4 GB with Premium)".
"""

# Ukuran part terbesar yang dipakai Telethon untuk file > 10 MB.
MAX_PART_SIZE_BYTES = 512 * 1024

# Batas JUMLAH part per file, per jenis akun (lihat docstring modul).
MAX_FILE_PARTS_FREE = 4000
MAX_FILE_PARTS_PREMIUM = 8000

# Margin untuk ambang SPLIT. ffmpeg memotong dengan ``-segment_time`` (berbasis
# DURASI) pada stream VBR, jadi satu part bisa lebih besar dari target rata-rata
# ketika bitrate naik diengahan rekaman. Tanpa margin, satu part bisa melewati
# batas part server dan arsip bagian itu hilang ditolak FILE_PARTS_INVALID.
SPLIT_SAFETY_FACTOR = 0.9


def max_file_bytes(max_parts: int = MAX_FILE_PARTS_FREE) -> int:
    """Ukuran file maksimum yang benar-benar muat di batas part ``max_parts``."""
    return int(max_parts) * MAX_PART_SIZE_BYTES


def safe_split_bytes(max_parts: int = MAX_FILE_PARTS_FREE) -> int:
    """Ambang split tertinggi yang masih aman untuk batas part ``max_parts``.

    Untuk akun biasa: 4000 × 512 KiB × 0.9 = 1800 MB — 10% di bawah batas
    server, memberi ruang bagi part yang membengkak karena pembagian
    berbasis durasi pada stream ber-bitrate variabel.
    """
    return int(int(max_parts) * MAX_PART_SIZE_BYTES * SPLIT_SAFETY_FACTOR)
