"""
test_disk_reclaim.py - Reclaim disk dari rekaman yang tidak akan pernah upload.

Rantai kegagalan yang diperbaiki modul ini:
  upload gagal permanen -> file 1-2 GB tertahan -> disk penuh ->
  disk guard memblokir SEMUA rekaman (IDN + Showroom) -> bot terkunci.

Modul ini HANYA boleh menghapus file yang terbukti terminal. Test di bawah
menjaga batas itu, terutama kasus shared path (satu file dirujuk baris
terminal DAN baris aktif) yang kalau salah akan menghilangkan rekaman
yang masih diantrikan.
"""
import tempfile
import unittest
from pathlib import Path

from bot import database, disk_reclaim
from bot.config import Config
from bot.timeutil import utc_now_iso


class DiskReclaimTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = Config.DB_PATH
        self._old_dir = Config.DOWNLOAD_DIR
        self._old_age = Config.DISK_RECLAIM_MIN_AGE_HOURS
        Config.DB_PATH = str(Path(self._tmp.name) / "reclaim.db")
        Config.DOWNLOAD_DIR = self._tmp.name
        Config.DISK_RECLAIM_MIN_AGE_HOURS = 0.0
        database.init_db()
        self._now = utc_now_iso()

    def tearDown(self):
        Config.DB_PATH = self._old_db
        Config.DOWNLOAD_DIR = self._old_dir
        Config.DISK_RECLAIM_MIN_AGE_HOURS = self._old_age
        self._tmp.cleanup()

    def _session(self, live_id: str, status: str, *, size: int = 4096) -> Path:
        path = Path(self._tmp.name) / f"{live_id}.mp4"
        path.write_bytes(b"0" * size)
        database.insert_live(
            live_id=live_id,
            member_username="jkt48_test",
            member_name="Test",
            started_at="2026-09-30T10:00:00+00:00",
        )
        database.update_status(
            live_id, status, file_path=str(path),
            file_size_bytes=size, download_ended_at=self._now,
        )
        return path

    def test_failed_session_file_is_reclaimed(self):
        path = self._session("dead", "failed")
        result = disk_reclaim.reclaim_disk(target_free_mb=10 ** 9)
        self.assertEqual(result.removed, 1)
        self.assertFalse(path.exists())
        self.assertGreater(result.freed_bytes, 0)

    def test_pending_upload_is_never_reclaimed(self):
        """pending_upload masih punya chances retry - jangan sampai dihapus."""
        path = self._session("waiting", "pending_upload")
        disk_reclaim.reclaim_disk(target_free_mb=10 ** 9)
        self.assertTrue(path.exists(), "file yang masih diantrean tak boleh hilang")

    def test_in_progress_statuses_are_never_reclaimed(self):
        for status in ("downloading", "segment_done", "merging",
                       "uploading_youtube", "detected", "download_complete"):
            with self.subTest(status=status):
                path = self._session(f"live_{status}", status)
                disk_reclaim.reclaim_disk(target_free_mb=10 ** 9)
                self.assertTrue(path.exists(), f"{status} tidak boleh direclaim")

    def test_shared_path_with_active_row_is_kept(self):
        """
        Kasus paling berbahaya: satu path dipakai baris terminal DAN baris
        aktif (merge group berbagi file). Hapus = rekaman hilang.
        """
        shared = Path(self._tmp.name) / "shared.mp4"
        shared.write_bytes(b"0" * 8192)
        for live_id, status in (("grp_terminal", "failed"),
                                ("grp_active", "pending_upload")):
            database.insert_live(
                live_id=live_id, member_username="jkt48_test",
                member_name="Test", started_at="2026-09-30T10:00:00+00:00",
            )
            database.update_status(
                live_id, status, file_path=str(shared),
                file_size_bytes=8192, download_ended_at=self._now,
            )

        result = disk_reclaim.reclaim_disk(target_free_mb=10 ** 9)

        self.assertEqual(result.removed, 0)
        self.assertTrue(shared.exists(), "path yang masih dirujuk baris aktif aman")

    def test_min_age_protects_recently_failed_file(self):
        """Sesi yang baru gagal harus dapat chances retry biasa dulu."""
        self._session("fresh", "failed")
        disk_reclaim.reclaim_disk(target_free_mb=10 ** 9, min_age_hours=48.0)
        self.assertTrue(
            (Path(self._tmp.name) / "fresh.mp4").exists(),
            "file yang baru gagal tidak boleh langsung dihapus",
        )

    def test_dry_run_deletes_nothing(self):
        path = self._session("preview", "failed")
        result = disk_reclaim.reclaim_disk(target_free_mb=10 ** 9, dry_run=True)
        self.assertTrue(path.exists())
        self.assertEqual(result.removed, 0)
        self.assertGreater(result.freed_bytes, 0, "dry-run tetap harus melaporkan ukuran")

    def test_missing_file_does_not_crash(self):
        database.insert_live(
            live_id="ghost", member_username="jkt48_test",
            member_name="Test", started_at="2026-09-30T10:00:00+00:00",
        )
        database.update_status(
            "ghost", "failed", file_path=str(Path(self._tmp.name) / "ghost.mp4"),
            file_size_bytes=100, download_ended_at=self._now,
        )
        result = disk_reclaim.reclaim_disk(target_free_mb=10 ** 9)
        self.assertEqual(result.removed, 0)
        self.assertEqual(result.failures, [])

    def test_summary_counts_only_existing_files(self):
        self._session("real", "failed", size=2048)
        summary = disk_reclaim.reclaim_summary()
        self.assertEqual(summary["existing_files"], 1)
        self.assertEqual(summary["total_bytes"], 2048)


if __name__ == "__main__":
    unittest.main()