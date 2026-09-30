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
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bot import database, main as bot_main
from bot.config import Config
from bot.telegram_sender import TelegramFloodExhausted
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
        # The group is finalized before the callback is invoked in production;
        # mirror that invariant in the fixture so tests exercise the same
        # canonical-path and atomic-marker path as the bot.
        database.close_merge_group(gid, str(path), "merged_1")
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

    def test_concat_failed_group_segments_are_uploaded_independently(self):
        """A failed concat keeps the historical group ID but not shared state."""
        bot = self._make_bot()
        path_a = Path(self._tmp.name) / "segment_a.mp4"
        path_b = Path(self._tmp.name) / "segment_b.mp4"
        path_a.write_bytes(b"a" * 128)
        path_b.write_bytes(b"b" * 128)
        gid = database.create_merge_group(
            "jkt48_daisy", "Daisy", "2026-09-22T13:00:00+00:00"
        )
        for live_id, path in (("fallback_a", path_a), ("fallback_b", path_b)):
            database.insert_live(
                live_id=live_id,
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
            )
            database.update_status(
                live_id, "pending_upload", file_path=str(path)
            )
            database.set_session_merge_group(live_id, gid)
        database.fail_merge_group(gid)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[51])
        bot.tg.send_message = AsyncMock(return_value=61)
        bot.yt_pool.upload_video = Mock(return_value=("ytA", "channel"))

        complete = asyncio.run(bot.handle_upload_ready(
            live_id="fallback_a",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path_a),
            platform="idn",
        ))

        self.assertTrue(complete)
        row_a = database.get_session("fallback_a")
        row_b = database.get_session("fallback_b")
        assert row_a is not None and row_b is not None
        self.assertEqual(row_a["youtube_video_id"], "ytA")
        self.assertEqual(row_a["telegram_message_ids"], "51")
        self.assertEqual(row_a["status"], "done_youtube")
        # The other segment must remain pending and must not inherit markers.
        self.assertEqual(row_b["status"], "pending_upload")
        self.assertIsNone(row_b["youtube_video_id"])
        self.assertIsNone(row_b["telegram_message_ids"])
        self.assertTrue(path_b.exists())

    def test_notification_exception_does_not_requeue_completed_uploads(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[71])
        bot.tg.send_message = AsyncMock(side_effect=RuntimeError("network down"))
        bot.yt_pool.upload_video = Mock(return_value=("ytN", "channel"))
        with patch("bot.main.Config.THUMBNAIL_COLLAGE_ENABLED", False):
            complete = asyncio.run(bot.handle_upload_ready(
                live_id="merged_1",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
                file_path=str(path),
                platform="idn",
            ))

        self.assertTrue(complete)
        self.assertTrue(path.exists())
        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["youtube_video_id"], "ytN")
            self.assertEqual(row["telegram_message_ids"], "71")



class TestTelegramArchiveDisabled(HandleUploadReadyTestCase):
    """
    TELEGRAM_ARCHIVE_UPLOAD_ENABLED=false -> live hanya diunggah ke YouTube.

    Sebelumnya flag ini diabaikan (`telegram_required = True` di main.py),
    jadi tahap Telegram selalu wajib dan YouTube tertahan di belakangnya —
    satu live yang gagal ter-upload ke Telegram tidak pernah tayang sama sekali.
    Sekarang Telegram dilewati dan pipeline selesai begitu YouTube punya video_id.
    """

    def setUp(self):
        super().setUp()
        Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED = False

    def test_pipeline_completes_with_youtube_only(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)

        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[1])
        bot.tg.send_message = AsyncMock(return_value=2)
        bot.yt_pool.upload_video = Mock(return_value=("ytONLY", "ch1"))
        bot.yt_pool.set_thumbnail = Mock(return_value=False)

        with patch("bot.main.Config.THUMBNAIL_COLLAGE_ENABLED", False):
            complete = asyncio.run(bot.handle_upload_ready(
                live_id="merged_1",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
                file_path=str(path),
                platform="idn",
            ))

        self.assertTrue(complete, "pipeline harus selesai tanpa Telegram")
        bot.tg.upload_video_with_splitting.assert_not_called()
        bot.yt_pool.upload_video.assert_called_once()

        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["youtube_video_id"], "ytONLY")
            self.assertFalse(row["telegram_message_ids"])

    def test_missing_file_still_finalizes_when_telegram_disabled(self):
        """File hilang + Telegram nonaktif bukan kegagalan, asal YouTube jadi."""
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path, youtube_video_id="ytDONE")

        # Berkas dihapus bersih seperti kasus AUTO_DELETE setelah sukses.
        path.unlink()

        complete = asyncio.run(bot.handle_upload_ready(
            live_id="merged_1",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
            file_path=str(path),
            platform="idn",
        ))

        self.assertTrue(complete)
        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")


class TestYouTubeDeadLetter(HandleUploadReadyTestCase):
    """
    Upload YouTube yang hopeless harus jadi terminal, bukan retry selamanya.

    Ini kunci pembubaran deadlock disk: file 1-2 GB yang `pending_upload`
    tanpa batas percobaan tidak pernah terhapus, dan menumpuknya itulah yang
    membuat disk guard memblokir seluruh rekaman.
    """

    def setUp(self):
        super().setUp()
        self._old_max = Config.YOUTUBE_UPLOAD_MAX_FAILURES
        Config.YOUTUBE_UPLOAD_MAX_FAILURES = 3

    def tearDown(self):
        Config.YOUTUBE_UPLOAD_MAX_FAILURES = self._old_max
        super().tearDown()

    def _fail_upload(self, bot, path) -> None:
        bot.tg.upload_video_with_splitting = AsyncMock(return_value=[9])
        bot.tg.send_message = AsyncMock(return_value=1)
        bot.yt_pool.upload_video = Mock(
            side_effect=YouTubeQuotaExceeded("All channels quota exceeded")
        )
        with patch("bot.main.Config.THUMBNAIL_COLLAGE_ENABLED", False):
            asyncio.run(bot.handle_upload_ready(
                live_id="merged_1",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
                file_path=str(path),
                platform="idn",
            ))

    def test_repeated_youtube_failures_mark_session_failed(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)

        for _ in range(3):
            self._fail_upload(bot, path)

        for row in self._group_rows(1):
            self.assertEqual(
                row["status"], "failed",
                "upload YouTube hopeless harus dead-letter, bukan pending selamanya",
            )
            self.assertTrue(path.exists(), "file boleh tinggal, tapi harus terminal")

    def test_failures_reset_after_successful_upload(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)

        self._fail_upload(bot, path)
        self.assertEqual(bot._yt_fail_count.get("merged_1"), 1)

        bot.yt_pool.upload_video = Mock(return_value=("ytOK", "ch1"))
        bot.yt_pool.set_thumbnail = Mock(return_value=False)
        with patch("bot.main.Config.THUMBNAIL_COLLAGE_ENABLED", False):
            asyncio.run(bot.handle_upload_ready(
                live_id="merged_1",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
                file_path=str(path),
                platform="idn",
            ))

        self.assertIsNone(bot._yt_fail_count.get("merged_1"))


class FloodCooldownTestCase(HandleUploadReadyTestCase):
    """File yang kehabisan jatah flood harus DIDIAMKAN, bukan digiling terus.

    Bukti dari VPS 26 Sep 2026: backoff sampai 240 detik tidak pernah
    mengubah hasil (upload selalu flood di 0,1%), sementara tiap percobaan
    membuang ~819 MB. Tanpa cooldown, satu file memblokir seluruh antrean
    dan membuang ~5 GB per siklus retry.
    """

    def _make_pending(self, live_id: str) -> Path:
        path = Path(self._tmp.name) / f"{live_id}.mp4"
        path.write_bytes(b"\x00" * 64)
        database.insert_live(
            live_id=live_id,
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-09-22T13:00:00+00:00",
        )
        database.update_status(live_id, "pending_upload", file_path=str(path))
        return path

    def _run_worker(self, bot, attempted: list[str], handler=None) -> None:
        async def _handle(**kwargs):
            attempted.append(kwargs["live_id"])
            if handler is not None:
                return await handler(**kwargs)
            return None

        bot.handle_upload_ready = _handle  # type: ignore[method-assign]
        asyncio.run(bot.retry_pending_uploads())

    def test_flood_marks_cooldown_and_continues_to_next_file(self):
        bot = self._make_bot()
        bot._flood_cooldown = {}
        self._make_pending("flooded")
        self._make_pending("healthy")

        attempted: list[str] = []

        async def handler(**kwargs):
            if kwargs["live_id"] == "flooded":
                raise TelegramFloodExhausted("flood 6/6 untuk 819 MB")

        old_cooldown = Config.TELEGRAM_FLOOD_COOLDOWN_MINUTES
        Config.TELEGRAM_FLOOD_COOLDOWN_MINUTES = 90
        try:
            self._run_worker(bot, attempted, handler)
            self.assertEqual(set(attempted), {"flooded", "healthy"})
            self.assertIn("flooded", bot._flood_cooldown)

            # Siklus berikutnya: file ber-cooldown dilewati, sisanya dicoba.
            attempted.clear()
            self._run_worker(bot, attempted)
            self.assertNotIn(
                "flooded", attempted,
                "file ber-cooldown tidak boleh diulang di siklus berikutnya",
            )
            self.assertIn("healthy", attempted)
        finally:
            Config.TELEGRAM_FLOOD_COOLDOWN_MINUTES = old_cooldown

    def test_cooldown_expires_and_file_is_retried_again(self):
        bot = self._make_bot()
        bot._flood_cooldown = {"gone": time.monotonic() - 10}
        self._make_pending("gone")

        attempted: list[str] = []
        self._run_worker(bot, attempted)
        self.assertIn("gone", attempted, "cooldown yang sudah lewat harus dilepas")
        self.assertNotIn("gone", bot._flood_cooldown)


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


class TestStuckUploadRecovery(HandleUploadReadyTestCase):
    """Baris 'uploading_telegram' yang ditinggalkan harus kembali ke antrean.

    Insiden 30 Sep 2026: admin menerima notifikasi "UPLOAD TELEGRAM" untuk
    jkt48_michie_1790670389, lalu tidak ada notifikasi lanjutan sama sekali
    selama 28 jam dan video tidak pernah muncul di channel. Barisnya terkunci
    di 'uploading_telegram' — status yang TIDAK dibaca
    ``get_all_pending_videos``, jadi tidak pernah di-retry dan tidak pernah
    diberi tahu hasilnya. Recovery periodik di ``retry_pending_uploads`` +
    watchdog upload yang menggantung adalah dua Flamengo penutupnya.
    """

    def test_stale_uploading_row_returns_to_queue(self):
        bot = self._make_bot()
        path = self._write_video()
        gid = self._insert_session_for_upload(path)
        database.update_status("daisy_a", "uploading_telegram", file_path=str(path))

        calls: list[str] = []

        async def fake_handle(**kwargs):
            calls.append(kwargs["live_id"])

        bot.handle_upload_ready = fake_handle  # type: ignore[method-assign]
        asyncio.run(bot.retry_pending_uploads())

        self.assertTrue(
            calls, "baris uploading_telegram yang tertinggal harus masuk antrean lagi"
        )
        statuses = {row["live_id"]: row["status"] for row in self._group_rows(gid)}
        self.assertNotEqual(
            statuses.get("daisy_a"), "uploading_telegram",
            "baris tidak boleh tetap terkunci di status in-flight",
        )

    def test_active_upload_is_not_recovered(self):
        """Upload yang SEDANG berjalan tidak boleh di-reset (jd upload ganda)."""
        bot = self._make_bot()
        path = self._write_video()
        gid = self._insert_session_for_upload(path)
        database.update_status("daisy_a", "uploading_telegram", file_path=str(path))

        calls: list[str] = []

        async def fake_handle(**kwargs):
            calls.append(kwargs["live_id"])

        bot.handle_upload_ready = fake_handle  # type: ignore[method-assign]
        # Simulasikan upload daisy_a yang masih berjalan di proses ini.
        import time as _time
        bot._active_uploads["daisy_a"] = (None, _time.monotonic())
        try:
            asyncio.run(bot.retry_pending_uploads())
        finally:
            bot._active_uploads.pop("daisy_a", None)

        self.assertNotIn("daisy_a", calls, "upload aktif tidak boleh di-retry")
        statuses = {row["live_id"]: row["status"] for row in self._group_rows(gid)}
        self.assertEqual(statuses.get("daisy_a"), "uploading_telegram")

    def test_watchdog_cancels_upload_that_exceeds_stale_window(self):
        """Upload yang menggantung > TELEGRAM_UPLOAD_STALE_MINUTES dibatalkan."""
        bot = self._make_bot()
        bot._active_uploads["merged_x"] = (None, 0.0)

        async def run():
            with patch.object(Config, "TELEGRAM_UPLOAD_STALE_MINUTES", 0):
                return bot._cancel_stuck_uploads()

        stuck = asyncio.run(run())
        self.assertEqual(stuck, ["merged_x"])
        self.assertNotIn(
            "merged_x", bot._active_uploads,
            "baris harus bisa dipulihkan recovery setelah dibatalkan",
        )

    def test_watchdog_keeps_upload_within_window(self):
        bot = self._make_bot()
        import time as _time
        bot._active_uploads["merged_ok"] = (None, _time.monotonic())

        async def run():
            with patch.object(Config, "TELEGRAM_UPLOAD_STALE_MINUTES", 90):
                return bot._cancel_stuck_uploads()

        self.assertEqual(asyncio.run(run()), [])
        self.assertIn("merged_ok", bot._active_uploads)


class TestTelegramGivesUp(HandleUploadReadyTestCase):
    """Arsip Telegram menyerah → file TETAP dihapus, video tetap tayang.

    Permintaan pemilik (30 Sep 2026): YouTube adalah tujuan wajib, Telegram
    hanya bonus. Kalau Telegram gagal terus, file tidak boleh menumpuk di VPS;
    website tetap menayangkannya, hanya tanpa tombol download karena tidak ada
    arsip untuk disalin.
    """

    def setUp(self):
        super().setUp()
        self._old_auto_del = Config.AUTO_DELETE_AFTER_UPLOAD
        self._old_max_fail = Config.TELEGRAM_UPLOAD_MAX_FAILURES
        Config.AUTO_DELETE_AFTER_UPLOAD = True
        Config.TELEGRAM_UPLOAD_MAX_FAILURES = 2

    def tearDown(self):
        Config.AUTO_DELETE_AFTER_UPLOAD = self._old_auto_del
        Config.TELEGRAM_UPLOAD_MAX_FAILURES = self._old_max_fail
        super().tearDown()

    def _failing_telegram(self, bot, message="flood wait"):
        bot.tg.upload_video_with_splitting = AsyncMock(
            side_effect=RuntimeError(message)
        )
        bot.tg.send_message = AsyncMock(return_value=None)
        bot.yt_pool.upload_video = Mock(return_value=("ytOK", "ch1"))
        bot.yt_pool.set_thumbnail = Mock(return_value=False)

    def _run(self, bot, path):
        with patch("bot.main.Config.THUMBNAIL_COLLAGE_ENABLED", False):
            return asyncio.run(bot.handle_upload_ready(
                live_id="merged_1",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-09-22T13:00:00+00:00",
                file_path=str(path),
                platform="idn",
            ))

    def test_file_deleted_after_telegram_gives_up(self):
        bot = self._make_bot()
        path = self._write_video()
        gid = self._insert_session_for_upload(path)
        self._failing_telegram(bot)

        # Percobaan 1 dan 2: Telegram gagal, YouTube tetap naik di percobaan 1.
        self._run(bot, path)
        self.assertTrue(
            path.exists(),
            "selama Telegram masih dicoba, file tidak boleh dihapus",
        )
        self._run(bot, path)

        self.assertFalse(
            path.exists(),
            "YouTube sukses + Telegram menyerah → file harus dihapus dari VPS",
        )
        rows = self._group_rows(gid)
        for row in rows:
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["youtube_video_id"], "ytOK")
            self.assertFalse(
                row["telegram_message_ids"],
                "tanpa arsip, website tidak menampilkan tombol download",
            )
            self.assertEqual(
                row["telegram_gave_up"], 1,
                "marker menyerah harus durable agar tidak retry lagi",
            )

    def test_pipeline_reports_complete_after_give_up(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)
        self._failing_telegram(bot)

        self._run(bot, path)
        complete = self._run(bot, path)

        self.assertTrue(
            complete,
            "YouTube wajib + Telegram menyerah = pipeline selesai",
        )

    def test_gave_up_row_is_not_retried_telegram_again(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)
        self._failing_telegram(bot)

        self._run(bot, path)
        self._run(bot, path)
        calls_after_give_up = bot.tg.upload_video_with_splitting.await_count

        # Siklus berikutnya: marker gave_up harus mencegah percobaan baru.
        bot.tg.upload_video_with_splitting.reset_mock()
        self._run(bot, path)
        self.assertEqual(
            bot.tg.upload_video_with_splitting.await_count, 0,
            "Telegram yang sudah menyerah tidak boleh dicoba lagi",
        )
        self.assertEqual(calls_after_give_up, 2)

    def test_temporary_telegram_failure_keeps_file_for_retry(self):
        """Kegagalan pertama (belum menyerah) tetap menahan file agar arsip bisa comeback."""
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)
        self._failing_telegram(bot)

        self._run(bot, path)

        self.assertTrue(path.exists())
        rows = self._group_rows(1)
        for row in rows:
            self.assertEqual(row["status"], "pending_upload")
            self.assertEqual(row["youtube_video_id"], "ytOK")
            self.assertEqual(row["telegram_gave_up"], 0)

    def test_flood_exhaustion_also_reaches_give_up(self):
        """Flood yang terus-menerus juga harus akhirnya menyerah (bukan menggantung).

        `TelegramFloodExhausted` sebelumnya dilempar keluar sebelum penghitung
        kegagalan berjalan, jadi file yang terus kena flood tidak pernah bisa
        mencapai keputusan menyerah dan tertahan di antrean tanpa henti.
        """
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)
        bot.tg.upload_video_with_splitting = AsyncMock(
            side_effect=TelegramFloodExhausted("flood 900s")
        )
        bot.tg.send_message = AsyncMock(return_value=None)
        bot.yt_pool.upload_video = Mock(return_value=("ytOK", "ch1"))
        bot.yt_pool.set_thumbnail = Mock(return_value=False)

        self._run(bot, path)
        self.assertTrue(path.exists(), "masih ada jatah retry")
        self._run(bot, path)

        self.assertFalse(
            path.exists(),
            "flood yang tidak mau berhenti harus berakhir surrender, bukan menggantung",
        )
        for row in self._group_rows(1):
            self.assertEqual(row["status"], "done_youtube")
            self.assertEqual(row["telegram_gave_up"], 1)


