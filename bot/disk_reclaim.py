"""
disk_reclaim.py - Reclaim disk space held by recordings that will never upload.

Why this exists
---------------
Every failed upload leaves its 1-2 GB recording on disk
(`AUTO_DELETE_AFTER_UPLOAD` only deletes after a *successful* upload). The
retry queue (`get_all_pending_videos`) has no age limit, and the manual
`bot.status --cleanup` deliberately SKIPS `pending_upload`/`download_complete`.
So dead recordings were a permanent black hole: nothing would ever upload them
and nothing would ever delete them.

Once the VPS disk dropped below `MIN_FREE_DISK_MB`, the disk guard in
`main.py` set `active_now = []` and blocked **every** new recording - for IDN
*and* Showroom. From the outside that looked like "the bot sometimes records and
sometimes does not, and cannot even detect who is live".

This module closes that deadlock by deleting only files that are provably
terminal: their session already failed (dead-letter) or finished, and no other
row still references the same path in a live status.

Safety rules (a file is deleted only when ALL hold)
--------------------------------------------------
1. Its own session status is terminal - never `pending_upload`, `downloading`,
   `merging`, `uploading_*` or `segment_done`.
2. No OTHER row references the same `file_path` in a non-terminal status
   (merge groups share one path across several segments).
3. It is older than `DISK_RECLAIM_MIN_AGE_HOURS`, so a just-failed file still
   gets its normal retry chance before being reclaimed.

Deletion is therefore limited to files nothing can upload anymore. Callers that
want a preview pass ``dry_run=True``.
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot import database
from bot.config import Config

logger = logging.getLogger(__name__)

# Status where a file is provably not waiting for any upload anymore.
TERMINAL_STATUSES = ("failed", "abandoned", "done_telegram", "done_youtube")

# Statuses that still need their local file (recording or upload in flight).
ACTIVE_STATUSES = (
    "detected",
    "downloading",
    "segment_done",
    "merging",
    "pending_upload",
    "download_complete",
    "uploading_telegram",
    "uploading_youtube",
)


@dataclass
class ReclaimResult:
    """Ringkasan satu kali تشغيل reclaim."""

    freed_bytes: int = 0
    removed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / (1024 * 1024)

    def __bool__(self) -> bool:
        return self.removed > 0


def _terminal_placeholders() -> str:
    return ", ".join(f"'{s}'" for s in TERMINAL_STATUSES)


def _active_placeholders() -> str:
    return ", ".join(f"'{s}'" for s in ACTIVE_STATUSES)


def find_reclaimable(
    min_age_hours: Optional[float] = None,
    limit: int = 200,
) -> list[dict]:
    """
    Sesi terminal lama yang file-nya masih ada dan aman dihapus.

    Diurutkan dari yang paling lama (selama ini paling banyak membebaskan ruang).
    Mengembalikan daftar dict baris `live_sessions`.
    """
    hours = float(
        Config.DISK_RECLAIM_MIN_AGE_HOURS if min_age_hours is None else min_age_hours
    )
    terminal = _terminal_placeholders()
    active = _active_placeholders()
    with database._get_conn() as conn:
        rows = conn.execute(
            f"""SELECT s.* FROM live_sessions s
                WHERE s.status IN ({terminal})
                  AND s.file_path IS NOT NULL AND s.file_path != ''
                  AND (julianday('now') - julianday(
                            COALESCE(s.download_ended_at, s.created_at))) * 24 > ?
                  AND NOT EXISTS (
                      SELECT 1 FROM live_sessions other
                      WHERE other.file_path = s.file_path
                        AND other.status IN ({active})
                  )
                ORDER BY (julianday('now') - julianday(
                            COALESCE(s.download_ended_at, s.created_at))) DESC
                LIMIT ?""",
            (hours, max(1, int(limit))),
        ).fetchall()
    return [dict(r) for r in rows]


def reclaim_disk(
    target_free_mb: Optional[int] = None,
    dry_run: bool = False,
    min_age_hours: Optional[float] = None,
    max_files: int = 200,
) -> ReclaimResult:
    """
    Hapus file rekaman yang sudah terminal sampai ruang bebas cukup.

    Berhenti begitu ruang bebas mencapai `target_free_mb` (default
    `MIN_FREE_DISK_MB`), jadi tidak membuang lebih dari yang diperlukan.

    `dry_run=True` hanya melaporkan tanpa menghapus apa pun.
    """
    from bot.downloader import available_disk_mb

    result = ReclaimResult()
    target = int(Config.MIN_FREE_DISK_MB if target_free_mb is None else target_free_mb)
    rows = find_reclaimable(min_age_hours=min_age_hours, limit=max_files)
    if not rows:
        return result

    freed = 0
    for row in rows:
        # Beri tahu kalau ruang sudah cukup sebelum memproses sisa antrean.
        if not dry_run:
            free_now = available_disk_mb()
            if free_now is not None and free_now >= target:
                break

        path = row.get("file_path") or ""
        size = int(row.get("file_size_bytes") or 0)
        try:
            if path and os.path.isfile(path):
                actual = os.path.getsize(path)
                if dry_run:
                    freed += actual
                    result.skipped += 1
                    logger.info(
                        "[DRY-RUN] reclaim %s (%.1f MB)", path, actual / (1024 * 1024)
                    )
                    continue
                os.remove(path)
                freed += actual
                result.freed_bytes += actual
                result.removed += 1
                logger.info(
                    "Reclaim disk: hapus %s (%.1f MB) - sesi %s status=%s",
                    Path(path).name, actual / (1024 * 1024),
                    row.get("live_id"), row.get("status"),
                )
            else:
                # File sudah hilang; rapikan marker-nya saja.
                result.skipped += 1
        except OSError as exc:
            result.failures.append(f"{path}: {exc}")
            logger.warning("Gagal reclaim %s: %s", path, exc)
            continue

        if not dry_run:
            database.update_status(
                str(row["live_id"]),
                "failed",
                error_message=(
                    f"Reclaim disk (terminal, age>{Config.DISK_RECLAIM_MIN_AGE_HOURS}h)"
                ),
            )

    if dry_run:
        result.freed_bytes = freed
    if result.removed:
        logger.warning(
            "💾 Reclaim disk: %d file dihapus, %.1f MB dibebaskan",
            result.removed, result.freed_mb,
        )
    return result


def reclaim_summary() -> dict:
    """Ringkasan cepat ukuran antrean dead (tanpa menghapus)."""
    rows = find_reclaimable(limit=10000)
    total = 0
    existing = 0
    for row in rows:
        path = row.get("file_path") or ""
        try:
            if path and os.path.isfile(path):
                total += os.path.getsize(path)
                existing += 1
        except OSError:
            continue
    return {
        "rows": len(rows),
        "existing_files": existing,
        "total_bytes": total,
        "total_mb": total / (1024 * 1024),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Reclaim disk space from terminal (never-uploadable) recordings."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only, delete nothing"
    )
    parser.add_argument(
        "--hours", type=float, default=None,
        help="Minimum age before a file may be reclaimed "
             f"(default {Config.DISK_RECLAIM_MIN_AGE_HOURS}h from .env)",
    )
    args = parser.parse_args()

    database.init_db()
    summary = reclaim_summary()
    print("=" * 65)
    print("  Reclaim disk — rekaman terminal yang tidak akan pernah ter-upload")
    print(
        f"  Kandidat: {summary['existing_files']} file "
        f"({summary['total_mb']:.1f} MB) dari {summary['rows']} baris"
    )
    print("=" * 65)

    result = reclaim_disk(dry_run=args.dry_run, min_age_hours=args.hours)
    if args.dry_run:
        print(f"  [DRY-RUN] {summary['existing_files']} file akan dihapus "
              f"({summary['total_mb']:.1f} MB).")
    else:
        print(f"  ✅ {result.removed} file dihapus, {result.freed_mb:.1f} MB dibebaskan.")
    if result.failures:
        print(f"  ⚠️ {len(result.failures)} file gagal dihapus:")
        for failure in result.failures[:10]:
            print(f"     - {failure}")


if __name__ == "__main__":
    main()