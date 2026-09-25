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

from telethon.errors import FloodError, FloodWaitError, MediaEmptyError

from bot.config import Config
from bot.telegram_sender import (
    TelegramSender,
    build_telegram_video_caption,
    build_youtube_notification,
    _format_size,
    _flood_wait_seconds,
    _flood_backoff_seconds,
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



def _fake_message(message_id: int):
    """Objek pesan Telegram minimal untuk stub `send_file`."""

    class _Message:
        def __init__(self, mid: int) -> None:
            self.id = mid

    return _Message(message_id)


class TestTelegramFloodHandling(unittest.TestCase):
    """RPC 420 (`FLOOD_PREMIUM_WAIT_*`) harus dihormati, bukan diulang 5 detik.

    Insiden 26 Sep 2026: upload 819 MB kena FLOOD_PREMIUM_WAIT_3 tiga kali.
    Karena hanya `FloodWaitError` (RPC 429) yang tertangkap, error itu jatuh ke
    `except Exception` → upload diulang dari 0% dan tidak pernah selesai.
    """

    def test_flood_wait_error_uses_seconds_attribute(self):
        exc = FloodWaitError(request=None, capture=42)
        self.assertEqual(_flood_wait_seconds(exc), 47)

    def test_premium_wait_variant_is_parsed_from_message(self):
        exc = FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3")
        self.assertEqual(_flood_wait_seconds(exc), 8)

    def test_unknown_flood_falls_back_to_default(self):
        exc = FloodError(request=None, message="FLOOD")
        self.assertEqual(_flood_wait_seconds(exc, default=30), 30)

    def test_upload_waits_for_flood_instead_of_restarting_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            video.write_bytes(b"0" * 32)
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(
                side_effect=[
                    FloodError(request=None, message="FLOOD"),
                    _fake_message(77),
                ]
            )
            waits: list[float] = []

            async def fake_sleep(seconds):
                waits.append(seconds)

            async def run():
                with patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep):
                    return await sender.send_video_file(video)

            self.assertEqual(asyncio.run(run()), 77)
            self.assertEqual(waits, [30], "harus menunggu sesuai flood, bukan 5 detik")
            self.assertEqual(sender._client.send_file.await_count, 2)

    def test_flood_backoff_doubles_and_caps(self):
        self.assertEqual(_flood_backoff_seconds(15, 1), 15)
        self.assertEqual(_flood_backoff_seconds(15, 2), 30)
        self.assertEqual(_flood_backoff_seconds(15, 3), 60)
        self.assertEqual(_flood_backoff_seconds(15, 4), 120)
        # Cap menahan backoff agar tidak tumbuh tanpa batas.
        self.assertEqual(_flood_backoff_seconds(15, 20, cap=900), 900)

    def test_upload_backoff_grows_across_flood_retries(self):
        """Tiga flood berturut-turut harus menunggu 15/30/60, bukan 8/8/8."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            video.write_bytes(b"0" * 32)
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(
                side_effect=[
                    FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3"),
                    FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3"),
                    FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3"),
                    _fake_message(99),
                ]
            )
            waits: list[float] = []

            async def fake_sleep(seconds):
                waits.append(seconds)

            async def run():
                with patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep):
                    return await sender.send_video_file(video)

            self.assertEqual(asyncio.run(run()), 99)
            self.assertEqual(
                waits, [15, 30, 60], "jeda flood harus memanjang, bukan konstan"
            )

    def test_upload_gives_up_after_flood_budget(self):
        """Setelah jatah flood habis, upload kembali None (file tetap di disk)."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            video.write_bytes(b"0" * 32)
            old_retries = Config.TELEGRAM_FLOOD_MAX_RETRIES
            Config.TELEGRAM_FLOOD_MAX_RETRIES = 2
            try:
                sender = TelegramSender.__new__(TelegramSender)
                sender._connected = True
                sender._client = AsyncMock()
                sender._client.send_file = AsyncMock(
                    side_effect=FloodError(request=None, message="FLOOD")
                )

                async def fake_sleep(seconds):
                    return None

                async def run():
                    with patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep):
                        return await sender.send_video_file(video)

                self.assertIsNone(asyncio.run(run()))
                self.assertEqual(
                    sender._client.send_file.await_count,
                    2,
                    "upload harus berhenti tepat di jatah flood, tidak mencoba lebih",
                )
            finally:
                Config.TELEGRAM_FLOOD_MAX_RETRIES = old_retries

    def test_media_empty_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "story.mp4"
            video.write_bytes(b"0" * 32)
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(side_effect=MediaEmptyError(request=None))

            self.assertIsNone(asyncio.run(sender.send_video_file(video)))
            self.assertEqual(
                sender._client.send_file.await_count,
                1,
                "media ditolak permanen tidak boleh diulang 3x",
            )

    def test_album_media_empty_error_is_not_retried(self):
        # Dua berkas agar benar-benar melewati jalur album (1 berkas memakai
        # jalur media tunggal, lihat test_single_photo_is_sent_as_media_not_album).
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "1.jpg"
            second = Path(directory) / "2.jpg"
            first.write_bytes(b"x")
            second.write_bytes(b"y")
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(side_effect=MediaEmptyError(request=None))

            ids = asyncio.run(
                sender._send_media_album([first, second], "caption", -100123)
            )
            self.assertEqual(ids, [])
            self.assertEqual(sender._client.send_file.await_count, 1)

    def test_single_photo_is_sent_as_media_not_album(self):
        """1 foto HARUS dikirim sebagai media biasa, bukan album.

        Telethon meneruskan list apa pun ke `_send_album` (SendMultiMediaRequest)
        dan Telegram menolak album satu media dengan MediaEmptyError — inilah
        penyebab story TikTok 1 foto tak pernah masuk arsip.
        """
        with tempfile.TemporaryDirectory() as directory:
            only = Path(directory) / "story.jpg"
            only.write_bytes(b"x")
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(return_value=_fake_message(55))

            ids = asyncio.run(sender._send_media_album([only], "caption", -100123))
            self.assertEqual(ids, [55])
            sent_media = sender._client.send_file.await_args.args[1]
            self.assertIsInstance(
                sent_media, str,
                "1 berkas harus dikirim sebagai path, bukan list (album)",
            )

    def test_multi_photo_still_uses_album(self):
        """2+ foto tetap dikirim sebagai album (perilaku tidak berubah)."""
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "1.jpg"
            second = Path(directory) / "2.jpg"
            first.write_bytes(b"x")
            second.write_bytes(b"y")
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(
                return_value=[_fake_message(1), _fake_message(2)]
            )

            ids = asyncio.run(
                sender._send_media_album([first, second], "caption", -100123)
            )
            self.assertEqual(ids, [1, 2])
            sent_media = sender._client.send_file.await_args.args[1]
            self.assertIsInstance(sent_media, list, "multi-foto harus tetap album")

    def test_album_flood_waits_before_retry(self):
        # Dua berkas agar melewati jalur album (1 berkas memakai media tunggal).
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "1.jpg"
            second = Path(directory) / "2.jpg"
            first.write_bytes(b"x")
            second.write_bytes(b"y")
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(
                side_effect=[
                    FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3"),
                    [_fake_message(88), _fake_message(89)],
                ]
            )
            waits: list[float] = []

            async def fake_sleep(seconds):
                waits.append(seconds)

            async def run():
                with patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep):
                    return await sender._send_media_album(
                        [first, second], "caption", -100123
                    )

            self.assertEqual(asyncio.run(run()), [88, 89])
            self.assertEqual(
                waits, [8], "durasi FLOOD_PREMIUM_WAIT_3 harus dipakai, bukan default"
            )


if __name__ == "__main__":
    unittest.main()
