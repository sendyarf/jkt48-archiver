"""
test_live_admin_notifications.py - Tes notifikasi admin untuk proses live IDN & Showroom.

Yang dijaga:
  * `_notify_admin` memilih rute yang benar (admin bot BotFather -> semua admin,
    fallback userbot ke ADMIN_CHAT_ID), no-op tanpa tujuan, best-effort saat
    pengiriman gagal, dan mati total saat ADMIN_LIVE_NOTIFY_ENABLED=false.
  * Proses record IDN & Showroom mengirim notifikasi mulai / segmen selesai / gagal.
  * `MergeManager` mengirim notifikasi merge mulai / selesai / gagal lewat
    callback `on_notify`, dan tetap aman saat callback tidak diisi.
  * Pipeline upload mengirim notifikasi per tahap: Telegram, YouTube, kuota habis,
    dan file hilang.
"""
import asyncio
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import database, main as bot_main, merger
from bot.config import Config
from bot.youtube_uploader import YouTubeQuotaExceeded
from tests.test_member_manager import HLS_LULU, MemberManagerTestCase
from tests.test_merge_reconnect import MEMBER, NAME, MergeTestCase
from tests.test_pending_upload import HandleUploadReadyTestCase

SAMPLE_ROOM = {
    "username": "jkt48_lulu",
    "display_name": "Lulu JKT48",
    "room_id": "317738",
    "hls_url": "https://cdn.showroom.example/room.m3u8",
    "room_name": "Lulu Live",
    "cover_image": "https://static.showroom-live.com/cover.jpg",
    "started_at": "2026-09-25T10:00:00+00:00",
}


def _texts(mock: AsyncMock) -> list[str]:
    """Semua teks yang dikirim lewat mock notifikasi (argumen posisi pertama)."""
    return [call.args[0] for call in mock.await_args_list]

class TestNotifyAdminRouting(MemberManagerTestCase):
    """Rute pengiriman `JKT48LiveBot._notify_admin`."""

    def setUp(self):
        super().setUp()
        self._old_token = Config.TELEGRAM_BOT_TOKEN
        self._old_admin = Config.ADMIN_CHAT_ID
        self._old_ids = Config.TELEGRAM_ADMIN_IDS
        self._old_enabled = Config.ADMIN_LIVE_NOTIFY_ENABLED
        Config.ADMIN_LIVE_NOTIFY_ENABLED = True

    def tearDown(self):
        Config.TELEGRAM_BOT_TOKEN = self._old_token
        Config.ADMIN_CHAT_ID = self._old_admin
        Config.TELEGRAM_ADMIN_IDS = self._old_ids
        Config.ADMIN_LIVE_NOTIFY_ENABLED = self._old_enabled
        super().tearDown()

    def _make_bot(self) -> bot_main.JKT48LiveBot:
        with patch("bot.main.TelegramSender"), patch("bot.main.YouTubeChannelPool"):
            return bot_main.JKT48LiveBot()

    def test_lewat_admin_bot_mengirim_ke_semua_admin(self):
        Config.TELEGRAM_BOT_TOKEN = "123:abc"
        Config.ADMIN_CHAT_ID = 999
        bot = self._make_bot()
        bot.admin_bot = SimpleNamespace(notify_admins=AsyncMock())

        asyncio.run(bot._notify_admin("<b>REC MULAI</b>"))

        bot.admin_bot.notify_admins.assert_awaited_once_with(
            "<b>REC MULAI</b>", parse_mode="HTML"
        )

    def test_tanpa_admin_bot_fallback_ke_userbot(self):
        Config.TELEGRAM_BOT_TOKEN = ""
        Config.ADMIN_CHAT_ID = 777
        Config.TELEGRAM_ADMIN_IDS = ""
        bot = self._make_bot()
        bot.tg.send_message = AsyncMock(return_value=1)

        asyncio.run(bot._notify_admin("halo admin"))

        bot.tg.send_message.assert_awaited_once_with("halo admin", channel_id=777)

    def test_tanpa_tujuan_tidak_mengirim(self):
        Config.TELEGRAM_BOT_TOKEN = ""
        Config.ADMIN_CHAT_ID = 0
        Config.TELEGRAM_ADMIN_IDS = ""
        bot = self._make_bot()
        bot.tg.send_message = AsyncMock()

        asyncio.run(bot._notify_admin("tidak ada tujuan"))

        bot.tg.send_message.assert_not_awaited()

    def test_flag_nonaktif_mematikan_notifikasi(self):
        Config.TELEGRAM_BOT_TOKEN = ""
        Config.ADMIN_CHAT_ID = 777
        Config.ADMIN_LIVE_NOTIFY_ENABLED = False
        bot = self._make_bot()
        bot.tg.send_message = AsyncMock()

        asyncio.run(bot._notify_admin("dimatikan"))

        bot.tg.send_message.assert_not_awaited()

    def test_kegagalan_kirim_tidak_melempar(self):
        Config.TELEGRAM_BOT_TOKEN = ""
        Config.ADMIN_CHAT_ID = 777
        bot = self._make_bot()
        bot.tg.send_message = AsyncMock(side_effect=RuntimeError("network down"))

        asyncio.run(bot._notify_admin("tetap aman"))  # tidak boleh raise


class TestRecordNotifications(MemberManagerTestCase):
    """Notifikasi proses record IDN & Showroom."""

    def _make_bot(self) -> bot_main.JKT48LiveBot:
        with patch("bot.main.TelegramSender"), patch("bot.main.YouTubeChannelPool"):
            return bot_main.JKT48LiveBot()

    def _notify_mock(self, bot: bot_main.JKT48LiveBot) -> AsyncMock:
        notify = AsyncMock()
        bot._notify_admin = notify  # type: ignore[method-assign]
        return notify

    def _video(self, name: str) -> Path:
        path = Path(self._tmp.name) / name
        path.write_bytes(b"\x00" * 2048)
        return path

    def test_idn_mengirim_notifikasi_mulai_dan_segmen_selesai(self):
        bot = self._make_bot()
        notify = self._notify_mock(bot)
        video = self._video("idn_seg.mp4")

        async def fake_lookup(username, platform="idn"):
            return {"slug": "haii-1", "title": "haii"}

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            return video

        async def fake_add_segment(**kwargs):
            return None

        bot._fetch_member_live = fake_lookup
        bot.merge_mgr.add_segment = fake_add_segment
        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_member_task(
                {"username": "jkt48_lulu", "display_name": "Lulu JKT48", "hls_url": HLS_LULU},
                "jkt48_lulu_1",
            ))

        texts = _texts(notify)
        self.assertTrue(any("REC MULAI" in t and "IDN" in t for t in texts), texts)
        self.assertTrue(any("jkt48_lulu_1" in t for t in texts), texts)
        self.assertTrue(any("SEGMEN SELESAI" in t for t in texts), texts)

    def test_idn_gagal_mengirim_notifikasi_error(self):
        bot = self._make_bot()
        notify = self._notify_mock(bot)
        bot._fetch_member_live = AsyncMock(return_value=None)

        with patch("bot.main.download_stream", side_effect=bot_main.DownloadError("stream mati")):
            asyncio.run(bot._record_member_task(
                {"username": "jkt48_lulu", "display_name": "Lulu JKT48", "hls_url": HLS_LULU},
                "jkt48_lulu_2",
            ))

        texts = _texts(notify)
        self.assertTrue(any("REC GAGAL" in t and "stream mati" in t for t in texts), texts)


    def test_showroom_mengirim_notifikasi_mulai_dan_segmen_selesai(self):
        bot = self._make_bot()
        notify = self._notify_mock(bot)
        video = self._video("sr_seg.mp4")

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            return video

        async def fake_add_segment(**kwargs):
            return None

        async def fake_room_live(room_id):
            return False

        bot.merge_mgr.add_segment = fake_add_segment
        bot.showroom.is_room_live = fake_room_live

        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_showroom_task(dict(SAMPLE_ROOM), "sr_JKT48_Lulu_77"))

        texts = _texts(notify)
        self.assertTrue(
            any("REC MULAI" in t and "SHOWROOM" in t and "sr_JKT48_Lulu_77" in t for t in texts),
            texts,
        )
        self.assertTrue(any("SEGMEN SELESAI" in t and "SHOWROOM" in t for t in texts), texts)

    def test_showroom_gagal_mengirim_notifikasi_error(self):
        bot = self._make_bot()
        notify = self._notify_mock(bot)

        async def fake_room_live(room_id):
            return False

        bot.showroom.is_room_live = fake_room_live

        with patch("bot.main.download_stream", side_effect=bot_main.DownloadError("hls putus")):
            asyncio.run(bot._record_showroom_task(dict(SAMPLE_ROOM), "sr_JKT48_Lulu_78"))

        texts = _texts(notify)
        self.assertTrue(
            any("REC GAGAL" in t and "SHOWROOM" in t and "hls putus" in t for t in texts),
            texts,
        )


class TestMergeNotifications(MergeTestCase):
    """Notifikasi tahap merge lewat callback `on_notify`."""

    def _manager_with_notify(self) -> merger.MergeManager:
        self.notifications: list[str] = []

        async def fake_notify(text: str) -> None:
            self.notifications.append(text)

        return merger.MergeManager(
            on_upload_ready=self._fake_upload,
            probe_active=self._fake_probe,
            fetch_live=self._fake_live,
            on_notify=fake_notify,
        )

    def _add_extra_segment(self, group_id: int, file_name: str) -> Path:
        live_id = f"{MEMBER}_{file_name}"
        path = self._dummy_video(file_name)
        database.insert_live(
            live_id=live_id,
            member_username=MEMBER,
            member_name=NAME,
            started_at=datetime.now().isoformat(),
            hls_url=HLS_LULU,
        )
        database.update_status(
            live_id, "segment_done",
            file_path=str(path),
            file_size_bytes=path.stat().st_size,
            download_ended_at=datetime.now().isoformat(),
        )
        database.set_session_merge_group(live_id, group_id)
        return path

    def test_concat_mengirim_notifikasi_mulai_dan_selesai(self):
        group_id = self._make_group(with_segment=True, file_name="a.mp4")
        self._add_extra_segment(group_id, "b.mp4")
        merged = self._dummy_video("merged.mp4", 16384)

        mgr = self._manager_with_notify()
        mgr._concat_files = AsyncMock(return_value=str(merged))
        with patch("bot.merger.has_enough_disk_space", return_value=True):
            asyncio.run(mgr._merge_and_upload(group_id, MEMBER))

        self.assertTrue(any("MERGE MULAI" in t for t in self.notifications), self.notifications)
        done = [t for t in self.notifications if "MERGE SELESAI" in t]
        self.assertEqual(len(done), 1, self.notifications)
        self.assertIn("2 segmen", done[0])
        self.assertEqual(len(self.uploads), 1)

    def test_concat_gagal_mengirim_notifikasi_dan_segmen_diupload_terpisah(self):
        group_id = self._make_group(with_segment=True, file_name="c.mp4")
        self._add_extra_segment(group_id, "d.mp4")

        mgr = self._manager_with_notify()
        mgr._concat_files = AsyncMock(return_value=None)
        with patch("bot.merger.has_enough_disk_space", return_value=True):
            asyncio.run(mgr._merge_and_upload(group_id, MEMBER))

        self.assertTrue(any("MERGE GAGAL" in t for t in self.notifications), self.notifications)
        self.assertEqual(len(self.uploads), 2, "segmen harus diupload terpisah")

    def test_tanpa_callback_tetap_berjalan(self):
        group_id = self._make_group(with_segment=True, file_name="e.mp4")
        self._add_extra_segment(group_id, "f.mp4")
        merged = self._dummy_video("merged2.mp4", 16384)

        with patch("bot.merger.has_enough_disk_space", return_value=True):
            self.mgr._concat_files = AsyncMock(return_value=str(merged))
            asyncio.run(self.mgr._merge_and_upload(group_id, MEMBER))

        self.assertEqual(len(self.uploads), 1)


class TestUploadNotifications(HandleUploadReadyTestCase):
    """Notifikasi tiap tahap pipeline upload (Telegram -> YouTube)."""

    def _bot_with_notify(self):
        bot = self._make_bot()
        notify = AsyncMock()
        bot._notify_admin = notify  # type: ignore[method-assign]
        return bot, notify

    def _upload(self, bot, path: Path, platform: str = "idn") -> bool:
        return asyncio.run(bot.handle_upload_ready(
            live_id="merged_1",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path),
            platform=platform,
        ))

    def test_sukses_mengirim_notifikasi_telegram_dan_youtube(self):
        bot, notify = self._bot_with_notify()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[21])
        bot.tg.send_message = AsyncMock(return_value=88)
        bot.yt_pool.upload_video = Mock(return_value=("ytNEW", "ch1"))
        bot.yt_pool.set_thumbnail = Mock(return_value=False)

        with patch("bot.main.build_collage", return_value=None):
            complete = self._upload(bot, path, platform="showroom")

        self.assertTrue(complete)
        texts = _texts(notify)
        self.assertTrue(any("UPLOAD TELEGRAM" in t and "SHOWROOM" in t for t in texts), texts)
        self.assertTrue(any("TELEGRAM SELESAI" in t for t in texts), texts)
        self.assertTrue(any("UPLOAD YOUTUBE" in t and "SHOWROOM" in t for t in texts), texts)
        youtube_done = [t for t in texts if "YOUTUBE SELESAI" in t]
        self.assertEqual(len(youtube_done), 1, texts)
        self.assertIn("youtu.be/ytNEW", youtube_done[0])

    def test_kuota_youtube_habis_mengirim_alert(self):
        bot, notify = self._bot_with_notify()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[111])
        bot.yt_pool.upload_video = Mock(
            side_effect=YouTubeQuotaExceeded("All channels quota exceeded: ch1(10000)")
        )

        complete = self._upload(bot, path)

        self.assertFalse(complete)
        texts = _texts(notify)
        self.assertTrue(any("KUOTA HABIS" in t for t in texts), texts)
        self.assertTrue(any("TELEGRAM SELESAI" in t for t in texts), texts)

    def test_telegram_gagal_mengirim_notifikasi_dan_menunda_youtube(self):
        bot, notify = self._bot_with_notify()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(side_effect=RuntimeError("flood wait"))
        bot.yt_pool.upload_video = Mock(return_value=("SHOULD", "not"))

        complete = self._upload(bot, path)

        self.assertFalse(complete)
        bot.yt_pool.upload_video.assert_not_called()
        texts = _texts(notify)
        self.assertTrue(any("TELEGRAM GAGAL" in t and "flood wait" in t for t in texts), texts)
        self.assertFalse(any("UPLOAD YOUTUBE" in t for t in texts), texts)

    def test_file_hilang_mengirim_notifikasi_gagal(self):
        bot, notify = self._bot_with_notify()
        path = self._write_video()
        self._insert_session_for_upload(path)
        path.unlink()

        complete = self._upload(bot, path)

        self.assertFalse(complete)
        texts = _texts(notify)
        self.assertTrue(any("FILE HILANG" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()

