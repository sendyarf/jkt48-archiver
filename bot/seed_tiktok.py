"""
seed_tiktok.py - Isi tabel `tiktok_accounts` dari `tiktok_accounts.json`.

Sifat skrip ini:
- IDEMPOTEN — aman dijalankan berulang; nama/`enabled` yang sudah ada tidak
  ditimpa (lihat `database.upsert_tiktok_account`).
- TIDAK mengubah perilaku bot selama `TIKTOK_ENABLED=false`.
- Cocokkan akun TikTok dengan member JKT48 di `member_hls` secara otomatis
  (nama tampilan + username), sehingga halaman /tiktok bisa menampilkan nama
  member, bukan hanya username TikTok. Hasil pencocokan hanya ditulis ke
  database, bukan ke JSON.

Jalankan di server:
    python3 -m bot.seed_tiktok
    python3 -m bot.seed_tiktok --dry-run
    python3 -m bot.seed_tiktok --file tiktok_accounts.json
"""
import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from bot import database
from bot.config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Marker akun cadangan untuk member yang belum punya akun pribadi.
BACKUP_ACCOUNT_SUFFIX = "u16"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def name_variants(value: str) -> set:
    """
    Variasi nama untuk pencocokan akun TikTok ↔ member.

    'jkt48_indah' → {'jkt48indah', 'indah'}
    'Indah JKT48' → {'indahjkt48', 'indah'}
    """
    slug = slugify(value)
    if not slug:
        return set()
    variants = {slug}
    if slug.startswith("jkt48"):
        variants.add(slug[5:])
    if slug.endswith("jkt48"):
        variants.add(slug[:-5])
    return {v for v in variants if v}


def load_accounts(path: str) -> list[dict]:
    """Baca daftar akun dari file JSON."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File daftar akun TikTok tidak ditemukan: {path}")
    data: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
    accounts = data.get("accounts") or []
    if not isinstance(accounts, list):
        raise ValueError("Field 'accounts' harus berupa array")
    return list(accounts)


def build_member_index() -> dict:
    """Peta nama-tersedia → baris member_hls, untuk pencocokan otomatis."""
    index: dict = {}
    for member in database.get_all_member_hls(include_disabled=True):
        username = member.get("username") or ""
        display = member.get("display_name") or ""
        for candidate in name_variants(username) | name_variants(display):
            index.setdefault(candidate, member)
    return index


def match_member(unique_id: str, index: dict) -> Optional[dict]:
    """Cari member yang paling cocok untuk sebuah username TikTok."""
    for candidate in sorted(name_variants(unique_id), key=len, reverse=True):
        member = index.get(candidate)
        if member:
            return member
    return None


def seed(accounts: list[dict], dry_run: bool = False) -> tuple[list[str], list[str]]:
    """
    Tulis akun ke database.

    Returns:
        (daftar USERNAME yang belum cocok ke member, daftar username cadangan)
    """
    index = build_member_index()
    unmatched: list[str] = []
    backups: list[str] = []

    for entry in accounts:
        unique_id = (entry.get("unique_id") or entry.get("username") or "").strip().lstrip("@")
        if not unique_id:
            continue
        enabled = entry.get("enabled", True)
        member = match_member(unique_id, index)
        if member is None:
            unmatched.append(unique_id)
            if slugify(unique_id).endswith(BACKUP_ACCOUNT_SUFFIX):
                backups.append(unique_id)
        display_name = (
            entry.get("display_name")
            or (member or {}).get("display_name")
            or ""
        )
        member_username = entry.get("member_username") or (member or {}).get("username") or ""

        action = "DRY-RUN" if dry_run else "seed"
        logger.info(
            "[%s] %-22s → %s%s",
            action,
            unique_id,
            display_name or "(nama belum diketahui)",
            f" [{member_username}]" if member_username else "",
        )
        if not dry_run:
            database.upsert_tiktok_account(
                unique_id=unique_id,
                display_name=display_name,
                member_username=member_username or None,
                enabled=bool(enabled),
            )
    return unmatched, backups


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed akun TikTok JKT48 ke database.")
    parser.add_argument("--file", default=Config.TIKTOK_ACCOUNTS_FILE,
                        help="file JSON daftar akun (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="tampilkan rencana tanpa menulis DB")
    args = parser.parse_args()

    database.init_db()
    accounts = load_accounts(args.file)
    logger.info("Membaca %d akun dari %s", len(accounts), args.file)

    unmatched, backups = seed(accounts, dry_run=args.dry_run)

    logger.info("Selesai. %d akun diproses.", len(accounts))
    if unmatched:
        logger.info(
            "%d akun belum punya padanan member (isi `member_username` manual bila perlu): %s",
            len(unmatched), ", ".join(unmatched),
        )
    if backups:
        logger.info(
            "Akun cadangan u16 terdeteksi (%d): %s — biasanya berisi beberapa member "
            "sekaligus sehingga tidak dipetakan ke satu member.",
            len(backups), ", ".join(backups),
        )


if __name__ == "__main__":
    main()
