"""
test_pending_upload.py - Anti-double-upload & dual-destination (YouTube + arsip TG).

Cakup:
  * get_pending_uploads_youtube / get_all_pending_videos dedupe per file_path
  * get_group_upload_state untuk merge group
  * set_session_fields tidak mengubah status
  * handle_upload_ready: archive TG jalan walau YouTube kuota habis;
    retry tidak mengulang tujuan yang sudah selesai
"""
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bot import database, main as bot_main
from bot.config import Config
from bot.youtube_uploader import YouTubeQuotaExceeded
from tests.test_member_manager import MemberManagerTestCase


class PendingUploadDbTestCase(MemberManagerTestCase):
    def _insert_session(self, live_id: str, *, status: str, file_path: str = "",
                        merge_group_id: int | None = None, **extra) -> None:
        database.insert_live(
            live_id=live_id,
            member_username="jkt48_lulu",
            member_name="Lulu",
            started_at="2026-09-22T10:00:00+00:00",
        )
        database.update_status(live_id, status, file_path=file_path, **extra)
        if merge_group_id is not None:
            database.set_session_merge_group(live_id, merge_group_id)


class TestPendingQueueDedupe(PendingUploadDbTestCase):
    def test_pending_youtube_dedupes_shared_merged_path(self):
        gid = database.create_merge_group(
            "jkt48_lulu", "Lulu", "2026-09-22T10:00:00+00:00"
        )
        merged = "/tmp/merge_group_%d.mp4" % gid
        self._insert_session("seg_a", status="pending_upload", file_path=merged,
                             merge_group_id=gid)
        self._insert_session("seg_b", status="pending_upload", file_path=merged,
                             merge_group_id=gid)
        self._insert_session("seg_c", status="pending_upload", file_path=merged,
                             merge_group_id=gid)

        pending = database.get_pending_uploads_youtube()
        self.assertEqual(len(pending), 1, pending)
        self.assertEqual(pending[0]["file_path"], merged)

    def test_pending_youtube_keeps_distinct_paths(self):
        self._insert_session("solo_1", status="pending_upload", file_path="/tmp/a.mp4")
        self._insert_session("solo_2", status="pending_upload", file_path="/tmp/b.mp4")

        pending = database.get_pending_uploads_youtube()
        self.assertEqual(len(pending), 2)

    def test_all_pending_videos_dedupes_shared_path(self):
        self._insert_session("x1", status="pending_upload", file_path="/tmp/same.mp4")
        self._insert_session("x2", status="pending_upload", file_path="/tmp/same.mp4")
        self._insert_session("x3", status="download_complete", file_path="/tmp/other.mp4")

        items = database.get_all_pending_videos()
        paths = [i["file_path"] for i in items]
        self.assertEqual(paths.count("/tmp/same.mp4"), 1)
        self.assertIn("/tmp/other.mp4", paths)


class TestUploadStateHelpers(PendingUploadDbTestCase):
    def test_set_session_fields_does_not_change_status(self):
        self._insert_session("s1", status="pending_upload", file_path="/tmp/v.mp4")
        database.set_session_fields(live_id="s1", telegram_message_ids="10,11")
        row = database.get_session("s1")
        self.assertEqual(row["status"], "pending_upload")
        self.assertEqual(row["telegram_message_ids"], "10,11")

    def test_set_session_fields_updates_whole_group(self):
        gid = database.create_merge_group(
            "jkt48_lulu", "Lulu", "2026-09-22T10:00:00+00:00"
        )
        self._insert_session("g1", status="pending_upload", file_path="/tmp/m.mp4",
                             merge_group_id=gid)
        self._insert_session("g2", status="pending_upload", file_path="/tmp/m.mp4",
                             merge_group_id=gid)
        database.set_session_fields(group_id=gid, telegram_message_ids="99")
        for lid in ("g1", "g2"):
            self.assertEqual(database.get_session(lid)["telegram_message_ids"], "99")
            self.assertEqual(database.get_session(lid)["status"], "pending_upload")

    def test_group_upload_state_merges_progress(self):
        gid = database.create_merge_group(
            "jkt48_lulu", "Lulu", "2026-09-22T10:00:00+00:00"
        )
        self._insert_session("p1", status="pending_upload", file_path="/tmp/m.mp4",
                             merge_group_id=gid, youtube_video_id="ytABC")
        self._insert_session("p2", status="pending_upload", file_path="/tmp/m.mp4",
                             merge_group_id=gid, telegram_message_ids="7,8",
                             telegram_message_id=42)

        state = database.get_group_upload_state(group_id=gid)
        self.assertEqual(state["youtube_video_id"], "ytABC")
        self.assertEqual(state["telegram_message_ids"], "7,8")
        self.assertEqual(state["telegram_message_id"], 42)


class HandleUploadReadyTestCase(MemberManagerTestCase):
    def setUp(self):
        super().setUp()
        self._old_target = Config.UPLOAD_TARGET
        self._old_archive_en = Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED
        self._old_archive_ch = Config.TELEGRAM_ARCHIVE_CHANNEL_ID
        self._old_auto_del = Config.AUTO_DELETE_AFTER_UPLOAD
        self._old_admin = Config.ADMIN_CHAT_ID
        Config.UPLOAD_TARGET = "youtube"
        Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED = True
        Config.TELEGRAM_ARCHIVE_CHANNEL_ID = -100123
        Config.AUTO_DELETE_AFTER_UPLOAD = False
        Config.ADMIN_CHAT_ID = 0

    def tearDown(self):
        Config.UPLOAD_TARGET = self._old_target
        Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED = self._old_archive_en
        Config.TELEGRAM_ARCHIVE_CHANNEL_ID = self._old_archive_ch
        Config.AUTO_DELETE_AFTER_UPLOAD = self._old_auto_del
        Config.ADMIN_CHAT_ID = self._old_admin
        super().tearDown()

    def _make_bot(self) -> bot_main.JKT48LiveBot:
        with patch("bot.main.TelegramSender"), patch("bot.main.YouTubeChannelPool"):
            return bot_main.JKT48LiveBot()

    def _write_video(self) -> Path:
        p = Path(self._tmp.name) / "replay.mp4"
        p.write_bytes(b"\x00" * 1024)
        return p

    def _insert_session_for_upload(self, path: Path, **extra):
        gid = database.create_merge_group(
            "jkt48_daisy", "Daisy", "2026-09-22T13:00:00+00:00"
        )
        # dua baris segmen berbagi path merge yang sama
        for lid in ("daisy_a", "daisy_b"):
            database.insert_live(
                live_id=lid,
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
            )
            database.update_status(
                lid, "pending_upload", file_path=str(path), **extra
            )
            database.set_session_merge_group(lid, gid)
        return gid

    def _group_rows(self, _gid: int):
        with database._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM live_sessions WHERE member_username = 'jkt48_daisy'"
            ).fetchall()
        return [dict(r) for r in rows]


class TestHandleUploadDualDestination(HandleUploadReadyTestCase):
    def test_archive_runs_even_when_youtube_quota_exhausted(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[111, 112])
        bot.tg.send_message = AsyncMock(return_value=None)
        bot.yt_pool.upload_video = Mock(
            side_effect=YouTubeQuotaExceeded("All channels quota exceeded: ch1(10000)")
        )

        asyncio.run(bot.handle_upload_ready(
            live_id="merged_1",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path),
            platform="idn",
        ))

        bot.tg.upload_video_with_splitting.assert_awaited()
        kwargs = bot.tg.upload_video_with_splitting.await_args.kwargs
        self.assertEqual(kwargs.get("channel_id"), -100123)

        rows = self._group_rows(1)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["status"], "pending_upload")
            self.assertEqual(row["telegram_message_ids"], "111,112")
            self.assertFalse(row["youtube_video_id"])
        self.assertTrue(path.exists())

    def test_retry_skips_completed_destinations(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(
            path,
            youtube_video_id="ytDONE",
            telegram_message_ids="5,6",
        )

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[9])
        bot.tg.send_message = AsyncMock(return_value=77)
        bot.yt_pool.upload_video = Mock(return_value=("SHOULD", "not"))

        asyncio.run(bot.handle_upload_ready(
            live_id="merged_1",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path),
            platform="idn",
        ))

        bot.yt_pool.upload_video.assert_not_called()
        bot.tg.upload_video_with_splitting.assert_not_called()
        # yt+arsip sudah beres tapi belum dinotifikasi → notifikasi tetap dikirim
        bot.tg.send_message.assert_awaited()

        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["youtube_video_id"], "ytDONE")
            self.assertEqual(row["telegram_message_ids"], "5,6")

    def test_full_success_marks_done_and_notifies_once(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[21])
        bot.tg.send_message = AsyncMock(return_value=88)
        bot.yt_pool.upload_video = Mock(return_value=("ytNEW", "ch1"))
        bot.yt_pool.set_thumbnail = Mock(return_value=False)
        # build_collage dipatch agar tidak sentuh file mp4 palsu
        with patch("bot.main.build_collage", return_value=None):
            asyncio.run(bot.handle_upload_ready(
                live_id="merged_1",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
                file_path=str(path),
                platform="idn",
            ))

        bot.yt_pool.upload_video.assert_called_once()
        bot.tg.upload_video_with_splitting.assert_awaited_once()
        bot.tg.send_message.assert_awaited_once()

        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["youtube_video_id"], "ytNEW")
            self.assertEqual(row["telegram_message_ids"], "21")
            self.assertEqual(row["telegram_message_id"], 88)

    def test_yt_done_archive_pending_retries_archive_only(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path, youtube_video_id="ytOLD")

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[31])
        bot.tg.send_message = AsyncMock(return_value=91)
        bot.yt_pool.upload_video = Mock(return_value=("NOPE", "x"))

        asyncio.run(bot.handle_upload_ready(
            live_id="merged_1",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path),
            platform="idn",
        ))

        bot.yt_pool.upload_video.assert_not_called()
        bot.tg.upload_video_with_splitting.assert_awaited_once()
        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["youtube_video_id"], "ytOLD")
            self.assertEqual(row["telegram_message_ids"], "31")


class RetryLoopTestCase(HandleUploadReadyTestCase):
    def test_retry_pending_uploads_processes_each_file_once(self):
        bot = self._make_bot()
        path = self._write_video()
        gid = database.create_merge_group(
            "jkt48_daisy", "Daisy", "2026-09-22T13:00:00+00:00"
        )
        for lid in ("seg1", "seg2", "seg3"):
            database.insert_live(
                live_id=lid,
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
            )
            database.update_status(lid, "pending_upload", file_path=str(path))
            database.set_session_merge_group(lid, gid)

        calls: list[str] = []

        async def fake_handle(**kwargs):
            calls.append(kwargs["live_id"])

        bot.handle_upload_ready = fake_handle  # type: ignore[method-assign]
        asyncio.run(bot.retry_pending_uploads())

        self.assertEqual(len(calls), 1, calls)

    def test_retry_continues_after_one_item_raises(self):
        """Satu item gagal (mis. FloodWait) tidak boleh memblokir item lain."""
        bot = self._make_bot()
        path_a = Path(self._tmp.name) / "a.mp4"
        path_b = Path(self._tmp.name) / "b.mp4"
        path_a.write_bytes(b"\x00" * 64)
        path_b.write_bytes(b"\x00" * 64)
        for lid, path in (("fail_me", path_a), ("ok_me", path_b)):
            database.insert_live(
                live_id=lid,
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
            )
            database.update_status(lid, "pending_upload", file_path=str(path))

        calls: list[str] = []

        async def fake_handle(**kwargs):
            calls.append(kwargs["live_id"])
            if kwargs["live_id"] == "fail_me":
                raise RuntimeError("FloodWait 120s")

        bot.handle_upload_ready = fake_handle  # type: ignore[method-assign]
        asyncio.run(bot.retry_pending_uploads())

        # Kedua item tetap dicoba; urutan mengikuti created_at lalu rowid.
        self.assertEqual(set(calls), {"fail_me", "ok_me"}, calls)
        self.assertEqual(len(calls), 2, calls)

    def test_yt_done_archive_fail_keeps_specific_error(self):
        """Arsip TG gagal: pertahankan error spesifik, jangan ditimpa generik."""
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path, youtube_video_id="ytOLD")

        bot.tg.upload_video_with_splitting = AsyncMock(
            side_effect=RuntimeError("FloodWait 3600s during upload")
        )
        bot.tg.send_message = AsyncMock(return_value=None)
        bot.yt_pool.upload_video = Mock(return_value=("NOPE", "x"))

        asyncio.run(bot.handle_upload_ready(
            live_id="merged_1",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path),
            platform="idn",
        ))

        for row in self._group_rows(1):
            self.assertEqual(row["status"], "pending_upload")
            self.assertEqual(row["youtube_video_id"], "ytOLD")
            err = row["error_message"] or ""
            self.assertIn("FloodWait", err)
            self.assertNotEqual(err, "Telegram archive pending")


if __name__ == "__main__":
    unittest.main()
