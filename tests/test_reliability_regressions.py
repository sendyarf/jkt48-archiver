"""Regression tests for live HLS recording and non-blocking upload workers."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot import database, downloader, upload_pending
from bot.config import Config
from bot.hls_discovery import HLSDiscovery
from bot.hls_monitor import HLSMonitor
from bot.main import JKT48LiveBot
from tests.test_pending_upload import HandleUploadReadyTestCase


class _Response:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeHLSClient:
    is_closed = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        return self.responses.pop(0) if self.responses else _Response(404)


class HLSMonitorRetryTest(unittest.TestCase):
    def test_get_404_then_playlist_is_live(self):
        monitor = HLSMonitor(probe_attempts=2, retry_delay_seconds=0)
        client = _FakeHLSClient([
            _Response(404, "not found"),
            _Response(200, "#EXTM3U\n#EXT-X-VERSION:3\n"),
        ])
        monitor._client = client  # type: ignore[assignment]

        self.assertTrue(asyncio.run(monitor.is_stream_active("https://hls.test/live.m3u8")))
        self.assertEqual(len(client.calls), 2)

    def test_http_200_without_playlist_is_not_live(self):
        monitor = HLSMonitor(probe_attempts=3, retry_delay_seconds=0)
        client = _FakeHLSClient([_Response(200, "<html>error</html>")])
        monitor._client = client  # type: ignore[assignment]

        self.assertFalse(asyncio.run(monitor.is_stream_active("https://hls.test/live.m3u8")))
        self.assertEqual(len(client.calls), 1)


class _FakeProcess:
    def __init__(self, returncode: int = 1, output: bytes = b""):
        self.returncode = None
        self._returncode = returncode
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()
        self._output = output

    async def wait(self):
        self.returncode = self._returncode
        return self._returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class DownloaderCommandTest(unittest.TestCase):
    def test_ffmpeg_records_fragmented_mp4_and_keeps_retry(self):
        commands = []

        async def fake_exec(*cmd, **kwargs):
            commands.append(list(cmd))
            return _FakeProcess()

        async def run():
            with (
                patch("bot.downloader.shutil.which", return_value="/usr/bin/ffmpeg"),
                patch("bot.downloader._wait_for_hls_url", return_value=True),
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            ):
                with self.assertRaises(downloader.DownloadError):
                    await downloader.download_stream(
                        "https://hls.test/live.m3u8",
                        "jkt48_test",
                        "live_test",
                        max_empty_retries=1,
                        empty_retry_delay=0,
                    )

        asyncio.run(run())
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertNotIn("--no-part", command)
        self.assertEqual(command[command.index("-f") + 1], "mp4")
        self.assertIn("+frag_keyframe+empty_moov+default_base_moof", command)
        self.assertTrue(command[-1].endswith(".mp4"))

    def test_nonempty_output_wins_over_unexpected_ffmpeg_exit(self):
        async def fake_exec(*cmd, **kwargs):
            Path(cmd[-1]).write_bytes(
                b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2"
                b"\x00\x00\x00\x10moof"
            )
            return _FakeProcess(returncode=42)

        async def run():
            with (
                patch("bot.downloader.shutil.which", return_value="/usr/bin/ffmpeg"),
                patch("bot.downloader._wait_for_hls_url", return_value=True),
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            ):
                return await downloader.download_stream(
                    "https://hls.test/live.m3u8",
                    "jkt48_test",
                    "live_partial",
                    max_empty_retries=1,
                    empty_retry_delay=0,
                )

        with tempfile.TemporaryDirectory() as directory:
            old_directory = Config.DOWNLOAD_DIR
            Config.DOWNLOAD_DIR = directory
            try:
                result = asyncio.run(run())
                self.assertTrue(result.exists())
                self.assertIn(b"ftyp", result.read_bytes())
            finally:
                Config.DOWNLOAD_DIR = old_directory


    def test_header_only_mp4_is_rejected(self):
        """Header ffmpeg tanpa fragment media bukan rekaman yang bisa diupload."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "header-only.mp4"
            path.write_bytes(
                b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2"
                b"\x00\x00\x00\x10moov"
            )
            self.assertFalse(downloader._is_usable_recording(path))

    def test_nonempty_non_mp4_error_body_is_rejected(self):
        async def fake_exec(*cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"HTTP 404 error page")
            return _FakeProcess(returncode=1)

        async def run():
            with (
                patch("bot.downloader.shutil.which", return_value="/usr/bin/ffmpeg"),
                patch("bot.downloader._wait_for_hls_url", return_value=True),
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            ):
                with self.assertRaises(downloader.DownloadError):
                    await downloader.download_stream(
                        "https://hls.test/live.m3u8",
                        "jkt48_test",
                        "live_error_body",
                        max_empty_retries=1,
                        empty_retry_delay=0,
                    )

        with tempfile.TemporaryDirectory() as directory:
            old_directory = Config.DOWNLOAD_DIR
            Config.DOWNLOAD_DIR = directory
            try:
                asyncio.run(run())
            finally:
                Config.DOWNLOAD_DIR = old_directory


class BackgroundWorkerTest(unittest.TestCase):
    def test_pending_upload_scheduler_is_single_flight(self):
        async def run():
            bot = object.__new__(JKT48LiveBot)
            bot._retry_task = None
            gate = asyncio.Event()
            calls = []

            async def worker():
                calls.append("start")
                await gate.wait()
                calls.append("stop")

            bot.retry_pending_uploads = worker  # type: ignore[method-assign]
            bot._schedule_pending_uploads()
            first = bot._retry_task
            bot._schedule_pending_uploads()
            self.assertIs(first, bot._retry_task)
            gate.set()
            await first
            self.assertEqual(calls, ["start", "stop"])

        asyncio.run(run())



    def test_hls_refresh_scheduler_is_single_flight(self):
        async def run(self):
            bot = object.__new__(JKT48LiveBot)
            bot._hls_refresh_task = None
            gate = asyncio.Event()
            calls = []

            class FakeDiscovery:
                async def discover_missing_members(self):
                    calls.append("discover")
                    return []

                async def refresh_known_members(self):
                    calls.append("refresh")
                    await gate.wait()
                    return []

            bot.hls_discovery = FakeDiscovery()
            with patch("bot.main.database.get_members_without_hls", return_value=[]):
                bot._schedule_hls_refresh()
                first = bot._hls_refresh_task
                bot._schedule_hls_refresh()
                self.assertIs(first, bot._hls_refresh_task)
                gate.set()
                await first
            self.assertEqual(calls, ["refresh"])

        asyncio.run(run(self))




class HLSDiscoveryKnownMemberTest(unittest.TestCase):
    def test_refresh_updates_known_member_url(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                old_db = Config.DB_PATH
                Config.DB_PATH = str(Path(directory) / "hls.db")
                try:
                    database.init_db()
                    database.register_members_if_not_exists(["jkt48_fritzy"])
                    database.update_member_hls_url(
                        "jkt48_fritzy", "https://old.example/live.m3u8", "Fritzy"
                    )
                    discovery = HLSDiscovery()
                    discovery.fetch_active_lives = AsyncMock(return_value=[{
                        "creator": {"username": "jkt48_fritzy", "name": "Fritzy"},
                        "playback_url": "https://new.example/live.m3u8",
                    }])
                    result = await discovery.refresh_known_members()
                    self.assertEqual(result[0]["username"], "jkt48_fritzy")
                    self.assertEqual(
                        database.get_member_hls("jkt48_fritzy")["hls_url"],
                        "https://new.example/live.m3u8",
                    )
                finally:
                    Config.DB_PATH = old_db

        asyncio.run(run())


class IDNRecordingRefreshTest(HandleUploadReadyTestCase):
    def test_retry_callback_refreshes_stale_member_url(self):
        bot = self._make_bot()
        old_url = "https://old.example/idn.m3u8"
        new_url = "https://new.example/idn.m3u8"
        video = Path(self._tmp.name) / "idn_refresh.mp4"
        video.write_bytes(b"video")
        database.register_members_if_not_exists(["jkt48_daisy"])
        database.update_member_hls_url("jkt48_daisy", old_url, "Daisy")
        captured = {}

        async def fake_lookup(username):
            return {
                "slug": "haii-260924000000",
                "title": "haii",
                "playback_url": new_url,
            }

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            captured["initial_url"] = hls_url
            captured["refresher"] = kwargs.get("url_refresher")
            self.assertEqual(await captured["refresher"](), new_url)
            return video

        async def fake_add_segment(**kwargs):
            captured["segment"] = kwargs

        bot._fetch_member_live = fake_lookup
        bot.merge_mgr.add_segment = fake_add_segment
        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_member_task(
                {
                    "username": "jkt48_daisy",
                    "display_name": "Daisy",
                    "hls_url": old_url,
                },
                "daisy_live_refresh",
            ))

        self.assertEqual(captured["initial_url"], old_url)
        self.assertEqual(
            database.get_member_hls("jkt48_daisy")["hls_url"],
            new_url,
        )
        row = database.get_session("daisy_live_refresh")
        assert row is not None
        self.assertEqual(row["status"], "segment_done")


class UploadDoesNotBlockEventLoopTest(HandleUploadReadyTestCase):
    def test_synchronous_youtube_upload_runs_in_worker_thread(self):
        bot = self._make_bot()
        path = self._write_video()
        self._insert_session_for_upload(path)
        old_archive = Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED
        old_thumbnail = Config.THUMBNAIL_COLLAGE_ENABLED
        Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED = True
        Config.THUMBNAIL_COLLAGE_ENABLED = False
        try:
            bot.tg.upload_video_with_splitting = AsyncMock(return_value=[301])
            bot.tg.send_message = AsyncMock(return_value=302)

            def slow_upload(*args, **kwargs):
                time.sleep(0.20)
                return "video-test", "channel-test"

            bot.yt_pool.upload_video = slow_upload  # type: ignore[method-assign]

            async def run():
                task = asyncio.create_task(bot.handle_upload_ready(
                    live_id="merged_1",
                    member_username="jkt48_daisy",
                    member_name="Daisy",
                    started_at="2026-09-22T13:00:00+00:00",
                    file_path=str(path),
                    platform="idn",
                ))
                await asyncio.sleep(0.02)
                self.assertFalse(task.done(), "event loop tertahan oleh upload sinkron")
                await task

            asyncio.run(run())
        finally:
            Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED = old_archive
            Config.THUMBNAIL_COLLAGE_ENABLED = old_thumbnail


class InterruptedUploadRecoveryTest(unittest.TestCase):
    def test_singular_notification_marker_is_not_archive_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "recovery.db")
            try:
                database.init_db()
                database.insert_live("notice_only", "jkt48_test", "Test")
                database.update_status(
                    "notice_only",
                    "pending_upload",
                    file_path=str(Path(directory) / "missing.mp4"),
                    telegram_message_id=42,
                )
                self.assertEqual(database.recover_interrupted_uploads(), 1)
                row = database.get_session("notice_only")
                self.assertEqual(row["status"], "failed")
                self.assertNotIn("Recovered", row["error_message"] or "")
            finally:
                Config.DB_PATH = old_db

    def test_effective_state_uses_finalized_group_marker(self):
        """Finalized group is one atomic destination, so group markers are shared."""
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "finalized-state.db")
            try:
                database.init_db()
                gid = database.create_merge_group(
                    "jkt48_test", "Test", "2026-09-24T00:00:00+00:00"
                )
                for live_id in ("a", "b"):
                    database.insert_live(live_id, "jkt48_test", "Test")
                    database.set_session_merge_group(live_id, gid)
                database.set_session_fields(
                    group_id=gid,
                    telegram_message_ids="10,11",
                    youtube_video_id="yt-final",
                )
                database.close_merge_group(gid, str(Path(directory) / "merged.mp4"), "merged_1")

                state = upload_pending._get_effective_upload_state("a")
                self.assertEqual(state["telegram_message_ids"], "10,11")
                self.assertEqual(state["youtube_video_id"], "yt-final")
            finally:
                Config.DB_PATH = old_db

    def test_concat_failed_group_does_not_share_markers_between_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "failed-state.db")
            try:
                database.init_db()
                gid = database.create_merge_group(
                    "jkt48_test", "Test", "2026-09-24T00:00:00+00:00"
                )
                for live_id in ("a", "b"):
                    database.insert_live(live_id, "jkt48_test", "Test")
                    database.set_session_merge_group(live_id, gid)
                database.update_status(
                    "a",
                    "pending_upload",
                    telegram_message_ids="10",
                    youtube_video_id="yt-a",
                )
                database.fail_merge_group(gid)

                state_a = upload_pending._get_effective_upload_state("a")
                state_b = upload_pending._get_effective_upload_state("b")
                self.assertEqual(state_a["telegram_message_ids"], "10")
                self.assertEqual(state_a["youtube_video_id"], "yt-a")
                self.assertFalse(state_b["telegram_message_ids"])
                self.assertFalse(state_b["youtube_video_id"])
            finally:
                Config.DB_PATH = old_db

    def test_untracked_scan_registers_durable_row(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            old_download_dir = Config.DOWNLOAD_DIR
            Config.DB_PATH = str(Path(directory) / "scan.db")
            Config.DOWNLOAD_DIR = directory
            try:
                database.init_db()
                video = Path(directory) / "jkt48_test_replay.mp4"
                video.write_bytes(b"video")
                item = {
                    "source": "disk",
                    "live_id": video.stem,
                    "member_username": "jkt48_test",
                    "member_name": "Test",
                    "started_at": "2026-09-24T00:00:00+00:00",
                    "platform": "idn",
                    "file_path": video,
                    "size_bytes": video.stat().st_size,
                    "db_status": "untracked",
                }
                live_id = upload_pending._register_untracked_file(item)
                row = database.get_session(live_id)
                self.assertIsNotNone(row)
                self.assertEqual(row["file_path"], str(video.resolve()))
                self.assertEqual(row["status"], "pending_upload")
                self.assertEqual(
                    upload_pending._register_untracked_file(item),
                    live_id,
                )
                self.assertEqual(
                    len(database.get_all_pending_videos()),
                    1,
                )
            finally:
                Config.DB_PATH = old_db
                Config.DOWNLOAD_DIR = old_download_dir

    def test_merge_group_recovery_uses_any_remaining_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "group-recovery.db")
            try:
                database.init_db()
                gid = database.create_merge_group(
                    "jkt48_test", "Test", "2026-09-24T00:00:00+00:00"
                )
                merged = Path(directory) / "member_merged_1.mp4"
                merged.write_bytes(b"merged-video")
                missing = Path(directory) / "missing-segment.mp4"

                for live_id, status, path in (
                    ("in_flight", "uploading_youtube", missing),
                    ("queued", "pending_upload", merged),
                ):
                    database.insert_live(
                        live_id, "jkt48_test", "Test",
                        "2026-09-24T00:00:00+00:00",
                    )
                    database.update_status(
                        live_id, status, file_path=str(path)
                    )
                    database.set_session_merge_group(live_id, gid)
                database.close_merge_group(gid, str(merged), "merged_1")

                self.assertEqual(database.recover_interrupted_uploads(), 1)
                with database._get_conn() as conn:
                    rows = conn.execute(
                        "SELECT status FROM live_sessions WHERE merge_group_id = ?",
                        (gid,),
                    ).fetchall()
                self.assertEqual({row["status"] for row in rows}, {"pending_upload"})
                self.assertTrue(merged.exists())
            finally:
                Config.DB_PATH = old_db

    def test_finalized_group_never_falls_back_to_partial_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "missing-merged.db")
            try:
                database.init_db()
                gid = database.create_merge_group(
                    "jkt48_test", "Test", "2026-09-24T00:00:00+00:00"
                )
                segment = Path(directory) / "segment-a.mp4"
                segment.write_bytes(b"partial-segment")
                missing_merged = Path(directory) / "member_merged_1.mp4"
                for live_id, status in (("a", "uploading_telegram"), ("b", "pending_upload")):
                    database.insert_live(
                        live_id, "jkt48_test", "Test",
                        "2026-09-24T00:00:00+00:00",
                    )
                    database.update_status(
                        live_id, status, file_path=str(segment)
                    )
                    database.set_session_merge_group(live_id, gid)
                database.close_merge_group(gid, str(missing_merged), "merged_1")

                self.assertEqual(
                    database.get_available_upload_path("a", gid),
                    str(missing_merged),
                )
                self.assertEqual(database.recover_interrupted_uploads(), 2)
                with database._get_conn() as conn:
                    rows = conn.execute(
                        "SELECT status, error_message FROM live_sessions "
                        "WHERE merge_group_id = ?",
                        (gid,),
                    ).fetchall()
                self.assertEqual({row["status"] for row in rows}, {"failed"})
                self.assertTrue(segment.exists(), "segment tidak boleh diprom jadi merged")
            finally:
                Config.DB_PATH = old_db


    def test_close_merge_group_promotes_segments_to_canonical_ready_state(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "close-finalized.db")
            try:
                database.init_db()
                gid = database.create_merge_group(
                    "jkt48_test", "Test", "2026-09-24T00:00:00+00:00"
                )
                merged = Path(directory) / "member_merged_2.mp4"
                merged.write_bytes(b"merged-video")
                for live_id in ("a", "b"):
                    database.insert_live(
                        live_id, "jkt48_test", "Test",
                        "2026-09-24T00:00:00+00:00",
                    )
                    database.update_status(
                        live_id, "segment_done", file_path="stale-segment.mp4"
                    )
                    database.set_session_merge_group(live_id, gid)

                database.close_merge_group(gid, str(merged), f"merged_{gid}")

                group = database.get_merge_group(gid)
                self.assertEqual(group["status"], "done")
                self.assertEqual(group["merged_file_path"], str(merged))
                rows = database.get_merge_segments(gid)
                self.assertEqual({row["status"] for row in rows}, {"download_complete"})
                self.assertEqual({row["file_path"] for row in rows}, {str(merged)})
                queued = database.get_all_pending_videos()
                self.assertEqual([row["file_path"] for row in queued], [str(merged)])
            finally:
                Config.DB_PATH = old_db

    def test_legacy_finalized_group_normalizes_segments_to_ready_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "legacy-finalized.db")
            try:
                database.init_db()
                gid = database.create_merge_group(
                    "jkt48_test", "Test", "2026-09-24T00:00:00+00:00"
                )
                merged = Path(directory) / "member_merged_1.mp4"
                merged.write_bytes(b"merged-video")
                for live_id in ("a", "b"):
                    database.insert_live(
                        live_id, "jkt48_test", "Test",
                        "2026-09-24T00:00:00+00:00",
                    )
                    database.update_status(
                        live_id, "segment_done", file_path="stale-segment.mp4"
                    )
                    database.set_session_merge_group(live_id, gid)

                # Simulate data written by an older finalization path that set
                # the group artifact but did not promote its session rows.
                with database._get_conn() as conn:
                    conn.execute(
                        """UPDATE merge_groups
                           SET status = 'done', merged_file_path = ?, merged_live_id = ?
                           WHERE id = ?""",
                        (str(merged), f"merged_{gid}", gid),
                    )

                self.assertEqual(database.recover_interrupted_uploads(), 2)
                rows = database.get_merge_segments(gid)
                self.assertEqual({row["status"] for row in rows}, {"download_complete"})
                self.assertEqual({row["file_path"] for row in rows}, {str(merged)})
                queued = database.get_all_pending_videos()
                self.assertEqual([row["file_path"] for row in queued], [str(merged)])
            finally:
                Config.DB_PATH = old_db

    def test_cleanup_never_deletes_pending_upload(self):
        from bot import status as status_viewer

        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "cleanup.db")
            try:
                database.init_db()
                pending = Path(directory) / "youtube-quota-wait.mp4"
                pending.write_bytes(b"video")
                database.insert_live(
                    "pending_1", "jkt48_test", "Test",
                    "2026-09-24T00:00:00+00:00",
                )
                database.update_status(
                    "pending_1", "pending_upload", file_path=str(pending),
                    file_size_bytes=pending.stat().st_size,
                    error_message="YouTube quota exceeded",
                )
                with database._get_conn() as conn:
                    conn.execute(
                        "UPDATE live_sessions SET created_at = '2000-01-01 00:00:00' "
                        "WHERE live_id = 'pending_1'"
                    )

                status_viewer.run_cleanup(hours=0)
                self.assertTrue(pending.exists())
                self.assertEqual(
                    database.get_session("pending_1")["status"],
                    "pending_upload",
                )
            finally:
                Config.DB_PATH = old_db

    def test_existing_file_is_returned_to_retry_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = Config.DB_PATH
            Config.DB_PATH = str(Path(directory) / "recovery.db")
            try:
                database.init_db()
                video = Path(directory) / "recording.mp4"
                video.write_bytes(b"video")
                database.insert_live("upload_1", "jkt48_test", "Test")
                database.update_status(
                    "upload_1", "uploading_youtube", file_path=str(video)
                )
                self.assertEqual(database.recover_interrupted_uploads(), 1)
                row = database.get_session("upload_1")
                self.assertEqual(row["status"], "pending_upload")
                self.assertIn("Recovered", row["error_message"])
            finally:
                Config.DB_PATH = old_db
