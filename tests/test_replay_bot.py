"""
test_replay_bot.py - Unit tests untuk bot Telegram publik (download deep-link).

Menguji parsing payload, lookup DB by youtube_video_id, dan perilaku
_send_replay tanpa memanggil Telegram API: `send_message` & `copy_message`
diganti stub sehingga pesan/ID yang dikirim bisa diperiksa.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from bot import database
from bot.config import Config
from bot.replay_bot import ReplayBot, _parse_message_ids

CHAT_ID = 12345
ARCHIVE_CHANNEL = -1003972547638
YT_ID = "gJ8G0PnE_1x"


class ReplayBotTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._old_db = Config.DB_PATH
        self._old_token = Config.TELEGRAM_REPLAY_BOT_TOKEN
        self._old_channel = Config.TELEGRAM_ARCHIVE_CHANNEL_ID
        Config.DB_PATH = str(base / "test.db")
        Config.TELEGRAM_REPLAY_BOT_TOKEN = "dummy-token"
        Config.TELEGRAM_ARCHIVE_CHANNEL_ID = ARCHIVE_CHANNEL
        database.init_db()

        self.bot = ReplayBot()
        self.sent: list[str] = []
        self.copied: list[tuple[int, int, int]] = []  # (chat, from_chat, msg_id)
        self.bot.send_message = self._fake_send  # type: ignore[assignment]
        self.bot.copy_message = self._fake_copy  # type: ignore[assignment]

    def tearDown(self):
        Config.DB_PATH = self._old_db
        Config.TELEGRAM_REPLAY_BOT_TOKEN = self._old_token
        Config.TELEGRAM_ARCHIVE_CHANNEL_ID = self._old_channel
        self._tmp.cleanup()

    async def _fake_send(self, chat_id, text, parse_mode=None):
        self.sent.append(text)
        return 1

    async def _fake_copy(self, chat_id, from_chat_id, message_id):
        self.copied.append((chat_id, from_chat_id, message_id))
        return message_id

    def _insert_session(self, live_id: str, yt: str, tg_ids: str) -> None:
        database.insert_live(live_id, "jkt48_lulu", "Lulu JKT48", "2026-09-19T10:00:00")
        database.update_status(
            live_id, "done_youtube",
            youtube_video_id=yt, telegram_message_ids=tg_ids,
        )


class TestParseMessageIds(unittest.TestCase):
    def test_parses_comma_separated(self):
        self.assertEqual(_parse_message_ids("12,13,14"), [12, 13, 14])

    def test_ignores_junk_and_spaces(self):
        self.assertEqual(_parse_message_ids(" 12 , x ,, 15 "), [12, 15])

    def test_empty(self):
        self.assertEqual(_parse_message_ids(""), [])
        self.assertEqual(_parse_message_ids(None), [])


class TestDatabaseLookup(ReplayBotTestCase):
    def test_finds_session_by_youtube_id(self):
        self._insert_session("live_1", YT_ID, "101")
        row = database.get_archived_session_by_youtube_id(YT_ID)
        self.assertIsNotNone(row)
        self.assertEqual(row["live_id"], "live_1")
        self.assertEqual(row["telegram_message_ids"], "101")

    def test_returns_none_when_no_archive(self):
        # Sesi punya youtube id tapi belum diarsipkan ke Telegram
        database.insert_live("live_2", "jkt48_lulu", "Lulu", "2026-09-19T10:00:00")
        database.update_status("live_2", "done_youtube", youtube_video_id="abc123")
        self.assertIsNone(database.get_archived_session_by_youtube_id("abc123"))

    def test_returns_none_for_unknown_id(self):
        self.assertIsNone(database.get_archived_session_by_youtube_id("nope123"))
        self.assertIsNone(database.get_archived_session_by_youtube_id(""))


class TestSendReplay(ReplayBotTestCase):
    def test_single_part_copies_from_archive_channel(self):
        self._insert_session("live_1", YT_ID, "555")
        asyncio.run(self.bot._send_replay(CHAT_ID, YT_ID))
        self.assertEqual(self.copied, [(CHAT_ID, ARCHIVE_CHANNEL, 555)])
        self.assertEqual(self.sent, [])  # tidak ada pesan error

    def test_multi_part_copies_all_in_order(self):
        self._insert_session("live_2", YT_ID, "10,11,12")
        asyncio.run(self.bot._send_replay(CHAT_ID, YT_ID))
        self.assertEqual(
            self.copied,
            [(CHAT_ID, ARCHIVE_CHANNEL, 10), (CHAT_ID, ARCHIVE_CHANNEL, 11), (CHAT_ID, ARCHIVE_CHANNEL, 12)],
        )

    def test_unknown_payload_replies_not_found(self):
        asyncio.run(self.bot._send_replay(CHAT_ID, "tidak-ada"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("tidak ditemukan", self.sent[0].lower())
        self.assertEqual(self.copied, [])

    def test_archive_with_corrupt_ids_replies_not_found(self):
        self._insert_session("live_3", YT_ID, "  ,,")
        asyncio.run(self.bot._send_replay(CHAT_ID, YT_ID))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("tidak ditemukan", self.sent[0].lower())


class TestDispatch(ReplayBotTestCase):
    def _send(self, text: str) -> None:
        asyncio.run(
            self.bot._handle_update({
                "update_id": 1,
                "message": {"chat": {"id": CHAT_ID}, "text": text},
            })
        )

    def test_start_without_payload_shows_welcome(self):
        self._send("/start")
        self.assertTrue(any("Replay Bot" in m for m in self.sent))

    def test_non_command_shows_welcome(self):
        self._send("halo")
        self.assertTrue(any("Replay Bot" in m for m in self.sent))

    def test_start_with_payload_triggers_copy(self):
        self._insert_session("live_1", YT_ID, "777")
        self._send(f"/start {YT_ID}")
        self.assertEqual(self.copied, [(CHAT_ID, ARCHIVE_CHANNEL, 777)])


class TestStartGate(ReplayBotTestCase):
    def test_start_fails_without_token(self):
        Config.TELEGRAM_REPLAY_BOT_TOKEN = ""
        self.assertFalse(ReplayBot().start())

    def test_start_fails_without_archive_channel(self):
        Config.TELEGRAM_ARCHIVE_CHANNEL_ID = 0
        self.assertFalse(ReplayBot().start())


if __name__ == "__main__":
    unittest.main()
