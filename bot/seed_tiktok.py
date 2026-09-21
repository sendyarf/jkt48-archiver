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

# Batas panjang untuk pencocokan berbasis nama inti. Pencocokan persis aman
# sejak 3 huruf ('lyn'), sedangkan pencocokan awalan ('kathrin' → 'kathrina')
# baru dipakai sejak 5 huruf supaya tidak salah pasang.
MIN_CORE_LENGTH = 3
MIN_PREFIX_LENGTH = 5


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


def core_name(value: str) -> str:
    """
    Nama inti sebuah username: tanpa penanda `jkt48`, angka, dan inisial
    satu huruf. Dipakai sebagai lapis kedua pencocokan karena username TikTok
    sering memakai bentuk berbeda dari username IDN.

    'jkt48.lyn.s'      → 'lyn'        (cocok dengan member `jkt48_lyn`)
    'jkt48.ella.a'     → 'ella'
    'jkt48.raisha.s'   → 'raisha'
    'kathrinjkt48'     → 'kathrin'    (cocok awalan `jkt48_kathrina`)
    'jkt48.aurellia_'  → 'aurellia'
    'jkt48.u16'        → ''           (akun cadangan, tidak pernah dicocokkan)
    """
    parts = [p for p in re.split(r"[^a-z0-9]+", (value or "").lower()) if p]
    cleaned: list[str] = []
    for part in parts:
        if part == "jkt48":
            continue
        part = part.replace("jkt48", "")
        part = re.sub(r"\d+", "", part)
        if len(part) <= 1:  # inisial (`.s`, `.a`) atau sisa angka (`u16` → 'u')
            continue
        cleaned.append(part)
    return "".join(cleaned)


def is_backup_account(unique_id: str) -> bool:
    """Akun cadangan u16 dipakai bersama beberapa member → jangan dipetakan."""
    return slugify(unique_id).endswith(BACKUP_ACCOUNT_SUFFIX)


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


def build_member_core_index() -> dict:
    """
    Peta nama inti → daftar member, untuk pencocokan lapis kedua.

    Berupa daftar (bukan satu member) supaya nama inti yang dipakai lebih dari
    satu member bisa dideteksi dan DILEWATI alih-alih salah pasang.
    """
    index: dict = {}
    for member in database.get_all_member_hls(include_disabled=True):
        for value in (member.get("username") or "", member.get("display_name") or ""):
            core = core_name(value)
            if len(core) < MIN_CORE_LENGTH:
                continue
            bucket = index.setdefault(core, [])
            if all(m.get("username") != member.get("username") for m in bucket):
                bucket.append(member)
    return index


def _match_by_core(unique_id: str, core_index: dict) -> Optional[dict]:
    """
    Lapis kedua: cocokkan nama inti akun dengan nama inti member.

    Urutan: inti sama persis → salah satu inti menjadi awalan inti lainnya
    (`kathrin` ⊂ `kathrina`). Awalan hanya dipakai bila inti persis TIDAK ADA
    sama sekali — kalau inti persisnya ada tetapi ambigu, hasilnya `None`
    supaya tidak menebak (mis. `raisha` ambigu tidak boleh jatuh ke member
    `raishakedua`). Hasil juga hanya diterima bila tunggal.
    """
    core = core_name(unique_id)
    if len(core) < MIN_CORE_LENGTH:
        return None

    exact = core_index.get(core) or []
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None

    if len(core) < MIN_PREFIX_LENGTH:
        return None
    candidates: list[dict] = []
    for member_core, members in core_index.items():
        shorter, longer = sorted((member_core, core), key=len)
        if len(shorter) < MIN_PREFIX_LENGTH or not longer.startswith(shorter):
            continue
        for member in members:
            if all(m.get("username") != member.get("username") for m in candidates):
                candidates.append(member)
    if len(candidates) == 1:
        return candidates[0]
    return None


def match_member(unique_id: str, index: dict, core_index: Optional[dict] = None) -> Optional[dict]:
    """
    Cari member yang paling cocok untuk sebuah username TikTok.

    Lapis 1 memakai varian nama persis (`name_variants`); bila gagal, lapis 2
    memakai nama inti (`core_name`). Akun cadangan `u16` tidak pernah dipetakan
    ke satu member karena dipakai bersama.
    """
    for candidate in sorted(name_variants(unique_id), key=len, reverse=True):
        member = index.get(candidate)
        if member:
            return member
    if core_index is None or is_backup_account(unique_id):
        return None
    return _match_by_core(unique_id, core_index)


def seed(accounts: list[dict], dry_run: bool = False) -> tuple[list[str], list[str]]:
    """
    Tulis akun ke database.

    Returns:
        (daftar USERNAME yang belum cocok ke member, daftar username cadangan)
    """
    index = build_member_index()
    core_index = build_member_core_index()
    unmatched: list[str] = []
    backups: list[str] = []
    core_matched = 0

    for entry in accounts:
        unique_id = (entry.get("unique_id") or entry.get("username") or "").strip().lstrip("@")
        if not unique_id:
            continue
        enabled = entry.get("enabled", True)
        member = match_member(unique_id, index)
        matched_by_core = False
        if member is None and not is_backup_account(unique_id):
            member = _match_by_core(unique_id, core_index)
            matched_by_core = member is not None
            core_matched += 1 if matched_by_core else 0
        if member is None:
            unmatched.append(unique_id)
            if is_backup_account(unique_id):
                backups.append(unique_id)
        display_name = (
            entry.get("display_name")
            or (member or {}).get("display_name")
            or ""
        )
        member_username = entry.get("member_username") or (member or {}).get("username") or ""

        action = "DRY-RUN" if dry_run else "seed"
        logger.info(
            "[%s] %-22s → %s%s%s",
            action,
            unique_id,
            display_name or "(nama belum diketahui)",
            f" [{member_username}]" if member_username else "",
            " (via nama inti)" if matched_by_core else "",
        )
        if not dry_run:
            database.upsert_tiktok_account(
                unique_id=unique_id,
                display_name=display_name,
                member_username=member_username or None,
                enabled=bool(enabled),
            )
    if core_matched:
        logger.info(
            "Pencocokan lapis kedua (nama inti) berhasil untuk %d akun.", core_matched
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
