"""
status.py - Tampilkan status bot JKT48 Live secara real-time dari database.

Jalankan di server:
    python3 -m bot.status                        # sekali lihat
    python3 -m bot.status --watch                # auto-refresh 10 detik
    python3 -m bot.status --cleanup              # hapus file stale (>24 jam)
    python3 -m bot.status --cleanup --hours 48   # stale threshold custom
    python3 -m bot.status --cleanup --dry-run    # preview tanpa hapus
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from bot import database
from bot import timeutil
from bot.config import Config


def fmt_size(b: int) -> str:
    if b is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def fmt_ago(ts: str) -> str:
    """
    Berapa lama sejak `ts`. Nilai tersimpan adalah UTC (baris baru) atau waktu
    naif tanpa penanda (baris lama) — lihat bot/timeutil.py.
    """
    if not ts:
        return "-"
    parsed = timeutil.parse_stored(ts, Config.LEGACY_NAIVE_TIME_OFFSET_HOURS)
    if parsed is None:
        return "-"
    diff = timeutil.utc_now() - parsed
    s = int(diff.total_seconds())
    if s < 0:
        s = 0
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m {s % 60}s ago"
    return f"{s // 3600}h {(s % 3600) // 60}m ago"


STATUS_ICON = {
    "detected":           "🔍",
    "downloading":        "⏺ ",
    "segment_done":       "✅",
    "download_complete":  "📦",
    "merging":            "🔀",
    "uploading_youtube":  "📤",
    "done_youtube":       "☁️ ",
    "uploading_telegram": "✈️ 📤",
    "done_telegram":      "✈️ ✅",
    "pending_upload":     "⏳",
    "failed":             "❌",
}


def check_token(token_file: str) -> str:
    """Return token validity status string."""
    if not token_file or not Path(token_file).exists():
        return "❌ File tidak ada"
    try:
        with open(token_file) as f:
            data = json.load(f)
        has_refresh = bool(data.get("refresh_token"))
        expiry = data.get("expiry") or data.get("token_expiry")
        if not has_refresh:
            return "⚠️  Tidak ada refresh_token — perlu re-auth!"
        if expiry:
            try:
                clean_exp = expiry.replace("Z", "+00:00")
                if "+" not in clean_exp and "-" not in clean_exp[10:]:
                    clean_exp += "+00:00"
                exp_dt = datetime.fromisoformat(clean_exp)
                if exp_dt < datetime.now(timezone.utc):
                    # Access token expired tapi refresh_token ada → auto-renew otomatis, normal
                    return "✅ Auto-renew (access token expired, refresh_token aktif)"
            except Exception:
                pass
        return "✅ Valid + refresh_token"
    except Exception as e:
        return f"❌ Error baca token: {e}"


def resolve_name(session: dict, name_lookup: dict[str, str]) -> str:
    """Return best available display name for a session."""
    mn = session.get("member_name") or ""
    mu = session.get("member_username") or ""
    if mn and mn.lower() != mu.lower():
        return mn
    return name_lookup.get(mu.lower(), mu)


def print_status() -> None:
    database.init_db()

    with database._get_conn() as conn:
        active_rows = conn.execute(
            """SELECT * FROM live_sessions
               WHERE status IN ('detected', 'downloading', 'segment_done',
                                'download_complete', 'merging', 'uploading_youtube',
                                'uploading_telegram', 'pending_upload')
               ORDER BY created_at DESC"""
        ).fetchall()
        active = [dict(r) for r in active_rows]

        recent_done = conn.execute(
            """SELECT * FROM live_sessions
               WHERE status IN ('done_youtube', 'done_telegram', 'failed')
               ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()
        done = [dict(r) for r in recent_done]

        hls_rows = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN hls_url IS NOT NULL AND hls_url != '' THEN 1 ELSE 0 END) as with_hls,
                      SUM(hls_confirmed) as confirmed
               FROM member_hls"""
        ).fetchone()

        unknown_rows = conn.execute(
            """SELECT username FROM member_hls
               WHERE (hls_url IS NULL OR hls_url = '') AND hls_confirmed = 0
               ORDER BY username"""
        ).fetchall()

        # Display name lookup: username → display_name from member_hls
        hls_name_rows = conn.execute("SELECT username, display_name FROM member_hls").fetchall()
        name_lookup = {
            r["username"].lower(): r["display_name"]
            for r in hls_name_rows
            if r["display_name"] and r["display_name"].lower() != r["username"].lower()
        }

    now = timeutil.format_display(
        timeutil.utc_now_iso(),
        "%Y-%m-%d %H:%M:%S",
        Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
        Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
    )
    print(f"\n{'='*65}")
    print(f"  JKT48 Live Bot Status — {now} {Config.DISPLAY_TIMEZONE_LABEL}")
    print(f"{'='*65}")

    # --- YouTube Channels ---
    yt_channels = database.get_youtube_channels()
    print(f"\n📺 YouTube Channels ({len(yt_channels)})\n")
    if yt_channels:
        QUOTA_LIMIT = 10000
        UPLOAD_COST = 1600  # units per upload
        for ch in yt_channels:
            label = ch["channel_label"]
            token = ch["token_file"]
            uploads = ch["uploads_today"]
            last_reset = ch.get("last_reset_at", "-")
            quota_used = uploads * UPLOAD_COST
            quota_left = max(0, QUOTA_LIMIT - quota_used)
            token_status = check_token(token)
            bar_filled = int(quota_used / QUOTA_LIMIT * 20)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            print(f"  📺 {label}")
            print(f"     Token  : {token_status}")
            print(f"     File   : {token}")
            print(f"     Upload : {uploads}x hari ini  (quota ~{quota_used:,}/{QUOTA_LIMIT:,} units)")
            print(f"     Sisa   : [{bar}] ~{quota_left:,} units ({max(0, quota_left // UPLOAD_COST)} upload lagi)")
            print(f"     Reset  : {last_reset}")
            print()
    else:
        print("  ⚠️  Tidak ada channel YouTube yang terkonfigurasi.")

    total = hls_rows["total"] or 0
    with_hls = hls_rows["with_hls"] or 0
    confirmed = hls_rows["confirmed"] or 0
    unknown_list = [r["username"] for r in unknown_rows]

    print(f"\n📡 HLS Coverage: {with_hls}/{total} member (confirmed: {confirmed})")
    if unknown_list:
        print(f"   ❓ Belum diketahui HLS: {', '.join(unknown_list)}")
    else:
        print(f"   ✅ Semua member sudah diketahui HLS-nya")

    print(f"\n{'─'*65}")
    if active:
        print(f"  🎙  Sesi Aktif ({len(active)})\n")
        for s in active:
            icon = STATUS_ICON.get(s["status"], "•")
            name = resolve_name(s, name_lookup)
            size = fmt_size(s.get("file_size_bytes"))
            started = fmt_ago(s.get("download_started_at") or s.get("created_at"))
            print(f"  {icon} {name:<22} {s['status']:<20} {size:>8}  ({started})")
    else:
        print(f"  💤  Tidak ada sesi aktif saat ini")

    print(f"\n{'─'*65}")
    if done:
        print(f"  📋 Selesai Terakhir (10)\n")
        for s in done:
            icon = STATUS_ICON.get(s["status"], "•")
            name = resolve_name(s, name_lookup)
            size = fmt_size(s.get("file_size_bytes"))
            dest = f"tg:{s['telegram_message_id']}" if s.get("telegram_message_id") else (f"yt:{s['youtube_video_id']}" if s.get("youtube_video_id") else "")
            ended = fmt_ago(s.get("download_ended_at") or s.get("created_at"))
            print(f"  {icon} {name:<22} {size:>8}  {dest:<25} ({ended})")

    print(f"{'='*65}\n")


def run_cleanup(hours: int = 24, dry_run: bool = False) -> None:
    """Hapus hanya artefak video yang sudah terminal dan aman.

    ``pending_upload`` dan ``download_complete`` sengaja tidak pernah ikut
    dibersihkan: keduanya masih membutuhkan file lokal untuk Telegram/YouTube
    pipeline.  Cleanup juga melindungi file yang direferensikan row belum
    selesai, termasuk ketika beberapa segmen/merge group memakai path yang sama.
    """
    database.init_db()

    # Cutoff dihitung di SQL memakai julianday (UTC vs UTC) agar tidak
    # bergantung zona waktu server maupun format string.
    hours_param = float(hours)
    display_cutoff = timeutil.format_display(
        (timeutil.utc_now() - timedelta(hours=hours)).isoformat(),
        "%Y-%m-%d %H:%M:%S",
        Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
        Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
    )

    tag = "[DRY-RUN] " if dry_run else ""

    print(f"\n{'='*65}")
    print(f"  {tag}Cleanup — sesi lebih tua dari {hours} jam")
    print(f"  Cutoff: {display_cutoff} {Config.DISPLAY_TIMEZONE_LABEL}")
    print(f"{'='*65}\n")

    with database._get_conn() as conn:
        hls_name_rows = conn.execute("SELECT username, display_name FROM member_hls").fetchall()
        name_lookup = {
            r["username"].lower(): r["display_name"]
            for r in hls_name_rows
            if r["display_name"] and r["display_name"].lower() != r["username"].lower()
        }
        rows = conn.execute(
            """SELECT * FROM live_sessions
               WHERE status = 'failed'
                 AND (julianday('now') - julianday(created_at)) * 24 > ?
                 AND file_path IS NOT NULL AND file_path != ''
                 AND NOT EXISTS (
                     SELECT 1 FROM live_sessions AS active
                     WHERE active.file_path = live_sessions.file_path
                       AND active.status IN (
                           'pending_upload', 'download_complete',
                           'uploading_telegram', 'uploading_youtube'
                       )
                 )
               ORDER BY created_at ASC""",
            (hours_param,),
        ).fetchall()
        stale = [dict(r) for r in rows]

    if not stale:
        print("  ✅ Tidak ada sesi stale yang perlu dibersihkan.\n")
        return

    total_freed = 0
    cleaned = 0

    for s in stale:
        live_id = s["live_id"]
        name = resolve_name(s, name_lookup)
        file_path = s.get("file_path")
        size_bytes = s.get("file_size_bytes") or 0
        status = s["status"]
        age = fmt_ago(s.get("created_at"))

        file_exists = bool(file_path and Path(file_path).exists())
        size_str = fmt_size(size_bytes)

        print(f"  {'⚠ ' if dry_run else '🗑 '} {name:<20} {status:<20} {size_str:>8}  ({age})")
        if file_path:
            print(f"     📁 {file_path}  {'[ADA]' if file_exists else '[TIDAK ADA]'}")

        if not dry_run:
            if file_exists:
                try:
                    os.remove(file_path)
                    total_freed += size_bytes
                    print(f"     ✅ File dihapus")
                except OSError as e:
                    print(f"     ❌ Gagal hapus: {e}")
            else:
                total_freed += size_bytes

            with database._get_conn() as conn:
                conn.execute(
                    "UPDATE live_sessions SET status = 'failed', error_message = ? WHERE live_id = ?",
                    (f"Cleaned up after {hours}h (was: {status})", live_id),
                )
            cleaned += 1

        print()

    print(f"{'─'*65}")
    if dry_run:
        total_size = sum((s.get("file_size_bytes") or 0) for s in stale)
        print(f"  [DRY-RUN] {len(stale)} sesi akan dihapus, {fmt_size(total_size)} akan dibebaskan.")
        print(f"  Jalankan tanpa --dry-run untuk eksekusi.\n")
    else:
        print(f"  ✅ {cleaned} sesi dibersihkan, {fmt_size(total_freed)} dibebaskan.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="JKT48 Live Bot status viewer & cleanup")
    parser.add_argument("--watch", action="store_true", help="Auto-refresh setiap N detik")
    parser.add_argument("--interval", type=int, default=10, help="Interval detik untuk --watch")
    parser.add_argument("--cleanup", action="store_true", help="Hapus file stale dari disk & DB")
    parser.add_argument("--hours", type=int, default=24, help="Hapus sesi lebih tua dari N jam (default: 24)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview saja, tidak benar-benar hapus")
    args = parser.parse_args()

    if args.cleanup:
        run_cleanup(hours=args.hours, dry_run=args.dry_run)
    elif args.watch:
        while True:
            print("\033[2J\033[H", end="")  # clear terminal
            print_status()
            time.sleep(args.interval)
    else:
        print_status()


if __name__ == "__main__":
    main()
