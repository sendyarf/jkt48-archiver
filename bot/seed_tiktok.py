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
from bot.jkt48_members import handle_index, load_roster, nickname_variants

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


def member_display_name(member: Optional[dict]) -> str:
    """
    Nama tampilan `member_hls` yang layak dipakai.

    Sebagian baris lama masih memakai username sebagai nama (mis. `jkt48_lia`),
    dan itu placeholder — bukan nama manusia — jadi dianggap kosong supaya nama
    dari roster resmi bisa mengisinya.
    """
    if not member:
        return ""
    name = (member.get("display_name") or "").strip()
    if not name or name.lower() == (member.get("username") or "").strip().lower():
        return ""
    return name


def roster_display_name(roster_member: Optional[dict]) -> str:
    """
    Nama tampilan dari roster resmi jkt48.com: '<nickname> JKT48'.

    Dipakai hanya bila `member_hls` belum punya nama manusia (placeholder),
    dengan format yang sama seperti nama member lain ('Indah JKT48').
    """
    if not roster_member:
        return ""
    nickname = (roster_member.get("nickname") or "").strip()
    return f"{nickname} JKT48" if nickname else ""


def roster_avatar(roster_member: Optional[dict]) -> str:
    """URL foto member dari roster resmi ('' bila tidak ada)."""
    if not roster_member:
        return ""
    return (roster_member.get("photo") or "").strip()


def _roster_key(member: dict) -> str:
    """Kunci penghubung roster: nickname, jatuh ke `code` bila nickname kosong."""
    return (member.get("nickname") or member.get("code") or "").strip()


def _match_roster_member(
    member: dict, index: dict, core_index: dict
) -> Optional[dict]:
    """Petakan satu member roster resmi ke baris `member_hls`."""
    for variant in sorted(nickname_variants(member), key=len, reverse=True):
        hit = index.get(variant)
        if hit:
            return hit
    for variant in sorted(nickname_variants(member), key=len, reverse=True):
        hit = _match_by_core(variant, core_index)
        if hit:
            return hit
    return None


def build_roster_link(index: Optional[dict] = None, core_index: Optional[dict] = None) -> dict:
    """
    Hubungkan roster resmi jkt48.com ↔ `member_hls`, berkunci username TikTok.

    Return: {username_tiktok: {"roster": member_resmi, "member": baris_member_hls|None}}

    Sumbernya field `tiktok_account` resmi, jadi kepemilikan akun tidak lagi
    ditebak dari kemiripan nama (`jkt48.aurellia_` → `jkt48_lia`). Return {} bila
    cache roster belum ada, sehingga seed tetap jalan seperti sebelumnya.
    """
    roster = load_roster()
    if not roster:
        return {}
    if index is None:
        index = build_member_index()
    if core_index is None:
        core_index = build_member_core_index()

    member_by_key: dict = {}
    for member in roster:
        key = _roster_key(member)
        if not key:
            continue
        member_by_key[key] = _match_roster_member(member, index, core_index)

    link: dict = {}
    for handle, member in handle_index(roster).items():
        link[handle] = {"roster": member, "member": member_by_key.get(_roster_key(member))}
    logger.info(
        "Roster resmi: %d member, %d akun TikTok; %d akun terhubung ke member_hls.",
        len(roster),
        len(link),
        sum(1 for item in link.values() if item.get("member")),
    )
    return link


def build_roster_reverse(index: Optional[dict] = None,
                         core_index: Optional[dict] = None) -> dict:
    """
    Kebalikan `build_roster_link`: {username member_hls → member roster resmi}.

    Dipakai untuk akun TikTok yang TIDAK dilaporkan API resmi (field
    `tiktok_account` kosong) tetapi membernya jelas dari pencocokan nama —
    mis. `jkt48.heidi__` dan `jkt48.rara_`. Nama + foto resminya tetap bisa
    diisi tanpa menebak siapa pemilik akunnya. Return {} bila cache kosong.
    """
    roster = load_roster()
    if not roster:
        return {}
    if index is None:
        index = build_member_index()
    if core_index is None:
        core_index = build_member_core_index()

    reverse: dict = {}
    for member in roster:
        hit = _match_roster_member(member, index, core_index)
        if not hit:
            continue
        key = (hit.get("username") or "").strip().lower()
        if key and key not in reverse:
            reverse[key] = member
    return reverse


def seed(accounts: list[dict], dry_run: bool = False) -> tuple[list[str], list[str]]:
    """
    Tulis akun ke database.

    Urutan penetapan member untuk tiap akun:
    1. `member_username` di JSON daftar akun (koreksi manual, selalu menang),
    2. roster resmi jkt48.com (field `tiktok_account`) — sumber otoritatif,
    3. kecocokan nama: varian persis, lalu nama inti.
    Foto + nama dari roster juga ditulis ke `avatar_url` / `display_name`.

    Returns:
        (daftar USERNAME yang belum cocok ke member, daftar username cadangan)
    """
    index = build_member_index()
    core_index = build_member_core_index()
    roster_link = build_roster_link(index, core_index)
    roster_reverse = build_roster_reverse(index, core_index)
    unmatched: list[str] = []
    backups: list[str] = []
    core_matched = 0
    roster_matched = 0
    roster_enriched = 0
    avatars = 0

    for entry in accounts:
        unique_id = (entry.get("unique_id") or entry.get("username") or "").strip().lstrip("@")
        if not unique_id:
            continue
        enabled = entry.get("enabled", True)
        link = roster_link.get(unique_id.lower()) or {}
        roster_member = link.get("roster") or {}
        roster_hit = link.get("member")

        # Lapis 1 & 2: tebak dari kemiripan nama. Dipakai bila roster resmi
        # tidak ada (cache belum dibuat) atau tidak menemukan padanannya di
        # `member_hls`.
        name_member = match_member(unique_id, index)
        matched_by_core = False
        if name_member is None and not is_backup_account(unique_id):
            name_member = _match_by_core(unique_id, core_index)
            matched_by_core = name_member is not None
            core_matched += 1 if matched_by_core else 0

        roster_enriched_here = False
        if roster_hit is not None:
            member = roster_hit
            roster_matched += 1
            if (
                name_member is not None
                and name_member.get("username") != roster_hit.get("username")
            ):
                logger.warning(
                    "  %s: roster resmi menunjuk %s, pencocokan nama menunjuk %s — "
                    "memakai roster.",
                    unique_id,
                    roster_hit.get("username"),
                    name_member.get("username"),
                )
        else:
            member = name_member
            # Akun yang TIDAK dilaporkan API resmi (mis. `jkt48.heidi__`,
            # `jkt48.rara_`) tidak muncul di `roster_link`, padahal membernya
            # jelas dari pencocokan nama. Pencocokan balik mengisi nama + foto
            # resminya tanpa perlu menebak kepemilikan akun.
            if member is not None:
                reverse_hit = roster_reverse.get(
                    (member.get("username") or "").strip().lower()
                )
                if reverse_hit:
                    roster_member = reverse_hit
                    roster_enriched += 1
                    roster_enriched_here = True
        if member is None:
            unmatched.append(unique_id)
            if is_backup_account(unique_id):
                backups.append(unique_id)
        display_name = (
            entry.get("display_name")
            or member_display_name(member)
            or roster_display_name(roster_member)
            or ""
        )
        member_username = entry.get("member_username") or (member or {}).get("username") or ""
        avatar_url = roster_avatar(roster_member)
        if avatar_url:
            avatars += 1

        marker = ""
        if roster_hit is not None:
            marker = " (roster)"
        elif roster_enriched_here:
            marker = " (nama/foto roster)"
        elif matched_by_core:
            marker = " (via nama inti)"

        action = "DRY-RUN" if dry_run else "seed"
        logger.info(
            "[%s] %-22s → %s%s%s",
            action,
            unique_id,
            display_name or "(nama belum diketahui)",
            f" [{member_username}]" if member_username else "",
            marker,
        )
        if not dry_run:
            database.upsert_tiktok_account(
                unique_id=unique_id,
                display_name=display_name,
                member_username=member_username or None,
                enabled=bool(enabled),
                avatar_url=avatar_url or None,
            )
    if core_matched:
        logger.info(
            "Pencocokan lapis kedua (nama inti) berhasil untuk %d akun.", core_matched
        )
    if roster_matched:
        logger.info(
            "Roster resmi jkt48.com memetakan %d akun ke member; %d akun mendapat foto member.",
            roster_matched,
            avatars,
        )
    if roster_enriched:
        logger.info(
            "%d akun tidak dilaporkan API resmi tetapi mendapat nama/foto dari roster "
            "(pencocokan balik).",
            roster_enriched,
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
