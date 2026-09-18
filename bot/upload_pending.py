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
import logging
import os
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
from bot.downloader import delete_file, get_file_size_bytes
from bot.telegram_sender import TelegramSender
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


async def collect_pending_items(scan_dir: bool = False) -> list[dict]:
    """
    Collect all pending videos from DB and optionally directly from DOWNLOAD_DIR.
    """
    database.init_db()
    items: list[dict] = []
    seen_paths: set[str] = set()

    # 1. Collect from database
    db_items = database.get_all_pending_videos()
    for s in db_items:
        fp = s.get("file_path")
        if not fp:
            continue
        p = Path(fp)
        if p.exists():
            resolved = str(p.resolve())
            if resolved not in seen_paths:
                seen_paths.add(resolved)
                items.append({
                    "source": "db",
                    "live_id": s.get("live_id"),
                    "member_username": s.get("member_username") or "jkt48",
                    "member_name": s.get("member_name") or s.get("member_username") or "JKT48 Member",
                    "started_at": s.get("started_at") or s.get("created_at") or timeutil.utc_now_iso(),
                    "file_path": p,
                    "size_bytes": p.stat().st_size,
                    "db_status": s.get("status"),
                })

    # 2. Optionally scan DOWNLOAD_DIR for untracked videos
    if scan_dir:
        download_dir = Path(Config.DOWNLOAD_DIR)
        if download_dir.exists():
            for f in sorted(download_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".ts"):
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
                            "file_path": f,
                            "size_bytes": f.stat().st_size,
                            "db_status": "untracked",
                        })

    return items


async def process_uploads(items: list[dict], dry_run: bool = False, keep_files: bool = False) -> None:
    max_bytes = get_default_max_bytes()
    total_size = sum(item["size_bytes"] for item in items)

    print("\n" + "=" * 70)
    print("  📋 JKT48 Live - Pending Videos Queue")
    print(f"  Target Upload    : Telegram Channel ({Config.TELEGRAM_CHANNEL_ID})")
    print(f"  Split Threshold  : {Config.TELEGRAM_MAX_FILE_SIZE_MB} MB")
    print(f"  Total Videos     : {len(items)}")
    print(f"  Total File Size  : {fmt_size(total_size)}")
    if dry_run:
        print("  Mode             : [DRY-RUN] (hanya preview, tidak upload)")
    print("=" * 70 + "\n")

    for i, item in enumerate(items, start=1):
        p: Path = item["file_path"]
        size = item["size_bytes"]
        split_note = " (akan di-split > 2GB)" if size > max_bytes else ""
        print(f"  {i}. [{item['source'].upper()}] {item['member_name']} ({item['member_username']})")
        print(f"     File : {p.name} ({fmt_size(size)}){split_note}")
        print(f"     Path : {p}")
        print(f"     Waktu: {item['started_at']}")
        print()

    if dry_run:
        print("✅ Dry-run selesai. Jalankan tanpa --dry-run untuk memulai upload.\n")
        return

    # Initialize Telegram sender
    tg = TelegramSender()
    print("Connecting to Telegram...")
    await tg.connect()

    successful = 0
    failed = 0

    try:
        for idx, item in enumerate(items, start=1):
            p: Path = item["file_path"]
            live_id = item["live_id"]
            name = item["member_name"]
            username = item["member_username"]
            started = item["started_at"]

            print(f"\n[{idx}/{len(items)}] Mengunggah video untuk {name} ({p.name} - {fmt_size(p.stat().st_size)})...")

            if item["source"] == "db":
                database.update_status(live_id, "uploading_telegram")

            try:
                msg_ids = await tg.upload_video_with_splitting(
                    file_path=p,
                    member_name=name,
                    member_username=username,
                    started_at=started,
                )

                if msg_ids:
                    successful += 1
                    print(f"  ✅ Sukses terkirim ke Telegram! Message ID: {msg_ids}")

                    if item["source"] == "db":
                        database.update_status(
                            live_id,
                            "done_telegram",
                            telegram_message_id=msg_ids[0],
                        )

                    # Delete original file if auto-delete enabled and not overridden
                    if Config.AUTO_DELETE_AFTER_UPLOAD and not keep_files:
                        delete_file(p)
                        print(f"  🗑️  File lokal dihapus dari VPS: {p.name}")
                else:
                    failed += 1
                    print(f"  ❌ Gagal mengunggah {p.name}: Tidak ada message ID returned.")
                    if item["source"] == "db":
                        database.update_status(live_id, "pending_upload", error_message="Upload failed")

            except Exception as exc:
                failed += 1
                logger.exception("Error saat mengunggah %s: %s", p.name, exc)
                if item["source"] == "db":
                    database.update_status(live_id, "pending_upload", error_message=str(exc))

    finally:
        await tg.disconnect()

    print("\n" + "=" * 70)
    print(f"  Upload Selesai: {successful} berhasil, {failed} gagal.")
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
