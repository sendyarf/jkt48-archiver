"""
Unit tests for recovery, video splitting, Telegram multipart, and config.

test_telegram_upload.py - Unit tests for video splitter, telegram captions, and config.
"""
import asyncio
import json
import os
import re
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
    MAX_FILE_PARTS_FREE,
    MAX_FILE_PARTS_PREMIUM,
    TelegramFileTooLarge,
    TelegramFloodExhausted,
    TelegramUploadStalled,
    _format_size,
    _flood_wait_seconds,
    _flood_wait_with_jitter,
)
from bot.telegram_limits import (
    MAX_PART_SIZE_BYTES,
    max_file_bytes,
    safe_split_bytes,
)
from bot.video_splitter import (
    VideoPart,
    cleanup_video_parts,
    generate_thumbnail,
    get_default_max_bytes,
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

            async def fake_split(path, max_bytes=None):
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

    def _make_two_part_scenario(self, directory):
        source = Path(directory) / "source.mp4"
        source.write_bytes(b"video")
        parts = [
            VideoPart(i, 2, Path(directory) / f"part{i}.mp4", 10, 1, 16, 9, True)
            for i in (1, 2)
        ]
        for part in parts:
            part.file_path.write_bytes(b"part")
        return source, parts

    def test_multi_part_sent_as_single_album_message(self):
        """2..10 part -> satu pesan album; send_video_file tidak dipanggil."""
        with tempfile.TemporaryDirectory() as directory:
            source, parts = self._make_two_part_scenario(directory)
            sender = TelegramSender.__new__(TelegramSender)
            sender.send_video_file = AsyncMock()
            sender._send_parts_album = AsyncMock(return_value=[501, 502])
            saved = []

            async def fake_split(path, max_bytes=None):
                return parts

            async def run():
                with (
                    patch("bot.telegram_sender.split_video_if_needed", side_effect=fake_split),
                    patch("bot.telegram_sender.generate_thumbnail", new=AsyncMock(return_value=None)),
                    patch("bot.telegram_sender.cleanup_video_parts"),
                ):
                    return await sender.upload_video_with_splitting(
                        source, "Test", "jkt48_test", "2026-09-24T00:00:00+00:00",
                        on_part_sent=lambda ids: saved.append(list(ids)),
                    )

            result = asyncio.run(run())
            self.assertEqual(result, [501, 502])
            self.assertEqual(saved, [[501, 502]])
            sender.send_video_file.assert_not_awaited()
            # Caption album tidak memuat header [Part x/y] per-part.
            album_caption = sender._send_parts_album.await_args.args[1]
            self.assertNotIn("[Part", album_caption)
            self.assertIn("album", album_caption.lower())

    def test_album_rejection_falls_back_to_per_part(self):
        """Album return None -> jatuh ke pengiriman per-part seperti semula."""
        with tempfile.TemporaryDirectory() as directory:
            source, parts = self._make_two_part_scenario(directory)
            sender = TelegramSender.__new__(TelegramSender)
            sender.send_video_file = AsyncMock(side_effect=[601, 602])
            sender._send_parts_album = AsyncMock(return_value=None)

            async def fake_split(path, max_bytes=None):
                return parts

            async def run():
                with (
                    patch("bot.telegram_sender.split_video_if_needed", side_effect=fake_split),
                    patch("bot.telegram_sender.generate_thumbnail", new=AsyncMock(return_value=None)),
                    patch("bot.telegram_sender.cleanup_video_parts"),
                ):
                    return await sender.upload_video_with_splitting(
                        source, "Test", "jkt48_test", "2026-09-24T00:00:00+00:00",
                    )

            result = asyncio.run(run())
            self.assertEqual(result, [601, 602])
            self.assertEqual(sender.send_video_file.await_count, 2)

    def test_resume_partial_ids_skip_album_path(self):
        """Resume dari marker parsial tidak boleh mengirim ulang sebagai album."""
        with tempfile.TemporaryDirectory() as directory:
            source, parts = self._make_two_part_scenario(directory)
            sender = TelegramSender.__new__(TelegramSender)
            sender.send_video_file = AsyncMock(return_value=602)
            sender._send_parts_album = AsyncMock()

            async def fake_split(path, max_bytes=None):
                return parts

            async def run():
                with (
                    patch("bot.telegram_sender.split_video_if_needed", side_effect=fake_split),
                    patch("bot.telegram_sender.generate_thumbnail", new=AsyncMock(return_value=None)),
                    patch("bot.telegram_sender.cleanup_video_parts"),
                ):
                    return await sender.upload_video_with_splitting(
                        source, "Test", "jkt48_test", "2026-09-24T00:00:00+00:00",
                        existing_message_ids=[601],
                    )

            result = asyncio.run(run())
            self.assertEqual(result, [601, 602])
            sender._send_parts_album.assert_not_awaited()

    def test_oversized_split_part_aborts_before_upload(self):
        """Part hasil split di atas batas part server -> gagal sebelum byte naik."""
        with tempfile.TemporaryDirectory() as directory:
            source, parts = self._make_two_part_scenario(directory)
            parts[0] = VideoPart(1, 2, parts[0].file_path, 9 * 1024 ** 3, 1, 16, 9, True)
            sender = TelegramSender.__new__(TelegramSender)
            sender.send_video_file = AsyncMock()
            sender._send_parts_album = AsyncMock()

            async def fake_split(path, max_bytes=None):
                return parts

            async def run():
                with (
                    patch("bot.telegram_sender.split_video_if_needed", side_effect=fake_split),
                    patch("bot.telegram_sender.generate_thumbnail", new=AsyncMock(return_value=None)),
                    patch("bot.telegram_sender.cleanup_video_parts"),
                ):
                    with self.assertRaises(TelegramFileTooLarge):
                        await sender.upload_video_with_splitting(
                            source, "Test", "jkt48_test",
                            "2026-09-24T00:00:00+00:00",
                        )

            asyncio.run(run())
            sender.send_video_file.assert_not_awaited()
            sender._send_parts_album.assert_not_awaited()



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

    def test_premium_wait_variant_uses_premium_floor(self):
        # FLOOD_PREMIUM_WAIT_3 = kuota premium lelah: 3 detik di pesan bukan
        # penalti sebenarnya → digenjot ke TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS.
        exc = FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3")
        self.assertEqual(
            _flood_wait_seconds(exc), Config.TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS
        )

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
                with (
                    patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep),
                    patch("bot.telegram_sender.random.uniform", return_value=0.0),
                ):
                    return await sender.send_video_file(video)

            self.assertEqual(asyncio.run(run()), 77)
            self.assertEqual(waits, [30], "harus menunggu sesuai flood, bukan 5 detik")
            self.assertEqual(sender._client.send_file.await_count, 2)

    def test_flood_wait_applies_random_jitter(self):
        """Jeda flood = durasi Telegram + jitter acak 0–30%."""
        with patch("bot.telegram_sender.random.uniform", return_value=0.3):
            self.assertEqual(_flood_wait_with_jitter(100), 130)
        with patch("bot.telegram_sender.random.uniform", return_value=0.0):
            self.assertEqual(_flood_wait_with_jitter(100), 100)

    def test_premium_flood_waits_use_premium_floor_on_upload(self):
        """FLOOD_PREMIUM_WAIT_* menunggu TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            video.write_bytes(b"0" * 32)
            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()
            sender._client.send_file = AsyncMock(
                side_effect=[
                    FloodError(request=None, message="FLOOD_PREMIUM_WAIT_3"),
                    _fake_message(99),
                ]
            )
            waits: list[float] = []

            async def fake_sleep(seconds):
                waits.append(seconds)

            async def run():
                with (
                    patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep),
                    patch("bot.telegram_sender.random.uniform", return_value=0.0),
                ):
                    return await sender.send_video_file(video)

            self.assertEqual(asyncio.run(run()), 99)
            self.assertEqual(
                waits,
                [Config.TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS],
                "FLOOD_PREMIUM_WAIT_* harus menunggu floor premium, bukan 8 detik",
            )

    def test_upload_gives_up_after_flood_budget(self):
        """Setelah jatah flood habis, upload berhenti tepat di batas."""
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
                        await sender.send_video_file(video)

                with self.assertRaises(TelegramFloodExhausted):
                    asyncio.run(run())
                self.assertEqual(
                    sender._client.send_file.await_count,
                    2,
                    "upload harus berhenti tepat di jatah flood, tidak mencoba lebih",
                )
            finally:
                Config.TELEGRAM_FLOOD_MAX_RETRIES = old_retries

    def test_large_upload_and_story_do_not_overlap(self):
        """Upload besar dan story TikTok harus BERURUTAN, bukan bersamaan.

        Rate limit Telegram per-akuan: bila keduanya jalan bersamaan, file
        besar flood jauh lebih awal (log 26 Sep 2026: flood di 4,6%).
        """
        with tempfile.TemporaryDirectory() as directory:
            big = Path(directory) / "big.mp4"
            big.write_bytes(b"0" * 32)
            photo = Path(directory) / "01.webp"
            photo.write_bytes(b"x")

            sender = TelegramSender.__new__(TelegramSender)
            sender._connected = True
            sender._client = AsyncMock()

            overlap = {"detected": False}
            active = {"count": 0}

            async def slow_send(*args, **kwargs):
                active["count"] += 1
                if active["count"] > 1:
                    overlap["detected"] = True
                await asyncio.sleep(0.05)
                active["count"] -= 1
                # send_video_file mengoper `file=` sebagai keyword, sedangkan
                # _send_media_album mengoper path/list sebagai argumen posisi.
                media = kwargs.get("file", args[1] if len(args) > 1 else None)
                if isinstance(media, list):
                    return [_fake_message(2), _fake_message(3)]
                return _fake_message(1)

            sender._client.send_file = AsyncMock(side_effect=slow_send)

            async def run():
                results = await asyncio.gather(
                    sender.send_video_file(big, caption="live"),
                    sender._send_media_album([photo], "story", -100123),
                )
                return results

            results = asyncio.run(run())
            # Kedua upload harus benar-benar berhasil (bukan gagal diam-diam).
            self.assertEqual(results[0], 1)
            self.assertEqual(results[1], [1])
            self.assertFalse(
                overlap["detected"],
                "upload Telegram harus serial (satu per satu), bukan paralel",
            )

    def test_flood_exhaustion_raises_dedicated_error(self):
        """Jatah flood habis → exception khusus (bukan return None biasa)."""
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
                        await sender.send_video_file(video)

                with self.assertRaises(TelegramFloodExhausted):
                    asyncio.run(run())
                self.assertEqual(sender._client.send_file.await_count, 2)
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
                with (
                    patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep),
                    patch("bot.telegram_sender.random.uniform", return_value=0.0),
                ):
                    return await sender._send_media_album(
                        [first, second], "caption", -100123
                    )

            self.assertEqual(asyncio.run(run()), [88, 89])
            self.assertEqual(
                waits,
                [Config.TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS],
                "FLOOD_PREMIUM_WAIT_3 harus menunggu floor premium, bukan 8 detik",
            )

    def test_album_flood_exhaustion_raises(self):
        """Jatah flood habis pada album → TelegramFloodExhausted (transien),
        bukan [] diam-diam (yang akan dianggap kegagalan permanen)."""
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "1.jpg"
            second = Path(directory) / "2.jpg"
            first.write_bytes(b"x")
            second.write_bytes(b"y")
            old_retries = Config.TELEGRAM_FLOOD_MAX_RETRIES
            Config.TELEGRAM_FLOOD_MAX_RETRIES = 1
            try:
                sender = TelegramSender.__new__(TelegramSender)
                sender._connected = True
                sender._client = AsyncMock()
                sender._client.send_file = AsyncMock(
                    side_effect=FloodError(request=None, message="FLOOD_WAIT_5")
                )

                async def fake_sleep(seconds):
                    return None

                async def run():
                    with patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep):
                        await sender._send_media_album(
                            [first, second], "caption", -100123
                        )

                with self.assertRaises(TelegramFloodExhausted):
                    asyncio.run(run())
                self.assertEqual(sender._client.send_file.await_count, 1)
            finally:
                Config.TELEGRAM_FLOOD_MAX_RETRIES = old_retries

    def test_single_media_flood_exhaustion_raises(self):
        """Fast path 1-berkas juga melempar TelegramFloodExhausted saat habis."""
        with tempfile.TemporaryDirectory() as directory:
            only = Path(directory) / "story.jpg"
            only.write_bytes(b"x")
            old_retries = Config.TELEGRAM_FLOOD_MAX_RETRIES
            Config.TELEGRAM_FLOOD_MAX_RETRIES = 1
            try:
                sender = TelegramSender.__new__(TelegramSender)
                sender._connected = True
                sender._client = AsyncMock()
                sender._client.send_file = AsyncMock(
                    side_effect=FloodError(request=None, message="FLOOD_WAIT_5")
                )

                async def fake_sleep(seconds):
                    return None

                async def run():
                    with patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep):
                        await sender._send_media_album([only], "caption", -100123)

                with self.assertRaises(TelegramFloodExhausted):
                    asyncio.run(run())
            finally:
                Config.TELEGRAM_FLOOD_MAX_RETRIES = old_retries


class _FakeUploadClient:
    """Client Telethon palsu untuk jalur resume: callable (SaveBigFilePart)
    + send_file untuk pengiriman akhir."""

    def __init__(self, fail_at_part=None):
        self.requests = []
        self.fail_at_part = fail_at_part
        self.failed_once = False
        self.send_file = AsyncMock(return_value=_fake_message(123))

    async def __call__(self, request):
        self.requests.append(request)
        part = getattr(request, "file_part", None)
        if (
            not self.failed_once
            and self.fail_at_part is not None
            and part is not None
            and part >= self.fail_at_part
        ):
            self.failed_once = True
            raise FloodError(request=None, message="FLOOD_WAIT_5")
        return True


class TestTelegramIntraFileResume(unittest.TestCase):
    """File >= ambang resume: upload per-part + sidecar, retry me-resume."""

    def _make_sender(self, client):
        sender = TelegramSender.__new__(TelegramSender)
        sender._connected = True
        sender._client = client
        return sender

    def test_resume_mid_file_does_not_resend_uploaded_parts(self):
        """Gagal di 60% → flood; retry melanjutkan part berikutnya, dan
        sidecar dihapus setelah sukses."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            # 10 part × 4 KB = 40 KB; ambang resume dan part size di-empot.
            video.write_bytes(b"0" * (10 * 4096))
            client = _FakeUploadClient(fail_at_part=6)
            sender = self._make_sender(client)
            sidecar = Path(str(video) + ".tgup.json")

            async def fake_sleep(seconds):
                return None

            async def run():
                with (
                    patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep),
                    patch("bot.telegram_sender.random.uniform", return_value=0.0),
                    patch("bot.telegram_sender._RESUME_MIN_SIZE_BYTES", 1024),
                    patch(
                        "bot.telegram_sender.utils.get_appropriated_part_size",
                        return_value=4,
                    ),
                ):
                    return await sender.send_video_file(video)

            self.assertEqual(asyncio.run(run()), 123)
            part_indexes = [r.file_part for r in client.requests]
            first_window = part_indexes[:7]
            self.assertEqual(first_window, [0, 1, 2, 3, 4, 5, 6],
                             "part 0–5 terkirim sebelum flood di part 6")
            self.assertEqual(
                part_indexes[7:], [6, 7, 8, 9],
                "retry harus resume dari part 6, bukan mengulang part 0–5",
            )
            resent = [p for p in part_indexes[7:] if p < 6]
            self.assertEqual(resent, [], "part sebelum checkpoint tidak di-upload ulang")
            self.assertFalse(sidecar.exists(), "sidecar dihapus setelah sukses")
            # Pengiriman akhir memakai handle InputFileBig hasil upload.
            sent_file = client.send_file.await_args.kwargs["file"]
            from telethon.tl.types import InputFileBig
            self.assertIsInstance(sent_file, InputFileBig)

    def test_resume_skips_upload_when_sidecar_is_complete(self):
        """parts_sent == total_parts → langsung kirim tanpa request part baru."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            size = 10 * 4096
            video.write_bytes(b"0" * size)
            sidecar = Path(str(video) + ".tgup.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "path": str(video),
                        "size": size,
                        "mtime": video.stat().st_mtime,
                        "file_id": 987654321,
                        "part_size": 4096,
                        "total_parts": 10,
                        "parts_sent": 10,
                    }
                ),
                encoding="utf-8",
            )
            client = _FakeUploadClient()
            sender = self._make_sender(client)

            async def run():
                with (
                    patch("bot.telegram_sender._RESUME_MIN_SIZE_BYTES", 1024),
                    patch(
                        "bot.telegram_sender.utils.get_appropriated_part_size",
                        return_value=4,
                    ),
                ):
                    return await sender.send_video_file(video)

            self.assertEqual(asyncio.run(run()), 123)
            self.assertEqual(client.requests, [], "tidak boleh ada upload ulang")
            self.assertFalse(sidecar.exists())

    def test_resume_sidecar_deleted_on_media_empty(self):
        """MediaEmptyError = penolakan permanen → sidecar ikut dihapus."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            video.write_bytes(b"0" * (10 * 4096))
            client = _FakeUploadClient()
            client.send_file = AsyncMock(side_effect=MediaEmptyError(request=None))
            sender = self._make_sender(client)
            sidecar = Path(str(video) + ".tgup.json")

            async def run():
                with (
                    patch("bot.telegram_sender._RESUME_MIN_SIZE_BYTES", 1024),
                    patch(
                        "bot.telegram_sender.utils.get_appropriated_part_size",
                        return_value=4,
                    ),
                ):
                    return await sender.send_video_file(video)

            self.assertIsNone(asyncio.run(run()))
            self.assertFalse(sidecar.exists())

    def test_over_account_part_limit_aborts_before_first_part(self):
        """File yang butuh part lebih banyak dari batas akun: berhenti total
        sebelum part pertama dikirim (server menolak FILE_PARTS_INVALID)."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "huge.mp4"
            # 10 part × 4 KB, batas akun di-empot jadi 5 part.
            video.write_bytes(b"0" * (10 * 4096))
            client = _FakeUploadClient()
            sender = self._make_sender(client)
            sidecar = Path(str(video) + ".tgup.json")

            async def run():
                with (
                    patch("bot.telegram_sender.MAX_FILE_PARTS_FREE", 5),
                    patch(
                        "bot.telegram_sender.utils.get_appropriated_part_size",
                        return_value=4,
                    ),
                ):
                    return await sender._upload_with_resume(
                        video, video.stat().st_size, video.stat().st_mtime
                    )

            with self.assertRaises(TelegramFileTooLarge) as caught:
                asyncio.run(run())
            self.assertIn("Telegram Premium", str(caught.exception))
            self.assertEqual(
                client.requests, [],
                "part tidak boleh dikirim kalau batas akun sejak awal tidak cukup",
            )
            self.assertFalse(sidecar.exists(), "sidecar tidak dibuat untuk file mustahil")

    def test_over_part_limit_is_not_retried(self):
        """Penolakan permanen ini tidak memakan jatah retry maupun bandwidth."""
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "huge.mp4"
            video.write_bytes(b"0" * (10 * 4096))
            client = _FakeUploadClient()
            sender = self._make_sender(client)
            sleeps: list[int] = []

            async def fake_sleep(seconds):
                sleeps.append(seconds)

            async def run():
                with (
                    patch("bot.telegram_sender.asyncio.sleep", side_effect=fake_sleep),
                    patch("bot.telegram_sender._RESUME_MIN_SIZE_BYTES", 1024),
                    patch("bot.telegram_sender.MAX_FILE_PARTS_FREE", 5),
                    patch(
                        "bot.telegram_sender.utils.get_appropriated_part_size",
                        return_value=4,
                    ),
                ):
                    return await sender.send_video_file(video)

            self.assertIsNone(asyncio.run(run()))
            self.assertEqual(client.requests, [], "tidak ada part yang terbuang")
            self.assertEqual(client.send_file.await_count, 0)
            self.assertEqual(sleeps, [], "error permanen tidak dicoba ulang")

    def test_max_file_parts_follows_account_premium_flag(self):
        """Batas part diambil dari jenis akun: Premium 8000, selain itu 4000."""
        self.assertEqual(
            (MAX_FILE_PARTS_FREE, MAX_FILE_PARTS_PREMIUM), (4000, 8000),
            "batas jumlah part dikonfirmasi live 26 Sep 2026 (4000 ok, 4001 ditolak)",
        )

        free_client = _FakeUploadClient()  # tanpa get_me → dianggap akun biasa
        free_sender = self._make_sender(free_client)
        self.assertEqual(asyncio.run(free_sender._max_file_parts()), MAX_FILE_PARTS_FREE)

        class _PremiumUser:
            premium = True

        premium_client = _FakeUploadClient()
        premium_client.get_me = AsyncMock(return_value=_PremiumUser())
        premium_sender = self._make_sender(premium_client)
        self.assertEqual(
            asyncio.run(premium_sender._max_file_parts()), MAX_FILE_PARTS_PREMIUM
        )


class TestSplitThresholdGuard(unittest.TestCase):
    """Ambang split tidak boleh menghasilkan part yang ditolak server."""

    def _bare_sender(self, client):
        sender = TelegramSender.__new__(TelegramSender)
        sender._connected = True
        sender._client = client
        return sender

    def test_part_cap_matches_live_server_limit(self):
        """Angka batas = konfigurasi server yang diverifikasi 26 Sep 2026."""
        self.assertEqual(MAX_FILE_PARTS_FREE, 4000)
        self.assertEqual(MAX_FILE_PARTS_PREMIUM, 8000)
        # 4000 part × 512 KiB = 2.097.152.000 B ≈ 2 GB (telegram.org/faq).
        self.assertEqual(max_file_bytes(MAX_FILE_PARTS_FREE), 2_097_152_000)
        self.assertEqual(max_file_bytes(MAX_FILE_PARTS_PREMIUM), 4_194_304_000)

    def test_default_limit_never_exceeds_free_account_capacity(self):
        """Config liar (mis. 5 GB) dijepit: part hasil split pasti bisa dikirim."""
        with patch.object(Config, "TELEGRAM_MAX_FILE_SIZE_MB", 5000):
            limit = get_default_max_bytes()
        self.assertEqual(limit, safe_split_bytes(MAX_FILE_PARTS_FREE))
        needed_parts = -(-limit // MAX_PART_SIZE_BYTES)
        self.assertLessEqual(
            needed_parts, MAX_FILE_PARTS_FREE,
            "file sebesar ambang split harus masih muat batas part akun biasa",
        )

    def test_default_limit_respects_smaller_config(self):
        """Ambang yang lebih kecil dari kapasitas akun dipakai apa adanya."""
        with patch.object(Config, "TELEGRAM_MAX_FILE_SIZE_MB", 1000):
            self.assertEqual(get_default_max_bytes(), 1000 * 1024 * 1024)

    def test_shipped_default_keeps_a_quarter_of_the_part_budget(self):
        """Ambang bawaan 1500 MB = 3.000 part: 25% jatah akun biasa tersisa
        untuk pembengkakan part (ffmpeg memotong per durasi pada stream VBR)."""
        with patch.object(Config, "TELEGRAM_MAX_FILE_SIZE_MB", 1500):
            limit = get_default_max_bytes()
        self.assertLessEqual(
            -(-limit // MAX_PART_SIZE_BYTES), MAX_FILE_PARTS_FREE * 3 // 4
        )

    def test_ambang_bawaan_konsisten_di_config_dan_env_example(self):
        """config.py dan .env.example menyebut angka yang sama, dan angka itu
        tidak boleh melewati 75% jatah part akun biasa."""
        root = Path(__file__).resolve().parents[1]
        patterns = {
            "bot/config.py": r'TELEGRAM_MAX_FILE_SIZE_MB", "(\d+)"',
            ".env.example": r"^TELEGRAM_MAX_FILE_SIZE_MB=(\d+)",
        }
        values = []
        for name, pattern in patterns.items():
            text = (root / name).read_text(encoding="utf-8")
            match = re.search(pattern, text, re.MULTILINE)
            self.assertIsNotNone(match, f"{name} tidak mengatur ambang split")
            values.append(int(match.group(1)))
        self.assertEqual(values[0], values[1])
        parts = values[1] * 1024 * 1024 // MAX_PART_SIZE_BYTES
        self.assertLessEqual(parts, MAX_FILE_PARTS_FREE * 3 // 4)

    def test_split_limit_follows_account_type(self):
        """Premium memperlonggar ambang sampai angka config (maks ~3.6 GB)."""
        class _PremiumUser:
            premium = True

        free_sender = self._bare_sender(_FakeUploadClient())
        premium_client = _FakeUploadClient()
        premium_client.get_me = AsyncMock(return_value=_PremiumUser())
        premium_sender = self._bare_sender(premium_client)

        async def run():
            with patch.object(Config, "TELEGRAM_MAX_FILE_SIZE_MB", 1950):
                return (
                    await free_sender._split_limit_bytes(),
                    await premium_sender._split_limit_bytes(),
                )

        free_limit, premium_limit = asyncio.run(run())
        self.assertEqual(free_limit, safe_split_bytes(MAX_FILE_PARTS_FREE))
        self.assertEqual(premium_limit, 1950 * 1024 * 1024)
        self.assertGreater(premium_limit, free_limit)

    def test_upload_passes_account_aware_limit_to_splitter(self):
        """Alur upload tidak lagi memakai ambang config mentah."""
        captured = {}

        async def fake_split(path, max_bytes=None):
            captured["max_bytes"] = max_bytes
            return [VideoPart(1, 1, Path(path), 10, 1.0, 16, 9, False)]

        sender = self._bare_sender(_FakeUploadClient())
        sender.send_video_file = AsyncMock(return_value=555)

        async def run():
            with (
                patch("bot.telegram_sender.split_video_if_needed", side_effect=fake_split),
                patch("bot.telegram_sender.generate_thumbnail",
                      new=AsyncMock(return_value=None)),
                patch("bot.telegram_sender.cleanup_video_parts"),
                patch.object(Config, "TELEGRAM_MAX_FILE_SIZE_MB", 1950),
            ):
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "source.mp4"
                    source.write_bytes(b"video")
                    return await sender.upload_video_with_splitting(
                        source, "Test", "jkt48_test", "2026-09-24T00:00:00+00:00",
                    )

        self.assertEqual(asyncio.run(run()), [555])
        self.assertEqual(
            captured["max_bytes"], safe_split_bytes(MAX_FILE_PARTS_FREE),
            "akun biasa: ambang dijepit ke kapasitas part server",
        )


class _HangingClient:
    """Client yang tidak pernah merespons — meniru koneksi TCP macet."""

    def __init__(self) -> None:
        self.upload_lock_held = False
        self.send_file_started = False
        self.requests: list[object] = []
        self._upload_lock = asyncio.Lock()
        self._notify_lock = asyncio.Lock()
        self._max_file_parts_cached = MAX_FILE_PARTS_FREE

    def __call__(self, request):
        self.requests.append(request)
        async def _never():
            await asyncio.sleep(3600)
        return _never()

    async def get_me(self):
        return None

    async def send_message(self, *args, **kwargs):
        return None

    async def send_file(self, *args, **kwargs):
        self.send_file_started = True
        async def _never():
            await asyncio.sleep(3600)
        return await _never()


class _OkUploadClient:
    """Client yang menerima semua part (tanpa flood, tanpa delay)."""

    def __init__(self) -> None:
        self.requests: list[object] = []
        self._upload_lock = asyncio.Lock()
        self._notify_lock = asyncio.Lock()
        self._max_file_parts_cached = MAX_FILE_PARTS_FREE

    def __call__(self, request):
        self.requests.append(request)

        async def _ok():
            return True

        return _ok()

    async def get_me(self):
        return None


class _ExpiredUploadSessionClient:
    """Server sudah membuang sebagian part, tapi sidecar lokal masih 100%."""

    def __init__(self, fail_parts: int = 1) -> None:
        self.send_file_calls = 0
        self.part_requests = 0
        self._fail_parts = fail_parts
        self._upload_lock = asyncio.Lock()
        self._notify_lock = asyncio.Lock()
        self._max_file_parts_cached = MAX_FILE_PARTS_FREE

    async def get_me(self):
        return None

    def __call__(self, request):
        self.part_requests += 1

        async def _ok():
            return True

        return _ok()

    async def send_file(self, *args, **kwargs):
        self.send_file_calls += 1
        if self.send_file_calls <= self._fail_parts:
            raise _file_part_missing()
        return _fake_message(self.send_file_calls)


def _file_part_missing():
    from telethon.errors import FilePartMissingError as _FPME
    return _FPME(request=None, capture=0)


class TestExpiredUploadSessionRecovery(unittest.TestCase):
    """Sidecar basi harus dibuang, bukan diulang ke file_id yang sudah mati.

    Terukur di VPS 1 Okt 2026: sidecar dari ~30 jam sebelumnya masih mencatat
    1007 dari 1403 part "terkirim", lalu part sisanya ter-upload dan
    SendMediaRequest ditolak `FILE_PART_MISSING: Part 532`. Error itu
    dicoba 7 kali tanpa ada yang menghapus sidecar, jadi file itu mustahil
    pernah berhasil: bot akan menyerah dan arsipnya hilang tanpa pernah
    mencoba mengunggah ulang.
    """

    def _sender(self, client, part_size_bytes):
        sender = TelegramSender.__new__(TelegramSender)
        sender._client = client
        sender._connected = True
        sender._max_file_parts_cached = MAX_FILE_PARTS_FREE
        return sender

    def test_stale_sidecar_is_discarded_and_reuploaded(self):
        client = _ExpiredUploadSessionClient()
        sender = self._sender(client, 0)
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            # >= 64 MB supaya jalur resume (sidecar) yang dipakai, bukan
            # upload biasa milik Telethon.
            size = 65 * 1024 * 1024
            video.write_bytes(b"0" * size)
            sidecar = Path(str(video) + ".tgup.json")

            # Sesi server sudah kedaluwarsa: 100% di sidecar, tapi FILE_PART_MISSING.
            sidecar.write_text(
                f'{{"file_id": "stale", "total_parts": 130, "parts_sent": 130, '
                f'"size": {size}, "mtime": 1.0}}',
                encoding="utf-8",
            )
            client.send_file_calls = 0
            client.part_requests = 0

            with patch.object(
                Config, "TELEGRAM_UPLOAD_PART_DELAY_MS", 0
            ), patch.object(Config, "TELEGRAM_SEND_TIMEOUT_SECONDS", 5), patch(
                "bot.telegram_sender.asyncio.sleep"
            ):
                result = asyncio.run(sender.send_video_file(video, caption="x"))

            self.assertIsNotNone(result, "percobaan kedua harus berhasil")
            self.assertEqual(
                client.send_file_calls, 2,
                "percobaan pertama gagal FILE_PART_MISSING, kedua harus jalan",
            )
            self.assertGreater(
                client.part_requests, 0,
                "setelah sidecar dibuang, part harus di-upload ulang dari awal",
            )
            self.assertFalse(
                sidecar.exists(),
                "sidecar harus dihapus setelah pesan berhasil terkirim",
            )

    def test_repeated_failure_gives_up_without_leaving_stale_sidecar(self):
        client = _ExpiredUploadSessionClient(fail_parts=99)
        sender = self._sender(client, 0)
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "big.mp4"
            size = 65 * 1024 * 1024
            video.write_bytes(b"0" * size)
            sidecar = Path(str(video) + ".tgup.json")
            sidecar.write_text(
                f'{{"file_id": "stale", "total_parts": 130, "parts_sent": 130, '
                f'"size": {size}, "mtime": 1.0}}',
                encoding="utf-8",
            )

            with patch.object(
                Config, "TELEGRAM_UPLOAD_PART_DELAY_MS", 0
            ), patch.object(Config, "TELEGRAM_SEND_TIMEOUT_SECONDS", 5):
                result = asyncio.run(sender.send_video_file(video, caption="x"))

            self.assertIsNone(result)
            self.assertFalse(
                sidecar.exists(),
                "sidecar basi tidak boleh ditinggalkan untuk siklus berikutnya",
            )


class TestTelegramPartPacing(unittest.TestCase):
    """Upload besar harus dijeda antar part, bukan mendorong tanpa henti.

    Terukur di VPS 1 Okt 2026: tanpa jeda, bot memompa ~3,6 part/detik
    (1,8 MB/s) lalu kena FLOOD_PREMIUM_WAIT 930-1042 detik setelah hanya
    ~20 MB - laju efektifnya 1,27 MB/menit, sementara YouTube mengunggah file
    yang sama dalam 2 menit 38 detik. Jeda 500 ms per part (~1 MB/s) menukar
    penalti 15 menit dengan transfer yang terus berjalan.
    """

    def _upload(self, delay_ms: int) -> tuple[list[float], int]:
        client = _OkUploadClient()
        sender = TelegramSender.__new__(TelegramSender)
        sender._client = client
        sender._connected = True
        sender._max_file_parts_cached = MAX_FILE_PARTS_FREE

        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds, *args, **kwargs):
            sleeps.append(seconds)
            return await real_sleep(0)

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                video = Path(directory) / "big.mp4"
                # Telethon memilih ukuran part sendiri (128 KiB untuk file
                # kecil, 256-512 KiB untuk yang lebih besar), jadi fixture
                # tidak perlu menebak: yang penting jumlah part > 1.
                video.write_bytes(b"0" * (2 * 1024 * 1024))
                with patch.object(
                    Config, "TELEGRAM_UPLOAD_PART_DELAY_MS", delay_ms
                ), patch("bot.telegram_sender.asyncio.sleep", fake_sleep):
                    await sender._upload_with_resume(
                        video, video.stat().st_size, 0.0
                    )
            return len(client.requests)

        parts = asyncio.run(run())
        self.assertGreater(parts, 1, "fixture harus terdiri dari beberapa part")
        return sleeps, parts

    def test_sleep_between_parts(self):
        sleeps, parts = self._upload(500)
        self.assertTrue(sleeps, "harus ada jeda antar part")
        self.assertAlmostEqual(max(sleeps), 0.5, places=3)
        # Part terakhir tidak perlu jeda, jadi jeda = jumlah part - 1.
        self.assertEqual(len(sleeps), parts - 1)

    def test_pacing_disabled_when_zero(self):
        sleeps, _ = self._upload(0)
        self.assertEqual(sleeps, [])


class TestTelegramAntiHang(unittest.TestCase):
    """Upload tidak boleh menggantung selamanya (insiden 30 Sep 2026).

    Gejala: notifikasi "UPLOAD TELEGRAM" terkirim, lalu tidak ada notifikasi
    apa pun selama 28 jam sementara video tidak pernah muncul di channel.
    Root cause: satu ``await`` RPC Telethon tidak pernah selesai maupun
    melempar error, sehingga status baris terkunci di ``uploading_telegram``
    (tak terlihat worker retry) dan lock upload membekukan notifikasi admin
    karena notifikasi memakai lock yang sama.
    """

    def _sender(self, client):
        sender = TelegramSender.__new__(TelegramSender)
        sender._client = client
        sender._connected = True
        return sender

    def test_rpc_timeout_becomes_stalled_error(self):
        """``_rpc`` mengubah asyncio.TimeoutError jadi TelegramUploadStalled."""
        from bot.telegram_sender import _rpc

        async def probe():
            with self.assertRaises(TelegramUploadStalled):
                await _rpc(asyncio.sleep(5), "mengirim pesan", 1)

        asyncio.run(probe())

    def test_rpc_returns_value_when_fast(self):
        from bot.telegram_sender import _rpc

        async def probe():
            return await _rpc(asyncio.sleep(0, result="ok"), "mengirim pesan", 5)

        self.assertEqual(asyncio.run(probe()), "ok")

    def test_notification_does_not_wait_for_upload_lock(self):
        """Notifikasi tidak boleh hostage oleh lock upload yang sedang dipegang."""
        client = _HangingClient()
        sender = self._sender(client)
        sender.connect = AsyncMock()

        async def run():
            # Simulasikan upload besar yang sedang memegang lock upload.
            await sender._upload_guard().acquire()
            try:
                sender._client.send_message = AsyncMock(return_value=_fake_message(7))
                with patch.object(Config, "TELEGRAM_NOTIFY_TIMEOUT_SECONDS", 2):
                    return await sender.send_message("halo", channel_id=123)
            finally:
                sender._upload_lock.release()

        self.assertEqual(asyncio.run(run()), 7)

    def test_upload_lock_is_released_after_stall(self):
        """Setelah timeout, lock upload WAJIB bisa dipakai lagi."""
        client = _HangingClient()
        sender = self._sender(client)

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                video = Path(directory) / "big.mp4"
                video.write_bytes(b"0" * (65 * 1024 * 1024))
                with patch.object(
                    Config, "TELEGRAM_PART_UPLOAD_TIMEOUT_SECONDS", 1
                ), patch.object(Config, "TELEGRAM_SEND_TIMEOUT_SECONDS", 1):
                    result = await sender.send_video_file(video, caption="x")
                # Timeout = gagal (return None), bukan menggantung.
                self.assertIsNone(result)
                self.assertFalse(sender._upload_guard().locked())

        asyncio.run(run())

    def test_stalled_upload_does_not_hammer_retries(self):
        """Timeout mengakhiri percobaan, bukan mengulang 3x tanpa jeda."""
        client = _HangingClient()
        sender = self._sender(client)

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                video = Path(directory) / "big.mp4"
                video.write_bytes(b"0" * (65 * 1024 * 1024))
                with patch.object(
                    Config, "TELEGRAM_PART_UPLOAD_TIMEOUT_SECONDS", 1
                ), patch.object(Config, "TELEGRAM_SEND_TIMEOUT_SECONDS", 1):
                    return await sender.send_video_file(video, caption="x")

        self.assertIsNone(asyncio.run(run()))
        self.assertEqual(
            len(client.requests), 1,
            "part yang menggantung harus dihentikan seketika, bukan diulang",
        )
        self.assertFalse(sender._upload_guard().locked())


if __name__ == "__main__":
    unittest.main()
