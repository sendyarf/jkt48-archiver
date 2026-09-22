"""
test_admin_bot_commands.py - Integration tests for Telegram admin bot commands.

Menguji dispatcher command (/help /list /add /stop /resume /remove /sethls
/info /status /live) tanpa memanggil Telegram API: `send_message` diganti
dengan stub sehingga balasan bisa diperiksa.
"""
import asyncio
import unittest

from bot import database, member_manager
from bot.admin_bot import AdminBot
from bot.config import Config
from tests.test_member_manager import HLS_LULU, MemberManagerTestCase

ADMIN_ID = 999


class TestAdminBotCommands(MemberManagerTestCase):
    def setUp(self):
        super().setUp()
        self._old_admin = Config.ADMIN_CHAT_ID
        self._old_extra = Config.TELEGRAM_ADMIN_IDS
        Config.ADMIN_CHAT_ID = ADMIN_ID
        Config.TELEGRAM_ADMIN_IDS = ""

        self.sent: list[tuple[int, str]] = []
        self.stop_callbacks: list[str] = []
        self.bot = AdminBot(
            on_stop_recording=lambda username: (
                self.stop_callbacks.append(username) or " Rekaman aktif dihentikan"
            )
        )
        self.bot.send_message = self._fake_send  # type: ignore[assignment]

    def tearDown(self):
        Config.ADMIN_CHAT_ID = self._old_admin
        Config.TELEGRAM_ADMIN_IDS = self._old_extra
        super().tearDown()

    async def _fake_send(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))
        return 1

    def _send(self, text: str, chat_id: int = ADMIN_ID) -> str:
        """Kirim satu pesan command, kembalikan gabungan balasan."""
        self.sent.clear()
        asyncio.run(
            self.bot._handle_update({
                "update_id": 1,
                "message": {"chat": {"id": chat_id}, "text": text},
            })
        )
        return "\n".join(t for _, t in self.sent)

    # ── Tests ─────────────────────────────────────────────────────────────
    def test_help_command(self):
        reply = self._send("/help")
        self.assertIn("/list", reply)
        self.assertIn("/stop", reply)

    def test_unauthorized_chat_is_rejected(self):
        reply = self._send("/list", chat_id=12345)
        self.assertIn("Akses ditolak", reply)
        self.assertIn("12345", reply)

    def test_empty_allowlist_rejects_everyone(self):
        self.bot._admin_ids = []
        reply = self._send("/list", chat_id=ADMIN_ID)
        self.assertIn("Akses ditolak", reply)
        self.assertNotIn("jkt48", reply)

    def test_empty_allowlist_does_not_start(self):
        self.bot._admin_ids = []
        self.assertIsNone(self.bot.start())
        self.assertFalse(self.bot._running)

    def test_unknown_command(self):
        reply = self._send("/nope")
        self.assertIn("tidak dikenal", reply)

    def test_non_command_text(self):
        reply = self._send("halo bot")
        self.assertIn("/help", reply)

    def test_stop_command_disables_and_calls_recording_callback(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        reply = self._send("/stop lulu")

        self.assertIn("di-STOP", reply)
        self.assertIn("Rekaman aktif dihentikan", reply)
        self.assertEqual(self.stop_callbacks, ["jkt48_lulu"])
        self.assertEqual(database.get_member_hls("jkt48_lulu")["enabled"], 0)
        self.assertIn("# STOPPED: jkt48_lulu", self._members_text())

    def test_stop_command_reports_already_stopped(self):
        member_manager.stop_member("jkt48-official")
        reply = self._send("/stop jkt48-official")
        self.assertIn("sudah dalam status STOP", reply)

    def test_add_then_resume_then_remove(self):
        reply = self._send("/add jkt48_baru Baru JKT48")
        self.assertIn("AKTIF dipantau", reply)
        self.assertEqual(database.get_member_hls("jkt48_baru")["display_name"], "Baru JKT48")

        member_manager.stop_member("jkt48_baru")
        reply = self._send("/resume baru")
        self.assertIn("diaktifkan kembali", reply)
        self.assertEqual(database.get_member_hls("jkt48_baru")["enabled"], 1)

        reply = self._send("/remove jkt48_baru")
        self.assertIn("dihapus", reply)
        self.assertIsNone(database.get_member_hls("jkt48_baru"))

    def test_list_modes(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        member_manager.stop_member("jkt48-official")

        reply = self._send("/list stopped")
        self.assertIn("jkt48-official", reply)

        reply = self._send("/list unknown")
        self.assertIn("jkt48_delynn", reply)

        reply = self._send("/list bogus")
        self.assertIn("tidak dikenal", reply)

    def test_info_command_shows_hls(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        reply = self._send("/info lulu")
        self.assertIn("LuflGrysVdSz", reply)
        self.assertIn(HLS_LULU, reply)

        reply = self._send("/info tidak-ada")
        self.assertIn("tidak ditemukan", reply)

    def test_sethls_command(self):
        reply = self._send(f"/sethls jkt48_lulu {HLS_LULU}")
        self.assertIn("disimpan", reply)
        self.assertEqual(database.get_member_hls("jkt48_lulu")["hls_url"], HLS_LULU)

        reply = self._send("/sethls jkt48_lulu not-a-url")
        self.assertIn("tidak valid", reply)

    def test_status_and_live_commands(self):
        reply = self._send("/status")
        self.assertIn("Total member", reply)
        self.assertIn("Antrian upload", reply)

        reply = self._send("/live")
        self.assertIn("Tidak ada sesi berjalan", reply)

    def test_id_command(self):
        reply = self._send("/id")
        self.assertIn(str(ADMIN_ID), reply)


if __name__ == "__main__":
    unittest.main()