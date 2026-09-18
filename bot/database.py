"""
database.py - SQLite persistence layer for JKT48 Live Bot.
Tracks all live sessions so we never double-download/send.
"""
import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional, Generator
from bot.config import Config

logger = logging.getLogger(__name__)

CREATE_LIVE_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS live_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    live_id             TEXT    UNIQUE NOT NULL,
    member_username     TEXT    NOT NULL,
    member_name         TEXT,
    started_at          TEXT,
    hls_url             TEXT,
    download_started_at TEXT,
    download_ended_at   TEXT,
    file_path           TEXT,
    file_size_bytes     INTEGER,
    status              TEXT    NOT NULL DEFAULT 'detected',
    telegram_message_id INTEGER,
    youtube_video_id    TEXT,
    error_message       TEXT,
    merge_group_id      INTEGER,
    created_at          TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_MERGE_GROUPS_SQL = """
CREATE TABLE IF NOT EXISTS merge_groups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    member_username     TEXT    NOT NULL,
    member_name         TEXT,
    started_at          TEXT,
    thumbnail_url       TEXT,
    live_title          TEXT,
    status              TEXT    NOT NULL DEFAULT 'waiting',
    merged_file_path    TEXT,
    merged_live_id      TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_MEMBER_HLS_SQL = """
CREATE TABLE IF NOT EXISTS member_hls (
    username      TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL DEFAULT '',
    hls_url       TEXT,
    hls_confirmed INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    added_at      TEXT DEFAULT (datetime('now')),
    last_live_at  TEXT
);
"""

CREATE_YOUTUBE_CHANNELS_SQL = """
CREATE TABLE IF NOT EXISTS youtube_channels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_label TEXT NOT NULL,
    token_file    TEXT NOT NULL UNIQUE,
    secret_file   TEXT NOT NULL,
    uploads_today INTEGER DEFAULT 0,
    last_reset_at TEXT DEFAULT (datetime('now'))
);
"""

# Possible values for `status` column:
# 'detected'           → live stream detected, not yet downloading
# 'downloading'        → yt-dlp/ffmpeg is currently recording
# 'segment_done'       → recorded segment, waiting in merge window
# 'download_complete'  → ready to upload
# 'merging'            → merging segments
# 'uploading_youtube'  → being uploaded to YouTube
# 'done_youtube'       → successfully uploaded to YouTube
# 'pending_upload'     → saved locally, waiting in queue (e.g. YouTube quota hit)
# 'failed'             → permanent failure


@contextmanager
def _get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(Config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create/upgrade tables if needed."""
    with _get_conn() as conn:
        conn.execute(CREATE_LIVE_SESSIONS_SQL)
        conn.execute(CREATE_MERGE_GROUPS_SQL)
        conn.execute(CREATE_MEMBER_HLS_SQL)
        conn.execute(CREATE_YOUTUBE_CHANNELS_SQL)

        # Migrate live_sessions columns if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(live_sessions)")]
        if "merge_group_id" not in cols:
            conn.execute("ALTER TABLE live_sessions ADD COLUMN merge_group_id INTEGER")
            logger.info("Schema migration: added merge_group_id column")
        if "hls_url" not in cols:
            conn.execute("ALTER TABLE live_sessions ADD COLUMN hls_url TEXT")
            logger.info("Schema migration: added hls_url column")
        if "live_slug" not in cols:
            conn.execute("ALTER TABLE live_sessions ADD COLUMN live_slug TEXT")
            logger.info("Schema migration: added live_slug column to live_sessions")

        # Migrate merge_groups columns if missing
        mg_cols = [r[1] for r in conn.execute("PRAGMA table_info(merge_groups)")]
        if "live_title" not in mg_cols:
            conn.execute("ALTER TABLE merge_groups ADD COLUMN live_title TEXT")
            logger.info("Schema migration: added live_title column")
        if "last_segment_at" not in mg_cols:
            conn.execute("ALTER TABLE merge_groups ADD COLUMN last_segment_at TEXT")
            logger.info("Schema migration: added last_segment_at column")
        if "live_slug" not in mg_cols:
            conn.execute("ALTER TABLE merge_groups ADD COLUMN live_slug TEXT")
            logger.info("Schema migration: added live_slug column to merge_groups")
        if "live_key" not in mg_cols:
            conn.execute("ALTER TABLE merge_groups ADD COLUMN live_key TEXT")
            logger.info("Schema migration: added live_key column to merge_groups")

        # Migrate member_hls columns if missing
        mh_cols = [r[1] for r in conn.execute("PRAGMA table_info(member_hls)")]
        if "hls_confirmed" not in mh_cols:
            conn.execute("ALTER TABLE member_hls ADD COLUMN hls_confirmed INTEGER NOT NULL DEFAULT 0")
            logger.info("Schema migration: added hls_confirmed column")
        if "enabled" not in mh_cols:
            conn.execute("ALTER TABLE member_hls ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            logger.info("Schema migration: added enabled column to member_hls")
        if "showroom_room_id" not in mh_cols:
            conn.execute("ALTER TABLE member_hls ADD COLUMN showroom_room_id TEXT")
            logger.info("Schema migration: added showroom_room_id column to member_hls")
        if "showroom_name" not in mh_cols:
            conn.execute("ALTER TABLE member_hls ADD COLUMN showroom_name TEXT")
            logger.info("Schema migration: added showroom_name column to member_hls")
        if "showroom_enabled" not in mh_cols:
            conn.execute("ALTER TABLE member_hls ADD COLUMN showroom_enabled INTEGER NOT NULL DEFAULT 1")
            logger.info("Schema migration: added showroom_enabled column to member_hls")
        if "showroom_only" not in mh_cols:
            # 1 = member hanya ada di Showroom (tidak punya akun IDN). Baris seperti ini
            # TIDAK boleh ikut IDN GraphQL discovery, kalau tidak bot akan mencoba
            # selamanya untuk username yang memang tidak ada di IDN.
            conn.execute("ALTER TABLE member_hls ADD COLUMN showroom_only INTEGER NOT NULL DEFAULT 0")
            logger.info("Schema migration: added showroom_only column to member_hls")

        # Platform marker: baris lama otomatis 'idn' (memang sesuai kenyataan).
        if "platform" not in cols:
            conn.execute("ALTER TABLE live_sessions ADD COLUMN platform TEXT NOT NULL DEFAULT 'idn'")
            logger.info("Schema migration: added platform column to live_sessions")
        if "platform" not in mg_cols:
            conn.execute("ALTER TABLE merge_groups ADD COLUMN platform TEXT NOT NULL DEFAULT 'idn'")
            logger.info("Schema migration: added platform column to merge_groups")

    logger.info("Database initialised at %s", Config.DB_PATH)


def is_live_known(live_id: str) -> bool:
    """Return True if we've already seen this live_id."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM live_sessions WHERE live_id = ?", (live_id,)
        ).fetchone()
    return row is not None


def get_live_status(live_id: str) -> Optional[str]:
    """Return the current status of a live_id, or None if not found."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM live_sessions WHERE live_id = ?", (live_id,)
        ).fetchone()
    return row["status"] if row else None


def count_reconnect_segments(base_live_id: str) -> int:
    """Count all segments (original + reconnect) for this base live_id."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE live_id = ? OR live_id LIKE ? OR live_id LIKE ?",
            (base_live_id, f"{base_live_id}_pt%", f"{base_live_id}_rc%"),
        ).fetchone()
    return row[0] if row else 0


def count_live_segments(base_live_id: str) -> int:
    """Return how many segment sessions exist for a base_live_id (base_live_id or base_live_id_pt%)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE live_id = ? OR live_id LIKE ?",
            (base_live_id, f"{base_live_id}_pt%"),
        ).fetchone()
        return row[0] if row else 0


def count_failed_segments(base_live_id: str) -> int:
    """Return how many failed segment sessions exist for a base_live_id."""
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM live_sessions
               WHERE (live_id = ? OR live_id LIKE ?) AND status = 'failed'""",
            (base_live_id, f"{base_live_id}_pt%"),
        ).fetchone()
        return row[0] if row else 0


def get_latest_terminal_session_by_hls_url(hls_url: str) -> Optional[dict]:
    """
    Find the most recent session with this HLS URL that has finished (terminal status).
    Used to detect member reconnects: the HLS URL of an AWS IVS channel never changes,
    so if the same URL appears again after a session is done/failed → reconnect.
    Returns None if no terminal session found for this URL.
    """
    terminal = ("done_telegram", "done_youtube", "failed")
    placeholders = ",".join("?" * len(terminal))
    with _get_conn() as conn:
        row = conn.execute(
            f"""SELECT * FROM live_sessions
               WHERE hls_url = ? AND status IN ({placeholders})
               ORDER BY created_at DESC LIMIT 1""",
            (hls_url, *terminal),
        ).fetchone()
    return dict(row) if row else None


def count_sessions_by_hls_url(hls_url: str) -> int:
    """Count all recorded sessions for this HLS URL (across all live IDs)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE hls_url = ?",
            (hls_url,),
        ).fetchone()
    return row[0] if row else 0


def insert_live(
    live_id: str,
    member_username: str,
    member_name: Optional[str] = None,
    started_at: Optional[str] = None,
    hls_url: Optional[str] = None,
    platform: str = "idn",
) -> None:
    """Insert a newly detected live session.

    `platform` menandai asal sesi ('idn' atau 'showroom'); default 'idn' agar
    pemanggil lama tidak perlu berubah.
    """
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO live_sessions
                (live_id, member_username, member_name, started_at, hls_url, platform, status)
            VALUES (?, ?, ?, ?, ?, ?, 'detected')
            """,
            (live_id, member_username, member_name, started_at, hls_url, platform),
        )


def update_status(live_id: str, status: str, **kwargs) -> None:
    """Update the status (and any extra columns) for a live session."""
    allowed_cols = {
        "file_path",
        "file_size_bytes",
        "telegram_message_id",
        "youtube_video_id",
        "download_started_at",
        "download_ended_at",
        "error_message",
    }
    extra = {k: v for k, v in kwargs.items() if k in allowed_cols}
    set_clause = ", ".join([f"{col} = ?" for col in extra])
    values = list(extra.values()) + [status, live_id]
    sql = f"UPDATE live_sessions SET {set_clause + ', ' if set_clause else ''}status = ? WHERE live_id = ?"
    with _get_conn() as conn:
        conn.execute(sql, values)


def get_session(live_id: str) -> Optional[sqlite3.Row]:
    """Fetch a single session row by live_id."""
    with _get_conn() as conn:
        return conn.execute(
            "SELECT * FROM live_sessions WHERE live_id = ?", (live_id,)
        ).fetchone()


def get_platform_for_live(live_id: str, merge_group_id: Optional[int] = None) -> str:
    """
    Platform untuk sebuah live, dipakai saat menyusun judul upload.

    Urutan: prefix live_id (`sr_` = Showroom) → kolom platform live_sessions →
    platform merge_groups (untuk live_id `merged_<gid>` yang tidak punya baris
    live_sessions) → default 'idn'.
    """
    if live_id.startswith("sr_"):
        return "showroom"
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT platform FROM live_sessions WHERE live_id = ? LIMIT 1",
            (live_id,),
        ).fetchone()
        if row and row["platform"]:
            return row["platform"]
        if merge_group_id is not None:
            grp = conn.execute(
                "SELECT platform FROM merge_groups WHERE id = ?",
                (merge_group_id,),
            ).fetchone()
            if grp and grp["platform"]:
                return grp["platform"]
    return "idn"


def get_pending_uploads() -> list:
    """Return sessions that finished downloading but not yet uploaded."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM live_sessions WHERE status = 'download_complete'"
        ).fetchall()
    return [dict(r) for r in rows]


def clean_interrupted_downloads() -> None:
    """
    Remove sessions that were interrupted by a restart (detected/downloading).
    This lets the bot re-detect and re-record them on the next poll.
    """
    with _get_conn() as conn:
        conn.execute(
            "DELETE FROM live_sessions WHERE status IN ('detected', 'downloading')"
        )


# ─── Merge Groups ──────────────────────────────────────────────────────────────

def create_merge_group(
    member_username: str,
    member_name: str,
    started_at: str,
    thumbnail_url: str = "",
    live_title: str = "",
    platform: str = "idn",
) -> int:
    """Create a new merge group and return its ID."""
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO merge_groups
               (member_username, member_name, started_at, thumbnail_url, live_title, platform, status)
               VALUES (?, ?, ?, ?, ?, ?, 'waiting')""",
            (member_username, member_name, started_at, thumbnail_url, live_title, platform),
        )
        return cur.lastrowid


def set_session_merge_group(live_id: str, group_id: int) -> None:
    """Assign a live session to a merge group."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE live_sessions SET merge_group_id = ? WHERE live_id = ?",
            (group_id, live_id),
        )


def get_merge_group(group_id: int) -> Optional[dict]:
    """Fetch a merge group by ID."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM merge_groups WHERE id = ?", (group_id,)
        ).fetchone()
    return dict(row) if row else None


def get_merge_group_timing(group_id: int) -> dict:
    """
    Umur grup & waktu idle dihitung DI DALAM SQL (UTC vs UTC).

    PENTING: kolom `created_at` / `last_segment_at` diisi SQLite memakai
    `datetime('now')` yaitu **UTC**, sedangkan Python memakai waktu lokal
    (WIB = UTC+7). Menghitung selisihnya di Python akan salah 7 jam, jadi
    perhitungan dilakukan di SQL agar konsisten.

    Return: {"age_seconds": float, "idle_seconds": float} (0.0 kalau grup tidak ada).
    """
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT
                 (julianday('now') - julianday(created_at)) * 86400.0 AS age_seconds,
                 (julianday('now') - julianday(COALESCE(last_segment_at, created_at))) * 86400.0
                   AS idle_seconds
               FROM merge_groups WHERE id = ?""",
            (group_id,),
        ).fetchone()
    if not row:
        return {"age_seconds": 0.0, "idle_seconds": 0.0}
    return {
        "age_seconds": max(0.0, float(row["age_seconds"] or 0.0)),
        "idle_seconds": max(0.0, float(row["idle_seconds"] or 0.0)),
    }


def get_active_merge_group(
    member_username: str,
    within_seconds: int = 600,
    platform: Optional[str] = None,
) -> Optional[dict]:
    """
    Return the latest 'waiting' merge group for this member if within time window.

    Window dihitung dari **segmen terakhir** (`last_segment_at`), bukan kapan grup
    dibuat. Penting: grup hasil reconnect panjang bisa berumur > 1 jam sejak dibuat
    padahal jeda dari segmen terakhirnya baru beberapa menit — itu tetap SATU live dan
    tidak boleh dipecah menjadi beberapa video.

    `platform` menyaring grup berdasarkan asal sesi. Ini penting untuk member yang
    punya IDN dan Showroom sekaligus: tanpa filter, segmen Showroom bisa masuk ke
    grup IDN yang sedang menunggu (atau sebaliknya) sehingga dua live berbeda
    tergabung menjadi satu video. None = perilaku lama (tanpa filter).
    """
    sql = """SELECT * FROM merge_groups
             WHERE member_username = ? AND status = 'waiting'
               AND (julianday('now') - julianday(COALESCE(last_segment_at, created_at))) * 86400 <= ?"""
    params: list = [member_username.strip().lower(), within_seconds]
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    sql += " ORDER BY id DESC LIMIT 1"

    with _get_conn() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def get_merge_segments(group_id: int) -> list[dict]:
    """Return all live_sessions assigned to a merge group."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM live_sessions
               WHERE merge_group_id = ? AND status IN ('segment_done', 'download_complete')
               ORDER BY download_started_at ASC""",
            (group_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_sessions_by_merge_group(group_id: int, status: str, **kwargs) -> None:
    """Update status and extra columns for all sessions belonging to a merge group."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT live_id FROM live_sessions WHERE merge_group_id = ?", (group_id,)
        ).fetchall()
    for r in rows:
        update_status(r["live_id"], status, **kwargs)


def close_merge_group(group_id: int, merged_file_path: str, merged_live_id: str) -> None:
    """Mark a merge group as done with the merged file path."""
    with _get_conn() as conn:
        conn.execute(
            """UPDATE merge_groups
               SET status = 'done', merged_file_path = ?, merged_live_id = ?
               WHERE id = ?""",
            (merged_file_path, merged_live_id, group_id),
        )


def fail_merge_group(group_id: int) -> None:
    """Mark a merge group as failed."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE merge_groups SET status = 'failed' WHERE id = ?",
            (group_id,),
        )


def get_waiting_merge_groups() -> list:
    """Return all merge groups still in 'waiting' status (oldest first).

    Used at startup to recover groups whose finalize timer was lost (e.g. after
    a restart or a previously-fixed bug where a failed reconnect cancelled the
    timer). Each group with at least one segment is re-scheduled for finalize.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM merge_groups WHERE status = 'waiting' ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_orphaned_segments() -> list[dict]:
    """Return sessions with status 'segment_done' that have no active waiting merge group."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT s.* FROM live_sessions s
               LEFT JOIN merge_groups g ON s.merge_group_id = g.id
               WHERE s.status IN ('segment_done', 'download_complete')
                 AND (s.merge_group_id IS NULL OR g.status != 'waiting')
               ORDER BY s.download_started_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def update_merge_group_last_segment(group_id: int) -> None:
    """Update last_segment_at timestamp for a merge group."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE merge_groups SET last_segment_at = datetime('now') WHERE id = ?",
            (group_id,),
        )


def update_merge_group_slug(
    group_id: int,
    live_slug: str,
    live_title: str = "",
    live_key: str = "",
) -> None:
    """
    Simpan slug + judul + **live_key** sesi live terakhir yang masuk ke grup ini.

    `live_slug` hanya untuk jejak/audit (slug IDN berubah saat reconnect!), sedangkan
    `live_key` (= judul live) adalah identitas sesi yang dipakai untuk memutuskan
    "reconnect" vs "live baru".
    """
    if not live_slug and not live_key:
        return
    with _get_conn() as conn:
        conn.execute(
            """UPDATE merge_groups
               SET live_slug  = CASE WHEN ? != '' THEN ? ELSE live_slug END,
                   live_key   = CASE WHEN ? != '' THEN ? ELSE live_key END,
                   live_title = CASE WHEN ? != '' THEN ? ELSE live_title END
               WHERE id = ?""",
            (
                live_slug, live_slug,
                live_key, live_key,
                live_title, live_title,
                group_id,
            ),
        )


def set_session_live_slug(live_id: str, live_slug: str) -> None:
    """Simpan slug sesi live (best-effort dari IDN) ke sebuah live_session."""
    if not live_slug:
        return
    with _get_conn() as conn:
        conn.execute(
            "UPDATE live_sessions SET live_slug = ? WHERE live_id = ?",
            (live_slug, live_id),
        )


def mark_session_failed(live_id: str, error_message: str) -> None:
    """Tandai satu sesi sebagai gagal (dipakai untuk segmen rusak/hilang)."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE live_sessions SET status = 'failed', error_message = ? WHERE live_id = ?",
            (error_message, live_id),
        )


# ─── Member HLS Operations ───────────────────────────────────────────────────

def register_members_if_not_exists(usernames: list[str]) -> None:
    """
    Ensure all usernames exist in member_hls table with NULL hls_url if not already present.
    """
    if not usernames:
        return
    with _get_conn() as conn:
        for u in usernames:
            conn.execute(
                """INSERT INTO member_hls (username, display_name, hls_url)
                   VALUES (?, ?, NULL)
                   ON CONFLICT(username) DO NOTHING""",
                (u.strip().lower(), u.strip().lower()),
            )


def upsert_member_hls(
    username: str,
    display_name: str,
    hls_url: Optional[str] = None,
    confirmed: bool = False,
) -> None:
    """Insert or update member HLS and display name.
    
    Set confirmed=True when the HLS URL is permanently known (e.g. from seed).
    Confirmed members are never re-probed via IDN GraphQL.
    """
    username = username.strip().lower()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO member_hls (username, display_name, hls_url, hls_confirmed)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE member_hls.display_name END,
                hls_url = COALESCE(excluded.hls_url, member_hls.hls_url),
                hls_confirmed = MAX(member_hls.hls_confirmed, excluded.hls_confirmed)
            """,
            (username, display_name or username, hls_url, 1 if confirmed else 0),
        )


def set_member_showroom(
    username: str,
    room_id: Optional[str],
    showroom_name: str = "",
    display_name: str = "",
    showroom_only: bool = False,
) -> None:
    """Simpan/ubah pasangan Showroom (room_id) untuk seorang member.

    Dipakai oleh bot.seed_showroom. TIDAK menyentuh kolom IDN (hls_url,
    hls_confirmed) maupun `enabled`, jadi aman dijalankan berulang.

    showroom_only=True menandai member yang hanya ada di Showroom supaya
    barisnya dilewati oleh IDN GraphQL discovery.

    `display_name` hanya DIISI bila masih kosong atau masih sama dengan
    username (placeholder dari register_members_if_not_exists). Nama yang
    sudah diisi manusia (mis. "Raisha JKT48") TIDAK ditimpa.
    """
    username = username.strip().lower()
    room_id = (room_id or "").strip() or None
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO member_hls
                (username, display_name, showroom_room_id, showroom_name, showroom_only)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                showroom_room_id = excluded.showroom_room_id,
                showroom_name = COALESCE(NULLIF(excluded.showroom_name, ''), member_hls.showroom_name),
                showroom_only = excluded.showroom_only,
                display_name = CASE
                    WHEN member_hls.display_name IS NULL
                      OR member_hls.display_name = ''
                      OR LOWER(member_hls.display_name) = LOWER(member_hls.username)
                    THEN COALESCE(NULLIF(excluded.display_name, ''), member_hls.display_name)
                    ELSE member_hls.display_name
                END
            """,
            (username, display_name or username, room_id, showroom_name, 1 if showroom_only else 0),
        )


def update_member_hls_url(username: str, hls_url: str, display_name: Optional[str] = None) -> None:
    """Update the HLS URL for a member once discovered."""
    username = username.strip().lower()
    with _get_conn() as conn:
        if display_name:
            conn.execute(
                "UPDATE member_hls SET hls_url = ?, display_name = ? WHERE username = ?",
                (hls_url, display_name, username),
            )
        else:
            conn.execute(
                "UPDATE member_hls SET hls_url = ? WHERE username = ?",
                (hls_url, username),
            )


def update_member_last_live(username: str) -> None:
    """Update the last_live_at timestamp for a member."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE member_hls SET last_live_at = datetime('now') WHERE username = ?",
            (username.strip().lower(),),
        )


def get_members_without_hls() -> list[dict]:
    """Return ENABLED members whose HLS URL is unknown and not yet confirmed.

    Members with hls_confirmed=1 are skipped — their channel is permanently
    known (seeded from history) and will be probed directly via HTTP.
    Only members with hls_confirmed=0 AND hls_url IS NULL need IDN GraphQL probing.

    Disabled members (enabled=0, i.e. stopped via CLI/admin bot) are excluded so
    a stopped channel is never re-discovered & recorded again.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM member_hls
               WHERE (hls_url IS NULL OR hls_url = '')
                 AND hls_confirmed = 0
                 AND enabled = 1
                 AND showroom_only = 0"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_members_with_showroom() -> list[dict]:
    """
    Member yang punya room Showroom DAN aktif dipantau di Showroom.

    `enabled = 1` menghormati stop/resume global (CLI/admin bot), sedangkan
    `showroom_enabled = 1` memungkinkan mematikan Showroom untuk satu member
    tanpa menghentikan pemantauan IDN-nya.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM member_hls
               WHERE showroom_room_id IS NOT NULL
                 AND showroom_room_id != ''
                 AND showroom_enabled = 1
                 AND enabled = 1
               ORDER BY username ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_member_showroom_room_id(username: str) -> str:
    """room_id Showroom milik satu member ('' bila tidak ada)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT showroom_room_id FROM member_hls WHERE LOWER(username) = ?",
            (username.strip().lower(),),
        ).fetchone()
    return (row["showroom_room_id"] if row and row["showroom_room_id"] else "") or ""


def set_member_showroom_enabled(username: str, enabled: bool) -> bool:
    """Aktifkan/nonaktifkan pemantauan Showroom untuk satu member."""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE member_hls SET showroom_enabled = ? WHERE LOWER(username) = ?",
            (1 if enabled else 0, username.strip().lower()),
        )
        return cur.rowcount > 0


def get_all_member_hls(include_disabled: bool = True) -> list[dict]:
    """Return all members in the database.

    include_disabled=False returns only members that are actively monitored
    (enabled = 1) — this is what the recording loop uses.
    """
    sql = "SELECT * FROM member_hls"
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY username ASC"
    with _get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def set_member_enabled(username: str, enabled: bool) -> bool:
    """
    Enable/disable monitoring for a member without deleting its HLS record.
    Returns True if a member row existed and was updated.
    """
    username = username.strip().lower()
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE member_hls SET enabled = ? WHERE username = ?",
            (1 if enabled else 0, username),
        )
        return cur.rowcount > 0


def delete_member_hls(username: str) -> bool:
    """Permanently remove a member row. Returns True if a row was deleted."""
    username = username.strip().lower()
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM member_hls WHERE username = ?", (username,))
        return cur.rowcount > 0


def get_latest_sessions_by_member() -> dict[str, dict]:
    """
    Return the most recent live_session row per member_username.
    Used by the member list/report helpers to show the last known status.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.* FROM live_sessions s
            JOIN (
                SELECT member_username, MAX(created_at) AS max_created
                FROM live_sessions
                GROUP BY member_username
            ) latest
              ON s.member_username = latest.member_username
             AND s.created_at = latest.max_created
            """
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["member_username"].lower()] = dict(r)
    return out


def get_member_hls(username: str) -> Optional[dict]:
    """Fetch single member row by username."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM member_hls WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


# ─── YouTube Channel Pool Operations ──────────────────────────────────────────

def sync_youtube_channels(channels: list[dict]) -> None:
    """
    Sync configured channels from config/env into database.
    channels: list of dicts with keys (channel_label, token_file, secret_file)
    """
    with _get_conn() as conn:
        for ch in channels:
            conn.execute(
                """
                INSERT INTO youtube_channels (channel_label, token_file, secret_file)
                VALUES (:channel_label, :token_file, :secret_file)
                ON CONFLICT(token_file) DO UPDATE SET
                    channel_label = excluded.channel_label,
                    secret_file = excluded.secret_file
                """,
                ch,
            )


def reset_channel_counters_if_new_day() -> None:
    """
    Reset uploads_today to 0 if the last reset date is before today (UTC).
    """
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE youtube_channels
            SET uploads_today = 0, last_reset_at = datetime('now')
            WHERE date(last_reset_at) < date('now')
            """
        )


def get_youtube_channels() -> list[dict]:
    """Return all configured YouTube channels."""
    reset_channel_counters_if_new_day()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM youtube_channels ORDER BY uploads_today ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_next_youtube_channel() -> Optional[dict]:
    """
    Get the next available YouTube channel with the lowest uploads_today.
    """
    reset_channel_counters_if_new_day()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM youtube_channels ORDER BY uploads_today ASC, id ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def increment_channel_uploads(channel_id: int) -> None:
    """Increment uploads_today count for a channel."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE youtube_channels SET uploads_today = uploads_today + 1 WHERE id = ?",
            (channel_id,),
        )


# ─── Pending Upload Queue ────────────────────────────────────────────────────

def get_pending_uploads_youtube() -> list[dict]:
    """Return sessions marked as pending_upload (e.g. waiting for quota reset)."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM live_sessions WHERE status = 'pending_upload' ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_pending_videos() -> list[dict]:
    """
    Return all sessions that have a recorded file on disk ready to upload.
    Only returns 'pending_upload' and 'download_complete' — not 'failed',
    since failed means permanently failed and should not be auto-retried.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM live_sessions
               WHERE status IN ('pending_upload', 'download_complete')
                 AND file_path IS NOT NULL AND file_path != ''
               ORDER BY created_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def mark_session_pending_upload(live_id: str, error_message: str = "") -> None:
    """Mark a session as pending_upload so it stays on disk and queues for retry."""
    with _get_conn() as conn:
        conn.execute(
            """UPDATE live_sessions
               SET status = 'pending_upload', error_message = ?
               WHERE live_id = ?""",
            (error_message, live_id),
        )

