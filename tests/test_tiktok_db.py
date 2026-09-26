"""
test_tiktok_db.py - Unit test pelacak percobaan unduh & retry 'failed' TikTok.

Mencakup:
1. Migrasi kolom `download_attempts` / `last_attempt_at` (idempoten, termasuk
   pada tabel tiktok_posts lama yang belum punya kolom tersebut).
2. `increment_tiktok_download_attempt()` menaikkan penghitung + stempel waktu.
3. `get_tiktok_retryable_failed()` menyaring status/percobaan/umur percobaan.
4. `update_sessions_by_merge_group()` memperbarui SEMUA segmen grup dalam
   SATU transaksi (semua baris mendapat status & kolom yang sama).
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot import database
from bot.config import Config


class TikTokDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self._saved_db = Config.DB_PATH
        Config.DB_PATH = str(self.base / "tiktok.db")
        database.init_db()

    def tearDown(self):
        Config.DB_PATH = self._saved_db
        self._tmp.cleanup()

    def _insert(self, post_id: str, **extra) -> None:
        database.insert_tiktok_post(
            {
                "id": post_id,
                "unique_id": extra.pop("unique_id", "indahjkt48"),
                "kind": "video",
                "is_story": extra.pop("is_story", False),
                "title": f"video {post_id}",
                "created_at": extra.pop("created_at", "2026-09-20T00:00:00+00:00"),
                "source_url": f"https://tiktok.invalid/{post_id}",
            }
        )
        if extra:
            database.update_tiktok_post(post_id, **extra)

    def _columns(self, table: str) -> list[str]:
        with database._get_conn() as conn:
            return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


class TestMigration(TikTokDbTestCase):
    def test_new_columns_exist_and_defaults(self):
        cols = self._columns("tiktok_posts")
        self.assertIn("download_attempts", cols)
        self.assertIn("last_attempt_at", cols)
        self._insert("m1", status="done")
        row = database.get_tiktok_post("m1")
        self.assertEqual(row["download_attempts"], 0)
        self.assertEqual(row["last_attempt_at"], "")

    def test_init_db_idempotent(self):
        # Menjalankan ulang init_db tidak boleh gagal/menggandakan kolom.
        database.init_db()
        database.init_db()
        self.assertEqual(self._columns("tiktok_posts").count("download_attempts"), 1)
        self.assertEqual(self._columns("tiktok_posts").count("last_attempt_at"), 1)

    def test_legacy_table_migrated(self):
        # Simulasi database produksi LAMA: tabel tanpa kolom percobaan unduh.
        conn = sqlite3.connect(Config.DB_PATH)
        try:
            conn.execute("DROP TABLE tiktok_posts")
            conn.execute(
                """CREATE TABLE tiktok_posts (
                    id TEXT PRIMARY KEY,
                    unique_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'video',
                    status TEXT NOT NULL DEFAULT 'detected',
                    error_message TEXT,
                    added_at TEXT DEFAULT (datetime('now'))
                )"""
            )
            conn.execute(
                "INSERT INTO tiktok_posts (id, unique_id, status) VALUES ('old1', 'indahjkt48', 'failed')"
            )
            conn.commit()
        finally:
            conn.close()
        database.init_db()
        row = database.get_tiktok_post("old1")
        self.assertEqual(row["download_attempts"], 0)
        self.assertEqual(row["last_attempt_at"], "")


class TestDownloadAttempts(TikTokDbTestCase):
    def test_increment_counts_and_stamps(self):
        self._insert("a1", status="failed")
        self.assertEqual(database.increment_tiktok_download_attempt("a1"), 1)
        self.assertEqual(database.increment_tiktok_download_attempt("a1"), 2)
        row = database.get_tiktok_post("a1")
        self.assertEqual(row["download_attempts"], 2)
        self.assertTrue(row["last_attempt_at"])

    def test_increment_unknown_post_returns_zero(self):
        self.assertEqual(database.increment_tiktok_download_attempt("tidak-ada"), 0)


class TestRetryableFailed(TikTokDbTestCase):
    def test_failed_below_attempt_cap_with_old_attempt_is_retryable(self):
        self._insert("r1", status="failed", download_attempts=2, last_attempt_at="2000-01-01 00:00:00")
        rows = database.get_tiktok_retryable_failed(limit=10, max_attempts=6, min_age_seconds=900)
        self.assertEqual([r["id"] for r in rows], ["r1"])

    def test_exhausted_attempts_not_retryable(self):
        self._insert("r1", status="failed", download_attempts=6, last_attempt_at="2000-01-01 00:00:00")
        rows = database.get_tiktok_retryable_failed(limit=10, max_attempts=6, min_age_seconds=0)
        self.assertEqual(rows, [])

    def test_recent_attempt_waits_for_cooldown(self):
        # last_attempt_at = sekarang (lewat increment) → min_age belum lewat.
        self._insert("r1", status="failed", download_attempts=1)
        database.increment_tiktok_download_attempt("r1")
        rows = database.get_tiktok_retryable_failed(limit=10, max_attempts=6, min_age_seconds=900)
        self.assertEqual(rows, [])
        # Tanpa syarat umur, baris yang sama langsung diambil.
        rows = database.get_tiktok_retryable_failed(limit=10, max_attempts=6, min_age_seconds=0)
        self.assertEqual([r["id"] for r in rows], ["r1"])

    def test_non_failed_status_and_limit(self):
        self._insert("r1", status="done", download_attempts=0)
        self._insert("r2", status="failed")
        self._insert("r3", status="failed")
        rows = database.get_tiktok_retryable_failed(limit=1, max_attempts=6, min_age_seconds=0)
        self.assertEqual([r["id"] for r in rows], ["r2"])  # terlama dulu, hormati limit


class TestUpdateSessionsByMergeGroup(TikTokDbTestCase):
    def test_all_segments_updated_in_one_call(self):
        gid = database.create_merge_group("indahjkt48", "Indah", "2026-09-20T00:00:00")
        for live_id in ("l1", "l2", "l3"):
            database.insert_live(live_id, "indahjkt48")
            database.set_session_merge_group(live_id, gid)

        database.update_sessions_by_merge_group(
            gid, "pending_upload", error_message="kuota habis"
        )
        for live_id in ("l1", "l2", "l3"):
            row = database.get_session(live_id)
            self.assertEqual(row["status"], "pending_upload")
            self.assertEqual(row["error_message"], "kuota habis")


if __name__ == "__main__":
    unittest.main()
