"""
upload_pending.py - Upload pending / backlogged videos on VPS to Telegram channel.

Usage on VPS:
    python -m bot.upload_pending              # Upload all pending sessions found in DB
    python -m bot.upload_pending --dry-run    # Preview pending videos without uploading
    python -m bot.upload_pending --scan-dir   # Also scan DOWNLOAD_DIR for files not yet in DB
    python -m bot.upload_pending --keep       # Keep files on disk even if AUTO_DELETE is true
"""
import argparse
import asyncio
import hashlib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import colorlog

from bot.config import Config
from bot import database
from bot import timeutil
from bot.main import JKT48LiveBot
from bot.video_splitter import get_default_max_bytes, get_video_metadata

logger = logging.getLogger("upload_pending")


def _setup_logging() -> None:
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s [%(levelname)s] %(name)s%(reset)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)


def fmt_size(b: Optional[int]) -> str:
    if not b:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _safe_stat_size(path: Path) -> int:
    """Return a file size without letting a disappearing file abort a queue."""
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        pass
    return 0


def _register_untracked_file(item: dict) -> str:
    """Persist a scanned file so partial Telegram/YouTube work survives retry.

    The scanner historically passed a path straight to the upload callback
    without creating a ``live_sessions`` row.  If Telegram succeeded and YouTube
    hit quota, the next scan had no marker and uploaded Telegram again.  A
    deterministic ID keeps the row stable across repeated ``--scan-dir`` runs.
    """
    path = Path(item["file_path"]).resolve()
    existing = database.get_session_by_file_path(str(path))
    if existing is not None:
        item["source"] = "db"
        item["live_id"] = str(existing["live_id"])
        item["db_status"] = existing["status"]
        return str(existing["live_id"])

    digest = hashlib.sha1(str(path).encode("utf-8", errors="replace")).hexdigest()[:16]
    live_id = f"untracked_{digest}"
    database.insert_live(
        live_id=live_id,
        member_username=item["member_username"],
        member_name=item["member_name"],
        started_at=item["started_at"],
        platform=item.get("platform") or "idn",
    )
    database.update_status(
        live_id,
        "pending_upload",
        file_path=str(path),
        file_size_bytes=_safe_stat_size(path),
        error_message="",
    )
    item["source"] = "db"
    item["live_id"] = live_id
    item["db_status"] = "pending_upload"
    return live_id




def _get_effective_upload_state(live_id: str) -> dict:
    """Return markers from a finalized group, or from this row alone.

    A concat-failed merge group keeps its historical ``merge_group_id`` on every
    segment, but those segments are independent uploads.  Reading the raw group
    ID would let one completed segment make the others appear complete.
    """
    group_id = database.get_finalized_upload_group_id(live_id)
    return database.get_group_upload_state(live_id, group_id)

async def collect_pending_items(scan_dir: bool = False) -> list[dict]:
    """
    Collect all pending videos from DB and optionally directly from DOWNLOAD_DIR.
    """
    database.init_db()
    # CLI dapat dijalankan setelah bot mati mendadak. Reconcile first so a
    # missing source file is not silently skipped and a completed pipeline is
    # not retried forever.
    database.recover_interrupted_uploads()
    items: list[dict] = []
    seen_paths: set[str] = set()

    # 1. Collect from database
    db_items = database.get_all_pending_videos()
    for s in db_items:
        fp = s.get("file_path")
        if not fp:
            continue
        resolved = database.get_available_upload_path(s.get("live_id") or "")
        p = Path(resolved or fp)
        if _safe_stat_size(p) > 0:
            resolved_key = str(p.resolve())
            if resolved_key not in seen_paths:
                seen_paths.add(resolved_key)
                items.append({
                    "source": "db",
                    "live_id": s.get("live_id"),
                    "member_username": s.get("member_username") or "jkt48",
                    "member_name": s.get("member_name") or s.get("member_username") or "JKT48 Member",
                    "started_at": s.get("started_at") or s.get("created_at") or timeutil.utc_now_iso(),
                    "platform": s.get("platform") or database.get_platform_for_live(s.get("live_id") or ""),
                    "file_path": p,
                    "size_bytes": _safe_stat_size(p),
                    "db_status": s.get("status"),
                })

    # 2. Optionally scan DOWNLOAD_DIR for untracked videos
    if scan_dir:
        download_dir = Path(Config.DOWNLOAD_DIR)
        if download_dir.exists():
            for f in sorted(download_dir.iterdir()):
                size_bytes = _safe_stat_size(f)
                if size_bytes > 0 and f.suffix.lower() in (".mp4", ".mkv", ".ts"):
                    resolved = str(f.resolve())
                    # Skip temporary files or split parts
                    if "_tgpart_" in f.name or "_thumb.jpg" in f.name or f.name.startswith("merge_"):
                        continue
                    if resolved not in seen_paths:
                        seen_paths.add(resolved)
                        # Attempt to extract member username from filename: e.g. "jkt48_ella_..."
                        stem_parts = f.stem.split("_")
                        if len(stem_parts) >= 2 and stem_parts[0].lower() == "jkt48":
                            u = f"{stem_parts[0]}_{stem_parts[1]}".lower()
                        else:
                            u = stem_parts[0].lower()

                        items.append({
                            "source": "disk",
                            "live_id": f.stem,
                            "member_username": u,
                            "member_name": u,
                            "started_at": datetime.fromtimestamp(
                        f.stat().st_mtime, timezone.utc
                    ).isoformat(),
                            "platform": database.get_platform_for_live(f.stem),
                            "file_path": f,
                            "size_bytes": size_bytes,
                            "db_status": "untracked",
                        })

    return items


async def process_uploads(items: list[dict], dry_run: bool = False, keep_files: bool = False) -> None:
    """Run the same Telegram-then-YouTube pipeline used by the main bot.

    ``items`` may contain database rows or untracked files.  Database rows are
    updated through the normal idempotent markers; untracked files are uploaded
    once but have no durable row to update.
    """
    max_bytes = get_default_max_bytes()
    total_size = sum(item["size_bytes"] for item in items)

    print("\n" + "=" * 70)
    print("  📋 JKT48 Live - Pending Videos Queue")
    print("  Target Upload    : Telegram archive → YouTube playback")
    print(f"  Telegram Channel : {Config.TELEGRAM_ARCHIVE_CHANNEL_ID or Config.TELEGRAM_CHANNEL_ID}")
    print(f"  Split Threshold  : {Config.TELEGRAM_MAX_FILE_SIZE_MB} MB")
    print(f"  Total Videos     : {len(items)}")
    print(f"  Total File Size  : {fmt_size(total_size)}")
    if dry_run:
        print("  Mode             : [DRY-RUN] (hanya preview, tidak upload)")
    print("=" * 70 + "\n")

    for i, item in enumerate(items, start=1):
        p: Path = item["file_path"]
        size = _safe_stat_size(p)
        split_note = " (akan di-split > 2GB)" if size > max_bytes else ""
        print(f"  {i}. [{item['source'].upper()}] {item['member_name']} ({item['member_username']})")
        print(f"     File : {p.name} ({fmt_size(size)}){split_note}")
        print(f"     Path : {p}")
        print(f"     Waktu: {item['started_at']}")
        print()

    if dry_run:
        print("✅ Dry-run selesai. Jalankan tanpa --dry-run untuk memulai upload.\n")
        return

    bot = JKT48LiveBot()
    successful = 0
    failed = 0

    try:
        for item in items:
            p: Path = item["file_path"]
            live_id = str(item.get("live_id") or "")
            current_size = _safe_stat_size(p)
            if current_size <= 0:
                # The file can disappear after collection (manual cleanup,
                # cleanup race, or a merge group being finalized). Reconcile
                # the durable row and try the canonical group artifact once
                # more before declaring the item lost.
                if item.get("source") == "db" and live_id:
                    database.recover_interrupted_uploads()
                    resolved = database.get_available_upload_path(live_id)
                    if resolved and _safe_stat_size(Path(resolved)) > 0:
                        p = Path(resolved)
                        item["file_path"] = p
                        current_size = _safe_stat_size(p)
                    else:
                        row = database.get_session(live_id)
                        state = _get_effective_upload_state(live_id)
                        if (
                            row is not None
                            and row.get("status") == "done_youtube"
                            and bool((state.get("telegram_message_ids") or "").strip())
                            and bool((state.get("youtube_video_id") or "").strip())
                        ):
                            successful += 1
                            print(f"  ✅ {live_id} sudah lengkap; file lokal sudah dibersihkan.")
                        else:
                            failed += 1
                            logger.warning(
                                "File pending hilang atau kosong setelah dikumpulkan: %s",
                                p,
                            )
                        continue
                else:
                    failed += 1
                    logger.warning("File pending hilang atau kosong, dilewati: %s", p)
                    continue
            if item["source"] != "db":
                try:
                    _register_untracked_file(item)
                except Exception as exc:
                    failed += 1
                    logger.exception("Gagal registering file untracked %s: %s", p, exc)
                    continue
            live_id = item["live_id"]
            name = item["member_name"]
            username = item["member_username"]
            started = item["started_at"]
            print(
                f"\n[{successful + failed + 1}/{len(items)}] Memproses video "
                f"{name} ({p.name} - {fmt_size(current_size)})..."
            )

            try:
                pipeline_complete = await bot.handle_upload_ready(
                    live_id=live_id,
                    member_username=username,
                    member_name=name,
                    started_at=started,
                    file_path=str(p),
                    platform=item.get("platform") or "",
                    keep_file=keep_files,
                )

                if item["source"] == "db":
                    row = database.get_session(live_id)
                    state = _get_effective_upload_state(live_id)
                    complete = (
                        bool(pipeline_complete)
                        and row is not None
                        and row.get("status") == "done_youtube"
                        and bool((state.get("telegram_message_ids") or "").strip())
                        and bool((state.get("youtube_video_id") or "").strip())
                    )
                else:
                    # For untracked files there is no durable row to inspect;
                    # trust the pipeline's return value instead of guessing
                    # from AUTO_DELETE or file existence.
                    complete = bool(pipeline_complete)

                if complete:
                    successful += 1
                    print("  ✅ Telegram + YouTube selesai.")
                else:
                    failed += 1
                    print("  ⚠️ Pipeline belum selesai; file tetap disimpan untuk retry.")
            except Exception as exc:
                failed += 1
                logger.exception("Error saat memproses %s: %s", p.name, exc)
                if item["source"] == "db":
                    database.update_status(
                        live_id,
                        "pending_upload",
                        error_message=str(exc),
                    )
    finally:
        await bot.tg.disconnect()

    print("\n" + "=" * 70)
    print(f"  Pipeline Selesai: {successful} lengkap, {failed} tertunda.")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload pending JKT48 live videos to Telegram channel.")
    parser.add_argument("--dry-run", action="store_true", help="Preview pending videos without uploading")
    parser.add_argument("--scan-dir", action="store_true", help="Also scan DOWNLOAD_DIR for untracked videos")
    parser.add_argument("--keep", action="store_true", help="Keep video files on disk after upload")
    args = parser.parse_args()

    _setup_logging()

    try:
        items = asyncio.run(collect_pending_items(scan_dir=args.scan_dir))
        if not items:
            print("\n✅ Tidak ada video pending yang ditemukan untuk di-upload.\n")
            return
        asyncio.run(process_uploads(items, dry_run=args.dry_run, keep_files=args.keep))
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)


if __name__ == "__main__":
    main()
