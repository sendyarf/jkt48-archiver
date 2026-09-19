"""
test_shutdown_salvage.py - Tes penyelamatan segmen parsial saat shutdown/restart.

Latar (temuan 19 Sep 2026): `shutdown()` dulu langsung `task.cancel()` untuk
semua rekaman, padahal `GRACEFUL_SHUTDOWN_SECONDS` didefinisikan tapi tidak
dipakai. Akibatnya sesi yang sedang direkam tetap berstatus 'downloading',
lalu dihapus `clean_interrupted_downloads()` saat boot — file parsial yang
sudah ditutup rapi oleh yt-dlp (SIGTERM) tidak pernah diupload.
"""
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import database, main as bot_main
from bot.config import Config
from tests.test_member_manager import MemberManagerTestCase

USERNAME = "jkt48_lulu"
LIVE_ID = "jkt48_lulu_1699999999"


class ShutdownSalvageTestCase(MemberManagerTestCase):
    def setUp(self):
        super().setUp()
        self._old_download_dir = Config.DOWNLOAD_DIR
        self._old_grace = Config.GRACEFUL_SHUTDOWN_SECONDS
        self.downloads = Path(self._tmp.name) / "downloads"
        self.downloads.mkdir(exist_ok=True)
        Config.DOWNLOAD_DIR = str(self.downloads)
        Config.GRACEFUL_SHUTDOWN_SECONDS = 1

    def tearDown(self):
        Config.DOWNLOAD_DIR = self._old_download_dir
        Config.GRACEFUL_SHUTDOWN_SECONDS = self._old_grace
        super().tearDown()

    def _make_bot(self) -> bot_main.JKT48LiveBot:
        with patch("bot.main.TelegramSender"), patch("bot.main.YouTubeChannelPool"):
            return bot_main.JKT48LiveBot()

    def _make_session(self, live_id: str = LIVE_ID, status: str = "downloading",
                      platform: str = "idn") -> str:
        database.insert_live(
            live_id=live_id,
            member_username=USERNAME,
            member_name="Lulu JKT48",
            started_at="2026-09-19T10:00:00+00:00",
            platform=platform,
        )
        database.update_status(
            live_id, status, download_started_at="2026-09-19T10:00:00+00:00"
        )
        return live_id

    def _write_partial(self, live_id: str = LIVE_ID, size: int = 8 * 1024 * 1024) -> Path:
        path = self.downloads / f"{USERNAME}_20260919_170000_{live_id}.mp4"
        path.write_bytes(b"\x00" * size)
        return path
class TestGracefulStop(ShutdownSalvageTestCase):
    def test_menunggu_task_selesai_setelah_sigterm(self):
        """Task yang berhenti karena SIGTERM tidak boleh dibatalkan paksa."""
        bot = self._make_bot()
        self._make_session()

        async def scenario():
            done_event = asyncio.Event()

            def fake_cancel(live_id):
                done_event.set()   # meniru yt-dlp keluar setelah SIGTERM
                return True

            async def recording():
                await done_event.wait()

            task = asyncio.create_task(recording())
            bot.recording_tasks.add(task)
            bot.active_live_ids[USERNAME] = LIVE_ID

            with patch("bot.main.cancel_download", side_effect=fake_cancel) as cancel:
                await bot._stop_recordings_gracefully()

            cancel.assert_called_once_with(LIVE_ID)
            return task

        task = asyncio.run(scenario())
        self.assertTrue(task.done())
        self.assertFalse(task.cancelled(), "task harus selesai normal, bukan dibatalkan")

    def test_tanpa_task_aktif_tidak_melempar(self):
        bot = self._make_bot()
        with patch("bot.main.cancel_download") as cancel:
            asyncio.run(bot._stop_recordings_gracefully())
        cancel.assert_not_called()


class TestSalvagePartialSegments(ShutdownSalvageTestCase):
    def test_task_macet_dibatalkan_lalu_file_parsial_diselamatkan(self):
        bot = self._make_bot()
        self._make_session()
        partial = self._write_partial()

        async def scenario():
            async def stuck():
                await asyncio.sleep(3600)

            task = asyncio.create_task(stuck())
            bot.recording_tasks.add(task)
            bot.active_live_ids[USERNAME] = LIVE_ID

            with patch("bot.main.cancel_download", return_value=True):
                await bot._stop_recordings_gracefully()
            await bot.merge_mgr.shutdown()

        asyncio.run(scenario())

        row = database.get_session(LIVE_ID)
        self.assertEqual(row["status"], "segment_done", "sesi harus jadi segmen sah")
        self.assertEqual(row["file_path"], str(partial))
        self.assertEqual(row["file_size_bytes"], partial.stat().st_size)
        self.assertIsNotNone(
            row["merge_group_id"],
            "segmen selamat harus masuk merge group supaya ikut diupload",
        )
        group = database.get_merge_group(row["merge_group_id"])
        self.assertEqual(group["status"], "waiting")
        self.assertEqual(len(database.get_merge_segments(row["merge_group_id"])), 1)

    def test_file_terlalu_kecil_tidak_didaftarkan(self):
        bot = self._make_bot()
        self._make_session()
        self._write_partial(size=100 * 1024)   # 100 KB jauh di bawah ambang 5 MB

        async def scenario():
            async def stuck():
                await asyncio.sleep(3600)

            task = asyncio.create_task(stuck())
            bot.recording_tasks.add(task)
            bot.active_live_ids[USERNAME] = LIVE_ID
            with patch("bot.main.cancel_download", return_value=True):
                await bot._stop_recordings_gracefully()

        asyncio.run(scenario())

        row = database.get_session(LIVE_ID)
        self.assertEqual(row["status"], "downloading", "file rusak tidak boleh didaftarkan")
        self.assertIsNone(row["merge_group_id"])

    def test_sesi_yang_sudah_selesai_tidak_disentuh(self):
        bot = self._make_bot()
        self._make_session(status="segment_done")
        self._write_partial()

        asyncio.run(bot._salvage_partial_segments({USERNAME: LIVE_ID}))

        row = database.get_session(LIVE_ID)
        self.assertEqual(row["status"], "segment_done")
        self.assertIsNone(row["file_path"], "sesi yang sudah tercatat tidak ditimpa")

    def test_showroom_memakai_platform_dari_sesi(self):
        bot = self._make_bot()
        live_id = "sr_JKT48_Olla_123_r1"
        self._make_session(live_id=live_id, platform="showroom")
        self._write_partial(live_id=live_id)

        asyncio.run(bot._salvage_partial_segments({f"sr:{USERNAME}": live_id}))

        row = database.get_session(live_id)
        self.assertEqual(row["status"], "segment_done")
        group = database.get_merge_group(row["merge_group_id"])
        self.assertEqual(group["platform"], "showroom")


if __name__ == "__main__":
    unittest.main()
