"""
member_manager.py - Member (channel) management: add / stop / resume / remove / list.

Sumber kebenaran pemantauan channel adalah tabel `member_hls` di SQLite:

  * enabled = 1 → HLS di-probe tiap siklus poll, direkam saat member live
  * enabled = 0 → diabaikan oleh monitor (status: STOP), tapi HLS URL tetap
                  disimpan sehingga resume instan tanpa discovery ulang

`members.txt` (MEMBERS_FILE) selalu disinkronkan otomatis:
  * add / resume → baris `username`
  * stop         → baris `# STOPPED: username` (komentar, tidak dibaca bot)

Modul ini dipakai bersama oleh `bot/member_cli.py` (CLI) dan `bot/admin_bot.py`
(Telegram admin bot) agar perilakunya identik.
"""
import logging
import re
from pathlib import Path
from typing import Optional

from bot import database
from bot import timeutil
from bot.config import Config

logger = logging.getLogger(__name__)

# Marker khusus untuk baris member yang di-stop di members.txt.
# Hanya baris dengan marker ini (atau baris polos) yang dianggap entry member —
# komentar bebas milik manusia tidak pernah disentuh.
STOPPED_MARKER = "# STOPPED:"

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")

# Status sesi live yang dianggap "masih berjalan"
LIVE_STATUSES = {
    "detected",
    "downloading",
    "segment_done",
    "download_complete",
    "merging",
    "uploading_youtube",
    "uploading_telegram",
    "pending_upload",
}

LIST_MODES = ("all", "active", "stopped", "live", "unknown")

# Guard agar migrasi skema (kolom `enabled`) dijalankan sekali per proses,
# walau modul ini dipanggil dari CLI/admin bot tanpa menjalankan bot utama.
_schema_ready = False


def _ensure_schema() -> None:
    """Jalankan migrasi skema database sekali saja sebelum operasi member."""
    global _schema_ready
    if _schema_ready:
        return
    database.init_db()
    _schema_ready = True


# ─── Helpers ────────────────────────────────────────────────────────────────

def normalize_username(raw: str) -> str:
    """
    Normalise user input into a bare member username.

    Accepts: 'jkt48_lulu', '@jkt48_lulu', 'jkt48_lulu/',
             'https://www.idn.app/jkt48_lulu'
    """
    u = (raw or "").strip().lower()
    if not u:
        return ""
    if "://" in u or "/" in u:
        u = u.rstrip("/").split("/")[-1]
    if "?" in u:
        u = u.split("?")[0]
    return u.lstrip("@").strip()


def validate_username(username: str) -> Optional[str]:
    """Return an error message if username is invalid, else None."""
    if not username:
        return "Username kosong. Contoh: jkt48_lulu"
    if not USERNAME_RE.match(username):
        return (
            f"Username tidak valid: '{username}'. "
            "Hanya huruf kecil, angka, '_', '-', '.' yang diizinkan."
        )
    return None


def channel_id_from_hls(hls_url: str) -> str:
    """Extract the AWS IVS channel ID from a playback URL ('' if unknown)."""
    if not hls_url:
        return ""
    match = re.search(r"channel\.([A-Za-z0-9_-]+)\.m3u8", hls_url)
    if match:
        return match.group(1)
    return ""


def fmt_ago(ts: str) -> str:
    """Human readable 'x lalu' from a stored timestamp string."""
    if not ts:
        return "-"
    parsed = timeutil.parse_stored(ts, Config.LEGACY_NAIVE_TIME_OFFSET_HOURS)
    if parsed is None:
        return "-"
    seconds = int((timeutil.utc_now() - parsed).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s lalu"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s lalu"
    if seconds < 86400:
        return f"{seconds // 3600}j {(seconds % 3600) // 60}m lalu"
    return f"{seconds // 86400}h lalu"


# ── members.txt sync ────────────────────────────────────────────────────────

def _members_file_path() -> Path:
    return Path(Config.MEMBERS_FILE)


def read_members_file_lines() -> list[str]:
    path = _members_file_path()
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _write_members_file_lines(lines: list[str]) -> None:
    path = _members_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")


def _line_matches(line: str, username: str) -> bool:
    """
    True if a members.txt line refers to `username`.

    Only plain entries and lines we wrote ourselves as `# STOPPED: <username>`
    match; arbitrary human comments are ignored.
    """
    stripped = line.strip()
    if stripped.startswith(STOPPED_MARKER):
        remainder = stripped[len(STOPPED_MARKER):]
    elif stripped.startswith("#"):
        return False
    else:
        remainder = stripped
    token = remainder.strip().split()[0] if remainder.strip() else ""
    return normalize_username(token) == username


def set_in_members_file(username: str, present: bool, keep_marker: bool = True) -> bool:
    """
    Add / comment-out / delete a member entry in MEMBERS_FILE.

    present=True  → entry written as plain `username`
    present=False → entry written as `# STOPPED: username` (keep_marker=True)
                    or removed entirely (keep_marker=False)

    Returns True if the file content changed.
    """
    lines = read_members_file_lines()
    new_lines: list[str] = []
    handled = False

    for line in lines:
        if _line_matches(line, username):
            if handled:
                continue  # drop duplicates
            handled = True
            if present:
                new_lines.append(username)
            elif keep_marker:
                new_lines.append(f"{STOPPED_MARKER} {username}")
            # keep_marker=False → line dropped entirely
        else:
            new_lines.append(line)

    if present and not handled:
        new_lines.append(username)

    if new_lines == lines:
        return False

    _write_members_file_lines(new_lines)
    return True


def members_file_stopped_entries() -> list[str]:
    """Return usernames currently marked `# STOPPED:` in members.txt."""
    out: list[str] = []
    for line in read_members_file_lines():
        stripped = line.strip()
        if not stripped.startswith(STOPPED_MARKER):
            continue
        parts = stripped[len(STOPPED_MARKER):].strip().split()
        username = normalize_username(parts[0]) if parts else ""
        if username:
            out.append(username)
    return out
# ─── Membaca & menyusun data member ─────────────────────────────────────────

def _compose_row(raw: dict, whitelist: set[str], sessions: dict[str, dict]) -> dict:
    """Gabungkan row member_hls + info whitelist/members.txt + sesi terakhir."""
    username = (raw.get("username") or "").lower()
    session = sessions.get(username) or {}
    hls_url = raw.get("hls_url") or ""
    return {
        "username": username,
        "display_name": raw.get("display_name") or username,
        "enabled": bool(raw.get("enabled", 1)),
        "hls_url": hls_url,
        "channel_id": channel_id_from_hls(hls_url),
        "hls_confirmed": bool(raw.get("hls_confirmed")),
        "in_db": True,
        "in_whitelist": username in whitelist,
        "in_members_file": username in whitelist,
        "last_live_at": raw.get("last_live_at") or "",
        "session_status": session.get("status") or "",
        "session_at": session.get("download_ended_at")
        or session.get("created_at")
        or "",
    }


def list_members(mode: str = "all") -> list[dict]:
    """
    Return member rows (member_hls + members.txt status) filtered by mode:

      all     → semua member di database + yang hanya ada di members.txt
      active  → sedang dipantau (enabled = 1)
      stopped → di-stop (enabled = 0)
      live    → punya sesi rekam/upload yang belum selesai
      unknown → aktif tapi HLS URL belum diketahui (menunggu discovery)
    """
    mode = (mode or "all").lower()
    if mode not in LIST_MODES:
        mode = "all"

    _ensure_schema()
    whitelist = Config.load_members()
    sessions = database.get_latest_sessions_by_member()

    rows = [
        _compose_row(r, whitelist, sessions)
        for r in database.get_all_member_hls(include_disabled=True)
    ]

    # Member yang ada di members.txt tapi belum terdaftar di DB
    known = {r["username"] for r in rows}
    for username in sorted(whitelist - known):
        rows.append({
            "username": username,
            "display_name": username,
            "enabled": True,
            "hls_url": "",
            "channel_id": "",
            "hls_confirmed": False,
            "in_db": False,
            "in_whitelist": True,
            "in_members_file": True,
            "last_live_at": "",
            "session_status": "",
            "session_at": "",
        })

    # Member yang sudah di-stop dan belum pernah masuk DB (hanya tersisa sebagai
    # `# STOPPED: username` di members.txt) tetap harus bisa di-/resume.
    stopped_entries = set(members_file_stopped_entries())
    known = {r["username"] for r in rows}
    for username in sorted(stopped_entries - known):
        rows.append({
            "username": username,
            "display_name": username,
            "enabled": False,
            "hls_url": "",
            "channel_id": "",
            "hls_confirmed": False,
            "in_db": False,
            "in_whitelist": False,
            "in_members_file": True,
            "last_live_at": "",
            "session_status": "",
            "session_at": "",
        })

    # Tandai baris yang tidak punya entri apa pun di members.txt
    for row in rows:
        row["in_members_file"] = row["in_whitelist"] or row["username"] in stopped_entries

    if mode == "active":
        rows = [r for r in rows if r["enabled"]]
    elif mode == "stopped":
        rows = [r for r in rows if not r["enabled"]]
    elif mode == "live":
        rows = [r for r in rows if r["session_status"] in LIVE_STATUSES]
    elif mode == "unknown":
        rows = [r for r in rows if r["enabled"] and not r["hls_url"]]

    rows.sort(key=lambda r: (not r["enabled"], r["username"]))
    return rows


def get_member_row(username: str) -> Optional[dict]:
    """Return a single composed row, or None if unknown."""
    username = normalize_username(username)
    for row in list_members("all"):
        if row["username"] == username:
            return row
    return None


def resolve_member_token(token: str) -> tuple[Optional[dict], str]:
    """
    Resolve a shorthand token (mis. 'lulu') into a member row.

    Returns (row, error_message). Only one of them is set.
    """
    normalized = normalize_username(token)
    if not normalized:
        return None, "Username kosong."

    rows = list_members("all")
    exact = [r for r in rows if r["username"] == normalized]
    if not exact:
        suffix = [r for r in rows if r["username"].endswith(normalized)]
        if len(suffix) == 1:
            exact = suffix
        elif len(suffix) > 1:
            names = ", ".join(sorted(r["username"] for r in suffix))
            return None, f"Ambigu: '{token}' cocok dengan beberapa member → {names}"
        else:
            return None, f"Member '{normalized}' tidak ditemukan di database."

    return exact[0], ""
# ─ Formatting untuk CLI & Telegram ────────────────────────────────────────

def status_label(row: dict) -> str:
    """Emoji + label status monitoring."""
    if row["enabled"]:
        return "✅ AKTIF" if row["hls_url"] else " AKTIF (HLS belum diketahui)"
    return " STOP"


def format_member_line(row: dict) -> str:
    """Satu baris ringkas untuk daftar member."""
    parts = [
        status_label(row),
        row["username"],
        f"— {row['display_name']}",
        "| HLS " + (f"✔ {row['channel_id']}" if row["hls_url"] else "✖"),
    ]
    if row["session_status"]:
        parts.append(f"| {row['session_status']} ({fmt_ago(row['session_at'])})")
    else:
        parts.append(f"| live terakhir: {fmt_ago(row['last_live_at'])}")
    if not row["in_members_file"]:
        parts.append("| ⚠ tidak ada di members.txt")
    return " ".join(parts)


def format_member_list(rows: list[dict], mode: str = "all") -> str:
    """Header + daftar member siap kirim (CLI / Telegram)."""
    label = {
        "all": "Semua member",
        "active": "Member aktif dipantau",
        "stopped": "Member di-stop",
        "live": "Member dengan sesi berjalan",
        "unknown": "Member aktif tanpa HLS",
    }.get(mode, mode)

    if not rows:
        return f"📋 {label}: (kosong)\n\nTidak ada data untuk filter '{mode}'."

    lines = [f"📋 {label} — {len(rows)} member", ""]
    lines.extend(format_member_line(r) for r in rows)
    return "\n".join(lines)


def format_member_detail(row: dict) -> str:
    """Detail lengkap 1 member."""
    members_file_state = (
        "ada (aktif)"
        if row["in_whitelist"]
        else ("tercatat STOP" if row["in_members_file"] else "tidak ada")
    )
    lines = [
        f"👤 {row['display_name']} ({row['username']})",
        f"Status     : {status_label(row)}",
        f"members.txt: {members_file_state}",
        f"Database   : {'ada' if row['in_db'] else 'belum (didaftarkan saat sync berikutnya)'}",
        "HLS URL    : " + (row["hls_url"] or "(belum diketahui — auto-discovery saat live)"),
    ]
    if row["channel_id"]:
        lines.append(f"Channel ID : {row['channel_id']}")
    lines.append(
        "Confirmed  : " + ("ya (tidak di-probe IDN lagi)" if row["hls_confirmed"] else "belum")
    )
    lines.append(f"Live terakhir: {fmt_ago(row['last_live_at'])}")
    if row["session_status"]:
        lines.append(
            f"Sesi terakhir: {row['session_status']} ({fmt_ago(row['session_at'])})"
        )
    return "\n".join(lines)


def format_status_summary(rows: list[dict]) -> str:
    """Ringkasan jumlah member per kondisi."""
    total = len(rows)
    active = sum(1 for r in rows if r["enabled"])
    stopped = total - active
    with_hls = sum(1 for r in rows if r["hls_url"])
    live = sum(1 for r in rows if r["session_status"] in LIVE_STATUSES)
    whitelist_only = sum(1 for r in rows if not r["in_whitelist"])
    return (
        f"📊 Total member: {total}\n"
        f"✅ Aktif: {active}    Stop: {stopped}\n"
        f" HLS diketahui: {with_hls}/{total}\n"
        f"🎙 Sesi berjalan: {live}\n"
        f"⚠️ Tanpa entri members.txt: {whitelist_only}"
    )
# ─── Aksi: add / stop / resume / remove / set HLS ──────────────────────────

def _result(ok: bool, action: str, username: str, lines: list[str], **extra) -> dict:
    out = {
        "ok": ok,
        "action": action,
        "username": username,
        "message": "\n".join(lines),
    }
    out.update(extra)
    return out


def add_member(
    raw: str,
    display_name: Optional[str] = None,
    hls_url: Optional[str] = None,
) -> dict:
    """
    Daftarkan / aktifkan kembali member agar direkam.

    hls_url boleh dikosongkan — bot akan auto-discovery HLS-nya saat member live
    (atau pakai `set_member_hls` bila channel ID AWS IVS sudah diketahui).
    """
    username = normalize_username(raw)
    error = validate_username(username)
    if error:
        return _result(False, "add", username, [f"❌ {error}"])

    _ensure_schema()
    existing = database.get_member_hls(username)
    resolved_name = display_name or (existing or {}).get("display_name") or username
    new_hls = (hls_url or "").strip() or None

    database.upsert_member_hls(
        username,
        resolved_name,
        hls_url=new_hls,
        confirmed=bool(new_hls),
    )
    database.set_member_enabled(username, True)
    file_changed = set_in_members_file(username, present=True)
    row = get_member_row(username) or {}

    lines = [f"✅ <b>{username}</b> sekarang AKTIF dipantau."]
    lines.append(
        "🆕 Member baru didaftarkan."
        if not existing
        else "♻️ Member lama diaktifkan kembali."
    )
    if new_hls:
        lines.append(f"📡 HLS manual dipakai (channel {channel_id_from_hls(new_hls) or '?'}):")
        lines.append(new_hls)
    elif row.get("hls_url"):
        lines.append(f"📡 HLS sudah tersimpan di database (channel {row.get('channel_id') or '?'}).")
    else:
        lines.append("🔎 HLS belum diketahui → auto-discovery (≈2 menit) saat member live.")
    if file_changed:
        lines.append(f"📝 {Config.MEMBERS_FILE} diperbarui.")
    lines.append("⏱ Berlaku pada siklus poll berikutnya (±15 detik), tanpa restart.")
    return _result(True, "add", username, lines, display_name=resolved_name, created=not existing)


def stop_member(raw: str, keep_marker: bool = True) -> dict:
    """
    Hentikan rekaman member: set enabled = 0 dan tandai members.txt.

    HLS URL tetap disimpan sehingga `resume_member` langsung aktif.
    Rekaman yang SEDANG berjalan tidak dibatalkan di sini — pemanggil
    (main.py lewat admin bot) yang membatalkan proses yt-dlp-nya.
    """
    row, error = resolve_member_token(raw)
    username = row["username"] if row else normalize_username(raw)
    if row is None:
        return _result(False, "stop", username, [f"❌ {error}"])

    if not row["enabled"]:
        return _result(
            True, "stop", username,
            [f"ℹ️ <b>{username}</b> sudah dalam status STOP."],
            was_enabled=False,
        )

    db_updated = database.set_member_enabled(username, False)
    file_changed = set_in_members_file(username, present=False, keep_marker=keep_marker)

    lines = [f"⏸ <b>{username}</b> di-STOP. Tidak akan direkam lagi."]
    if not db_updated:
        lines.append("ℹ️ Belum ada di tabel member_hls (hanya dicatat di members.txt).")
    if row["hls_url"]:
        lines.append(f"📡 HLS (channel {row['channel_id'] or '?'}) tetap disimpan → /resume instan.")
    if file_changed:
        lines.append(f"📝 {Config.MEMBERS_FILE} diperbarui.")
    lines.append("⏱ Berlaku pada siklus poll berikutnya (±15 detik).")
    return _result(True, "stop", username, lines, was_enabled=True, had_hls=bool(row["hls_url"]))
def resume_member(raw: str) -> dict:
    """Aktifkan kembali member yang sebelumnya di-stop."""
    # Dukung nama pendek (mis. 'official'): pakai username kanonik kalau ketemu,
    # supaya resume tidak membuat member baru.
    resolved_row, _err = resolve_member_token(raw)
    if resolved_row is not None:
        username = resolved_row["username"]
    else:
        username = normalize_username(raw)

    error = validate_username(username)
    if error:
        return _result(False, "resume", username, [f"❌ {error}"])

    row = get_member_row(username)
    if row and row["enabled"]:
        return _result(
            True, "resume", username,
            [f"ℹ️ <b>{username}</b> sudah AKTIF."],
            was_enabled=True,
        )

    had_hls = bool(row and row["hls_url"])
    result = add_member(username)
    if not result["ok"]:
        return result

    lines = [f"▶️ <b>{username}</b> diaktifkan kembali."]
    if had_hls:
        lines.append("📡 HLS URL lama dipakai → langsung siap rekam.")
    else:
        lines.append("🔎 HLS belum diketahui → bot akan discovery saat member live.")
    lines.append("⏱ Berlaku pada siklus poll berikutnya (±15 detik).")
    return _result(True, "resume", username, lines, had_hls=had_hls)


def remove_member(raw: str) -> dict:
    """Hapus member sepenuhnya: baris member_hls + entri members.txt."""
    row, error = resolve_member_token(raw)
    username = row["username"] if row else normalize_username(raw)
    if row is None:
        return _result(False, "remove", username, [f"❌ {error}"])

    deleted = database.delete_member_hls(username)
    file_changed = set_in_members_file(username, present=False, keep_marker=False)

    lines = [f"🗑 <b>{username}</b> dihapus dari pemantauan."]
    if deleted:
        lines.append("✅ Baris member_hls dihapus (HLS URL ikut hilang).")
    if file_changed:
        lines.append(f"📝 Entri di {Config.MEMBERS_FILE} dihapus.")
    if row["session_status"] in LIVE_STATUSES:
        lines.append("⚠️ Masih ada sesi berjalan — rekamannya tetap diupload setelah merge window.")
    lines.append("ℹ️ Pakai /add kalau ingin memantau lagi (HLS akan di-discovery ulang).")
    return _result(True, "remove", username, lines, deleted_row=deleted)


def set_member_hls(
    raw: str,
    hls_url: str,
    display_name: Optional[str] = None,
) -> dict:
    """
    Set/update HLS URL member secara manual (tanpa menunggu auto-discovery).

    Format: https://<dist>.playback.live-video.net/api/video/v1/
            <region>.<account>.channel.<CHANNEL_ID>.m3u8
    """
    username = normalize_username(raw)
    error = validate_username(username)
    if error:
        return _result(False, "set_hls", username, [f"❌ {error}"])

    url = (hls_url or "").strip()
    if not url:
        return _result(False, "set_hls", username, ["❌ HLS URL kosong."])
    if not url.lower().startswith("http") or ".m3u8" not in url.lower():
        return _result(
            False, "set_hls", username,
            ["❌ URL tidak valid. Harus URL playback AWS IVS (.m3u8)."],
        )

    _ensure_schema()
    existing = database.get_member_hls(username)
    resolved_name = display_name or (existing or {}).get("display_name") or username
    database.upsert_member_hls(username, resolved_name, hls_url=url, confirmed=True)
    database.set_member_enabled(username, True)
    file_changed = set_in_members_file(username, present=True)

    lines = [
        f" HLS untuk <b>{username}</b> disimpan (confirmed).",
        f"Channel ID: {channel_id_from_hls(url) or '?'}",
    ]
    if file_changed:
        lines.append(f"📝 {Config.MEMBERS_FILE} diperbarui.")
    lines.append("✅ Member aktif & siap direkam pada siklus berikutnya.")
    return _result(True, "set_hls", username, lines)