"""
test_main_integration.py - Integration tests for main.py channel-control wiring.

Menguji bagian main.py yang dipakai fitur start/stop channel:
  * `_cancel_active_recording` (dipanggil Telegram /stop)
  * warning satu kali untuk member yang di-stop tapi masih ada di members.txt
  * filter `enabled` pada siklus poll
"""
import unittest
from unittest.mock import patch

from bot import database, main as bot_main, member_manager
from tests.test_member_manager import HLS_LULU, MemberManagerTestCase


class TestMainBotIntegration(MemberManagerTestCase):
    def _make_bot(self) -> bot_main.JKT48LiveBot:
        """Bangun orchestrator tanpa Telegram/YouTube client sungguhan."""
        with patch("bot.main.TelegramSender"), patch("bot.main.YouTubeChannelPool"):
            return bot_main.JKT48LiveBot()

    def test_cancel_active_recording_invokes_downloader(self):
        bot = self._make_bot()
        bot.active_live_ids["jkt48_lulu"] = "jkt48_lulu_1699999999"

        with patch("bot.main.cancel_download", return_value=True) as cancel:
            message = bot._cancel_active_recording("JKT48_LULU")

        cancel.assert_called_once_with("jkt48_lulu_1699999999")
        self.assertIn("dihentikan", message)

    def test_cancel_active_recording_without_active_live_is_silent(self):
        bot = self._make_bot()
        with patch("bot.main.cancel_download", return_value=True) as cancel:
            message = bot._cancel_active_recording("jkt48_lulu")
        self.assertEqual(message, "")
        cancel.assert_not_called()

    def test_cancel_active_recording_reports_when_process_missing(self):
        bot = self._make_bot()
        bot.active_live_ids["jkt48_lulu"] = "jkt48_lulu_1"
        with patch("bot.main.cancel_download", return_value=False):
            message = bot._cancel_active_recording("jkt48_lulu")
        self.assertIn("tidak ditemukan", message)

    def test_sync_whitelist_warns_when_stopped_member_re_added_manually(self):
        member_manager.stop_member("jkt48-official")  # → "# STOPPED: jkt48-official"
        bot = self._make_bot()

        # Marker STOP bukan entri whitelist aktif → belum ada warning
        bot.sync_members_whitelist()
        self.assertEqual(bot._stopped_members_logged, set())

        # User menambahkan manual baris aktif padahal statusnya masih STOP
        with open(self.members_file, "a", encoding="utf-8") as f:
            f.write("jkt48-official\n")

        bot.sync_members_whitelist()
        self.assertEqual(bot._stopped_members_logged, {"jkt48-official"})

        # Siklus berikutnya tidak boleh spam warning yang sama
        bot.sync_members_whitelist()
        self.assertEqual(bot._stopped_members_logged, {"jkt48-official"})

        # Setelah diaktifkan kembali (resume), daftar warning dibersihkan
        member_manager.resume_member("jkt48-official")
        bot.sync_members_whitelist()
        self.assertEqual(bot._stopped_members_logged, set())

    def test_monitor_candidates_exclude_stopped_members(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        member_manager.add_member(
            "jkt48_delynn",
            hls_url=HLS_LULU.replace("LuflGrysVdSz", "5j4KMiUb4eXE"),
        )
        member_manager.stop_member("jkt48_lulu")

        candidates = [
            m["username"]
            for m in database.get_all_member_hls(include_disabled=False)
            if m.get("hls_url")
        ]
        self.assertIn("jkt48_delynn", candidates)
        self.assertNotIn("jkt48_lulu", candidates)

        # Setelah resume, member kembali masuk kandidat
        member_manager.resume_member("jkt48_lulu")
        candidates = [
            m["username"]
            for m in database.get_all_member_hls(include_disabled=False)
            if m.get("hls_url")
        ]
        self.assertIn("jkt48_lulu", candidates)


if __name__ == "__main__":
    unittest.main()