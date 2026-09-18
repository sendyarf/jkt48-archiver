"""
seed_showroom.py - Populate member_hls with Showroom room IDs.

Menyalin daftar room dari `showroom_rooms.json` ke kolom Showroom di
`member_hls` (showroom_room_id / showroom_name / showroom_only).

Sifat skrip ini:
- IDEMPOTEN — aman dijalankan berulang; hanya kolom Showroom yang ditulis.
- TIDAK menyentuh kolom IDN (hls_url, hls_confirmed) maupun `enabled`,
  jadi member yang sedang di-stop lewat CLI/admin bot tetap ter-stop.
- TIDAK mengubah perilaku bot. Jalur Showroom hanya aktif bila
  SHOWROOM_ENABLED=true (Fase 2).

Jalankan di server:
    python3 -m bot.seed_showroom
    python3 -m bot.seed_showroom --dry-run
    python3 -m bot.seed_showroom --file showroom_rooms.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from bot import database
from bot.config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def slugify(value: str) -> str:
    """'Lulu ' -> 'lulu', 'JKT48 Sona' -> 'jkt48sona'."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def load_rooms(path: str) -> tuple[list[dict], list[dict]]:
    """Baca file daftar room. Return (rooms, official_rooms)."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File daftar room tidak ditemukan: {path}")
    data: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
    rooms = data.get("rooms") or []
    official = data.get("official_rooms") or []
    if not isinstance(rooms, list):
        raise ValueError("Field 'rooms' harus berupa array")
    return list(rooms), list(official)


def build_rows(rooms: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Ubah entri file menjadi baris siap-tulis.

    Member yang tidak punya username IDN (idn_username kosong) dianggap
    Showroom-only: dibuatkan username sintetis `showroom_<slug>` dan
    ditandai showroom_only=1 agar dilewati IDN GraphQL discovery.
    """
    rows: list[dict] = []
    problems: list[str] = []
    seen_rooms: set[str] = set()
    seen_users: set[str] = set()

    for index, room in enumerate(rooms):
        room_id = str(room.get("room_id") or "").strip()
        display_name = str(room.get("display_name") or "").strip()
        showroom_name = str(room.get("showroom_name") or display_name).strip()

        if not room_id:
            problems.append(f"entri #{index} tanpa room_id → dilewati")
            continue
        if not room_id.isdigit():
            problems.append(f"room_id tidak valid '{room_id}' ({display_name}) → dilewati")
            continue
        if room_id in seen_rooms:
            problems.append(f"room_id ganda {room_id} ({display_name}) → dilewati")
            continue

        idn_username = str(room.get("idn_username") or "").strip().lower()
        showroom_only = not idn_username
        username = idn_username or f"showroom_{slugify(display_name) or room_id}"

        if username in seen_users:
            problems.append(
                f"username '{username}' dipakai dua room (room {room_id}) → dilewati"
            )
            continue

        seen_rooms.add(room_id)
        seen_users.add(username)
        rows.append(
            {
                "username": username,
                "room_id": room_id,
                "showroom_name": showroom_name,
                "display_name": display_name,
                "showroom_only": showroom_only,
            }
        )

    return rows, problems


def seed(rooms_file: Optional[str] = None, dry_run: bool = False) -> dict:
    """Tulis pasangan Showroom ke database. Return ringkasan statistik."""
    path = rooms_file or Config.SHOWROOM_ROOMS_FILE
    rooms, official = load_rooms(path)
    rows, problems = build_rows(rooms)

    stats = {
        "file": path,
        "rooms_in_file": len(rooms),
        "official_skipped": len(official),
        "rows": len(rows),
        "showroom_only": sum(1 for r in rows if r["showroom_only"]),
        "written": 0,
        "dry_run": dry_run,
        "problems": problems,
    }

    if dry_run:
        for row in rows[:5]:
            logger.info(
                "DRY-RUN %s → room %s (%s)%s",
                row["username"], row["room_id"], row["showroom_name"],
                " [showroom-only]" if row["showroom_only"] else "",
            )
        if len(rows) > 5:
            logger.info("DRY-RUN ... dan %d baris lainnya", len(rows) - 5)
        return stats

    database.init_db()
    for row in rows:
        database.set_member_showroom(
            username=row["username"],
            room_id=row["room_id"],
            showroom_name=row["showroom_name"],
            display_name=row["display_name"],
            showroom_only=row["showroom_only"],
        )
        stats["written"] += 1

    return stats


def report(stats: dict) -> None:
    logger.info("Daftar room   : %s", stats["file"])
    logger.info("Room di file  : %d (official dilewati: %d)",
                stats["rooms_in_file"], stats["official_skipped"])
    logger.info("Baris diproses: %d (showroom-only: %d)",
                stats["written"] if not stats["dry_run"] else stats["rows"],
                stats["showroom_only"])
    for problem in stats["problems"]:
        logger.warning("MASALAH: %s", problem)

    if stats["dry_run"]:
        logger.info("Mode DRY-RUN — tidak ada perubahan database.")
        return

    # Verifikasi: berapa member yang sudah punya room Showroom?
    members = database.get_all_member_hls(include_disabled=True)
    with_room = [m for m in members if (m.get("showroom_room_id") or "").strip()]
    logger.info(
        "Verifikasi    : %d/%d member punya showroom_room_id",
        len(with_room), len(members),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed room Showroom ke member_hls (idempoten)."
    )
    parser.add_argument("--file", default=None,
                        help=f"File daftar room (default: {Config.SHOWROOM_ROOMS_FILE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Tampilkan rencana tanpa menulis ke database")
    args = parser.parse_args()

    try:
        stats = seed(rooms_file=args.file, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Gagal membaca daftar room: %s", exc)
        return 1

    report(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())