"""
test_telegram_upload.py - Unit tests for video splitter, telegram captions, and config.
"""
import asyncio
import os
import subprocess
import unittest
from pathlib import Path

from bot.config import Config
from bot.telegram_sender import build_telegram_video_caption, _format_size
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


if __name__ == "__main__":
    unittest.main()
