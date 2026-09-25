"""
Unit tests for recovery, video splitting, Telegram multipart, and config.

test_telegram_upload.py - Unit tests for video splitter, telegram captions, and config.
"""
import asyncio
import os
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from bot.config import Config
from bot.telegram_sender import (
    TelegramSender,
    build_telegram_video_caption,
    build_youtube_notification,
    _format_size,
)
from bot.video_splitter import (
    VideoPart,
    cleanup_video_parts,
    generate_thumbnail,
    get_video_metadata,
    split_video_if_needed,
)


class TestConfigAndCaptions(unittest.TestCase):
    def test_config_defaults(self):
        self.assertIn(Config.UPLOAD_TARGET, ["telegram", "youtube"])
        self.assertGreaterEqual(Config.TELEGRAM_MAX_FILE_SIZE_MB, 500)

    def test_single_part_caption(self):
        caption = build_telegram_video_caption(
            member_name="Ella JKT48",
            member_username="jkt48_ella",
            started_at="2026-09-16T12:00:00Z",
            live_title="Live Santai",
            part_number=1,
            total_parts=1,
            file_size_bytes=1024 * 1024 * 500,  # 500 MB
        )
        self.assertIn("Ella JKT48", caption)
        self.assertIn("@.jkt48_ella", caption)
        self.assertIn("Live Santai", caption)
        self.assertIn("500.0 MB", caption)
        self.assertNotIn("Part 1/", caption)
        # Tanpa platform (baris lama) → tetap IDN.
        self.assertIn("IDN LIVE REPLAY", caption)

    def test_multi_part_caption(self):
        caption = build_telegram_video_caption(
            member_name="Lulu JKT48",
            member_username="jkt48_lulu",
            started_at="2026-09-16T15:30:00Z",
            live_title="Makan Bareng",
            part_number=2,
            total_parts=3,
            file_size_bytes=1024 * 1024 * 1800,  # 1.8 GB
        )
        self.assertIn("[Part 2/3]", caption)
        self.assertIn("Part 2 dari 3", caption)
        self.assertIn("Lulu JKT48", caption)
        self.assertIn("Makan Bareng", caption)

    def test_showroom_caption_uses_showroom_header(self):
        """Rekaman Showroom tidak boleh dilabeli IDN (regresi laporan 19 Sep 2026)."""
        caption = build_telegram_video_caption(
            member_name="Heidi JKT48",
            member_username="jkt48_heidi",
            started_at="2026-09-19T12:30:00Z",
            live_title="",
            part_number=1,
            total_parts=1,
            file_size_bytes=0,
            platform="showroom",
        )
        self.assertIn("SHOWROOM LIVE REPLAY", caption)
        self.assertNotIn("IDN", caption)
        self.assertIn("Heidi JKT48", caption)

    def test_explicit_idn_platform_caption(self):
        caption = build_telegram_video_caption(
            member_name="Michie JKT48",
            member_username="jkt48_michie",
            started_at="2026-09-19T12:30:00Z",
            platform="idn",
        )
        self.assertIn("IDN LIVE REPLAY", caption)

    def test_youtube_notification_header_follows_platform(self):
        showroom = build_youtube_notification(
            member_name="Heidi JKT48",
            member_username="jkt48_heidi",
            started_at="2026-09-19T12:30:00Z",
            video_id="abcdefghijk",
            platform="showroom",
        )
        self.assertIn("SHOWROOM LIVE REPLAY", showroom)
        self.assertNotIn("IDN", showroom)
        self.assertIn("https://youtu.be/abcdefghijk", showroom)

        idn = build_youtube_notification(
            member_name="Michie JKT48",
            member_username="jkt48_michie",
            started_at="2026-09-19T12:30:00Z",
            video_id="lmnopqrstuv",
            platform="idn",
        )
        self.assertIn("IDN LIVE REPLAY", idn)
        # Tanpa platform (pemanggil lama) → default IDN, tidak error.
        legacy = build_youtube_notification(
            member_name="Michie JKT48",
            member_username="jkt48_michie",
            started_at="2026-09-19T12:30:00Z",
            video_id="lmnopqrstuv",
        )
        self.assertIn("IDN LIVE REPLAY", legacy)


class TestVideoSplitter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create a small synthetic MP4 video (6 seconds) using ffmpeg
        cls.test_dir = Path("test_scratch")
        cls.test_dir.mkdir(exist_ok=True)
        cls.sample_video = cls.test_dir / "sample_video.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=6:size=320x240:rate=30",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=6",
            "-g", "30",
            "-c:v", "libx264",
            "-c:a", "aac",
            str(cls.sample_video),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @classmethod
    def tearDownClass(cls):
        # Cleanup synthetic files
        if cls.sample_video.exists():
            cls.sample_video.unlink(missing_ok=True)
        for f in cls.test_dir.glob("*"):
            f.unlink(missing_ok=True)
        if cls.test_dir.exists():
            try:
                cls.test_dir.rmdir()
            except Exception:
                pass

    def test_get_video_metadata(self):
        meta = asyncio.run(get_video_metadata(self.sample_video))
        self.assertEqual(meta.width, 320)
        self.assertEqual(meta.height, 240)
        self.assertGreater(meta.duration_seconds, 5.0)
        self.assertGreater(meta.size_bytes, 0)

    def test_generate_thumbnail(self):
        thumb = asyncio.run(generate_thumbnail(self.sample_video, seek_seconds=2.0))
        self.assertIsNotNone(thumb)
        self.assertTrue(thumb.exists())
        self.assertGreater(thumb.stat().st_size, 0)
        thumb.unlink(missing_ok=True)

    def test_split_not_needed_when_below_limit(self):
        parts = asyncio.run(split_video_if_needed(self.sample_video, max_bytes=100 * 1024 * 1024))
        self.assertEqual(len(parts), 1)
        self.assertFalse(parts[0].is_split)
        self.assertEqual(parts[0].part_number, 1)
        self.assertEqual(parts[0].total_parts, 1)
        self.assertEqual(parts[0].file_path, self.sample_video.resolve())

    def test_split_needed_when_exceeds_limit(self):
        # Set max_bytes lower than the test video size to force splitting into parts
        file_size = self.sample_video.stat().st_size
        forced_limit = int(file_size * 0.6)

        parts = asyncio.run(split_video_if_needed(self.sample_video, max_bytes=forced_limit))
        self.assertGreaterEqual(len(parts), 2)
        for idx, part in enumerate(parts, start=1):
            self.assertEqual(part.part_number, idx)
            self.assertEqual(part.total_parts, len(parts))
            self.assertTrue(part.is_split)
            self.assertTrue(part.file_path.exists())

        # Test cleanup of generated split parts
        cleanup_video_parts(parts)
        for part in parts:
            self.assertFalse(part.file_path.exists())


class TestTelegramMultipartResume(unittest.TestCase):
    """Multipart retries must resume after the last durable Telegram message."""

    def test_failure_persists_prefix_and_retry_skips_sent_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            parts = [
                VideoPart(i, 3, Path(directory) / f"part{i}.mp4", 10, 1, 16, 9, True)
                for i in (1, 2, 3)
            ]
            for part in parts:
                part.file_path.write_bytes(b"part")
            sender = TelegramSender.__new__(TelegramSender)
            sender.send_video_file = AsyncMock(side_effect=[101, None])
            saved_prefixes = []
            calls = []

            async def fake_split(path):
                calls.append(("split", str(path)))
                return parts

            async def fake_thumbnail(path):
                calls.append(("thumb", str(path)))
                return None

            async def run():
                with (
                    patch("bot.telegram_sender.split_video_if_needed", side_effect=fake_split),
                    patch("bot.telegram_sender.generate_thumbnail", side_effect=fake_thumbnail),
                    patch("bot.telegram_sender.cleanup_video_parts"),
                ):
                    with self.assertRaises(RuntimeError):
                        await sender.upload_video_with_splitting(
                            source,
                            "Test",
                            "jkt48_test",
                            "2026-09-24T00:00:00+00:00",
                            on_part_sent=lambda ids: saved_prefixes.append(list(ids)),
                        )
                    self.assertEqual(saved_prefixes, [[101]])

                    # Re-splitting the same source is expected. Only part 2
                    # should be uploaded; durable part 1 is not duplicated.
                    sender.send_video_file.reset_mock()
                    sender.send_video_file.side_effect = [202, 303]
                    result = await sender.upload_video_with_splitting(
                        source,
                        "Test",
                        "jkt48_test",
                        "2026-09-24T00:00:00+00:00",
                        existing_message_ids=saved_prefixes[-1],
                        on_part_sent=lambda ids: saved_prefixes.append(list(ids)),
                    )
                    return result

            result = asyncio.run(run())
            self.assertEqual(result, [101, 202, 303])
            uploaded_parts = [
                call.kwargs["file_path"].name
                for call in sender.send_video_file.await_args_list
            ]
            self.assertEqual(uploaded_parts, ["part2.mp4", "part3.mp4"])
            self.assertEqual(saved_prefixes[-1], [101, 202, 303])



if __name__ == "__main__":
    unittest.main()
