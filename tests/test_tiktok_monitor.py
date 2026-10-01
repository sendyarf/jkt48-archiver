"""
test_tiktok_monitor.py - Unit test alur pemantauan arsip TikTok.

Alur diuji ujung-ke-ujung dengan penyedia `fixture` (tanpa jaringan) dan stub
Telegram/YouTube: deteksi postingan & story, pembagian album foto per part,
status akhir `done`, round-robin antar akun, dry-run, dan retry upload.
"""
import asyncio
import time
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock
from unittest.mock import patch

from bot import database, tiktok_monitor
from bot.config import Config
from bot.tiktok_client import (
    BaseProvider,
    ProviderBlocked,
    ProviderError,
    RateLimiter,
    TikTokItem,
)
from bot.tiktok_media import MediaError, MediaPermanentError
from bot.tiktok_monitor import TikTokMonitor, item_from_db_row


def _make_image(path: Path, color: str = "red") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x568:d=1",
         "-frames:v", "1", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return path


def _make_video(path: Path, seconds: int = 2) -> Path:
    # `testsrc2` menghasilkan berkas ratusan KB seperti rekaman TikTok asli.
    # `testsrc` 320x240 lama hanya ~32 KB dan sengaja ditolak
    # `looks_like_media_error_page()` — ambang itu justru menangkap berkas
    # halaman tantangan TikTok yang dulu lolos terarsip (1 Okt 2026, 1,5 KB).
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc2=duration={seconds}:size=720x1280:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=800:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return path


class FakeTelegram:
    """Stub TelegramSender: mencatat media yang diterima tanpa hubungi API."""

    def __init__(self, fail_times: int = 0) -> None:
        self.albums: list[list[str]] = []
        self.videos: list[str] = []
        self.messages: list[str] = []
        self._fail_times = fail_times
        self._next_id = 100

    def _ids(self, count: int) -> list[int]:
        ids = list(range(self._next_id, self._next_id + count))
        self._next_id += count
        return ids

    async def send_tiktok_archive(self, media, *, post, account, channel_id=None):
        if self._fail_times > 0:
            self._fail_times -= 1
            return []
        if media.image_parts:
            for part in media.image_parts:
                self.albums.append([Path(p).name for p in part])
            return self._ids(len(media.image_parts))
        if media.archive_video_path is None:
            return []
        self.videos.append(Path(media.archive_video_path).name)
        return self._ids(1)

    async def send_message(self, text, channel_id=None, link_preview=True):
        self.messages.append(text)
        return 1

    async def disconnect(self) -> None:
        return None


class FakeYouTubePool:
    """Stub pool YouTube: mencatat upload + thumbnail tanpa token OAuth."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []
        self.thumbnails: list[str] = []

    @staticmethod
    def build_tiktok_title(member_name, posted_at=None, kind="video", is_story=False):
        return f"TIKTOK {kind.upper()} {member_name}"

    @staticmethod
    def build_tiktok_description(**kwargs) -> str:
        return "deskripsi uji"

    def upload_video(self, file_path, title, description=""):
        self.uploads.append((Path(file_path).name, title))
        return "yt-uji-1", "Channel Uji"

    def set_thumbnail(self, video_id, thumb_path, channel_label=""):
        self.thumbnails.append(str(thumb_path))
        return True


class TikTokMonitorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.base = base
        self._saved = {
            "DB_PATH": Config.DB_PATH,
            "DOWNLOAD_DIR": Config.DOWNLOAD_DIR,
            "TIKTOK_PROVIDER": Config.TIKTOK_PROVIDER,
            "TIKTOK_FIXTURE_DIR": Config.TIKTOK_FIXTURE_DIR,
            "TIKTOK_STORIES_ENABLED": Config.TIKTOK_STORIES_ENABLED,
            "TIKTOK_YT_UPLOAD_ENABLED": Config.TIKTOK_YT_UPLOAD_ENABLED,
            "AUTO_DELETE_AFTER_UPLOAD": Config.AUTO_DELETE_AFTER_UPLOAD,
            "THUMBNAIL_COLLAGE_ENABLED": Config.THUMBNAIL_COLLAGE_ENABLED,
            "TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO": Config.TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO,
            "TIKTOK_ACCOUNTS_PER_CHECK": Config.TIKTOK_ACCOUNTS_PER_CHECK,
            "TIKTOK_YT_BACKLOG_PER_CHECK": Config.TIKTOK_YT_BACKLOG_PER_CHECK,
            "TIKTOK_MAX_DOWNLOAD_ATTEMPTS": Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS,
            "TIKTOK_STORY_MAX_AGE_HOURS": Config.TIKTOK_STORY_MAX_AGE_HOURS,
        }
        Config.DB_PATH = str(base / "monitor.db")
        Config.DOWNLOAD_DIR = str(base / "downloads")
        Config.TIKTOK_PROVIDER = "fixture"
        Config.TIKTOK_FIXTURE_DIR = str(base / "fixtures")
        Config.TIKTOK_STORIES_ENABLED = True
        Config.TIKTOK_YT_UPLOAD_ENABLED = False
        Config.AUTO_DELETE_AFTER_UPLOAD = False
        Config.THUMBNAIL_COLLAGE_ENABLED = False
        Config.TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO = 1
        # Mayoritas tes menjalankan run_once per akun satu per satu — sematkan 1
        # agar perilaku lama tidak berubah; tes batch menyetelnya sendiri.
        Config.TIKTOK_ACCOUNTS_PER_CHECK = 1
        # Mayoritas tes memakai satu akun per siklus, jadi concurrency tidak
        # relevan; tes khusus batch menyetelnya sendiri.
        Config.TIKTOK_CONCURRENT_ACCOUNT_CHECKS = 1
        Config.TIKTOK_YT_BACKLOG_PER_CHECK = 2
        Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS = 6
        Config.TIKTOK_STORY_MAX_AGE_HOURS = 24
        (base / "fixtures").mkdir(parents=True, exist_ok=True)
        database.init_db()

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(Config, key, value)
        self._tmp.cleanup()

    # ── helper ──────────────────────────────────────────────────────────────
    def _write_fixture(self, unique_id: str, videos: list, stories: list) -> None:
        payload = {
            "account": {"unique_id": unique_id, "nickname": f"{unique_id} JKT48"},
            "videos": videos,
            "stories": stories,
        }
        (self.base / "fixtures" / f"{unique_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _photo_fixture_item(self, item_id: str, count: int = 12) -> dict:
        images = []
        for index in range(count):
            path = _make_image(
                self.base / f"{item_id}-{index}.jpg", ("red", "green", "blue")[index % 3]
            )
            images.append(str(path))
        return {
            "id": item_id,
            "kind": "photo",
            "title": f"foto {item_id}",
            "timestamp": 1789576980,
            "images": images,
        }

    def _video_fixture_item(self, item_id: str) -> dict:
        sample = _make_video(self.base / f"{item_id}.mp4", seconds=2)
        return {
            "id": item_id,
            "title": f"video {item_id}",
            "timestamp": 1789576980,
            "duration": 2,
            "fixture_media": {"video": str(sample)},
        }


class TestRunOnce(TikTokMonitorTestCase):
    def test_detects_and_archives_video_and_photo(self):
        self._write_fixture(
            "indahjkt48",
            [self._video_fixture_item("v1"), self._photo_fixture_item("p1", 12)],
            [],
        )
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48", "jkt48_indah")
        tg = FakeTelegram()
        monitor = TikTokMonitor(telegram=tg)

        processed = asyncio.run(monitor.run_once())
        self.assertEqual(processed, 2)

        video = database.get_tiktok_post("v1")
        self.assertEqual(video["status"], "done")
        self.assertTrue(video["telegram_message_ids"])
        self.assertTrue(Path(video["media_path"]).exists())

        photo = database.get_tiktok_post("p1")
        self.assertEqual(photo["status"], "done")
        self.assertEqual(photo["kind"], "photo")
        self.assertEqual(photo["image_count"], 12)
        # 12 foto → 2 album → 2 message_id (10 foto + 2 foto)
        self.assertEqual(len(photo["telegram_message_ids"].split(",")), 2)
        self.assertEqual([len(album) for album in tg.albums], [10, 2])
        # Slide show juga dibuat (dipakai YouTube bila diaktifkan).
        self.assertTrue(Path(photo["media_path"]).exists())

    def test_story_is_detected_and_marked(self):
        self._write_fixture("indahjkt48", [], [self._video_fixture_item("s1")])
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48", "jkt48_indah")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        asyncio.run(monitor.run_once())
        story = database.get_tiktok_post("s1")
        self.assertIsNotNone(story)
        self.assertEqual(story["is_story"], 1)
        self.assertEqual(story["status"], "done")

    def test_second_run_detects_nothing_new(self):
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        self.assertEqual(asyncio.run(monitor.run_once()), 1)
        self.assertEqual(asyncio.run(monitor.run_once()), 0)

    def test_round_robin_moves_to_next_account(self):
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        self._write_fixture("lulu_jkt48", [self._video_fixture_item("v2")], [])
        database.upsert_tiktok_account("indahjkt48")
        database.upsert_tiktok_account("lulu_jkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        asyncio.run(monitor.run_once())
        asyncio.run(monitor.run_once())
        self.assertTrue(database.tiktok_post_exists("v1"))
        self.assertTrue(database.tiktok_post_exists("v2"))

    def test_only_account_filter(self):
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        self._write_fixture("lulu_jkt48", [self._video_fixture_item("v2")], [])
        database.upsert_tiktok_account("indahjkt48")
        database.upsert_tiktok_account("lulu_jkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        asyncio.run(monitor.run_once(only_account="lulu_jkt48"))
        self.assertTrue(database.tiktok_post_exists("v2"))
        self.assertFalse(database.tiktok_post_exists("v1"))

    def test_dry_run_marks_detected_without_downloading(self):
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram(), dry_run=True)

        self.assertEqual(asyncio.run(monitor.run_once()), 1)
        row = database.get_tiktok_post("v1")
        self.assertEqual(row["status"], "detected")
        self.assertFalse(row["media_path"])

    def test_missing_fixture_account_is_skipped(self):
        database.upsert_tiktok_account("tidak-ada")
        monitor = TikTokMonitor(telegram=FakeTelegram())
        processed = asyncio.run(monitor.run_once())
        # Akun tanpa fixture = jawaban kosong yang sah, bukan crash: siklus
        # selesai tanpa memproses apa pun dan akun tetap dicatat sudah diperiksa.
        self.assertEqual(processed, 0)
        self.assertTrue(database.get_tiktok_account("tidak-ada")["last_checked_at"])

    def test_accounts_per_check_processes_a_batch(self):
        """TIKTOK_ACCOUNTS_PER_CHECK akun diperiksa dalam SATU siklus (rotasi cepat)."""
        Config.TIKTOK_ACCOUNTS_PER_CHECK = 2
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        self._write_fixture("lulu_jkt48", [self._video_fixture_item("v2")], [])
        database.upsert_tiktok_account("indahjkt48")
        database.upsert_tiktok_account("lulu_jkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        # Satu siklus langsung memeriksa dua akun (dulu butuh dua siklus).
        processed = asyncio.run(monitor.run_once())
        self.assertEqual(processed, 2)
        self.assertTrue(database.tiktok_post_exists("v1"))
        self.assertTrue(database.tiktok_post_exists("v2"))

    def test_batch_never_exceeds_account_count(self):
        """Daftar akun lebih sedikit daripada batch → tidak ada akun dobel."""
        Config.TIKTOK_ACCOUNTS_PER_CHECK = 5
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        processed = asyncio.run(monitor.run_once())
        self.assertEqual(processed, 1)


class TestFailureAndRetry(TikTokMonitorTestCase):
    def test_telegram_failure_queues_pending_then_retry_succeeds(self):
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48")

        failing = FakeTelegram(fail_times=1)
        monitor = TikTokMonitor(telegram=failing)
        asyncio.run(monitor.run_once())
        row = database.get_tiktok_post("v1")
        self.assertEqual(row["status"], "pending_upload")
        self.assertTrue(Path(row["media_path"]).exists())   # media tidak dihapus

        # Siklus berikutnya: Telegram normal → postingan tertunda diselesaikan.
        healthy = TikTokMonitor(telegram=FakeTelegram())
        done = asyncio.run(healthy.retry_pending())
        self.assertEqual(done, 1)
        self.assertEqual(database.get_tiktok_post("v1")["status"], "done")

    def test_youtube_upload_runs_when_enabled(self):
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._write_fixture("indahjkt48", [self._photo_fixture_item("p1", 3)], [])
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48")
        pool = FakeYouTubePool()
        monitor = TikTokMonitor(telegram=FakeTelegram(), youtube_pool=pool)

        asyncio.run(monitor.run_once())
        row = database.get_tiktok_post("p1")
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["youtube_video_id"], "yt-uji-1")
        self.assertEqual(len(pool.uploads), 1)
        self.assertTrue(pool.uploads[0][0].endswith(".mp4"))

    def test_auto_delete_removes_media_when_enabled(self):
        Config.AUTO_DELETE_AFTER_UPLOAD = True
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram())

        asyncio.run(monitor.run_once())
        row = database.get_tiktok_post("v1")
        self.assertEqual(row["status"], "done")
        self.assertFalse(Path(row["media_path"]).exists())

    def test_auto_delete_removes_media_after_successful_youtube(self):
        Config.AUTO_DELETE_AFTER_UPLOAD = True
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram(), youtube_pool=FakeYouTubePool())

        asyncio.run(monitor.run_once())
        row = database.get_tiktok_post("v1")
        self.assertEqual(row["youtube_video_id"], "yt-uji-1")
        self.assertFalse(Path(row["media_path"]).exists())

    def test_auto_delete_keeps_media_when_youtube_fails(self):
        """Regresi 22 Sep 2026: file dihapus saat YT gagal → backlog unduh
        ulang bisa mengambil varian berwatermark sehingga website beda
        dengan arsip Telegram. Media WAJIB tetap ada sampai YT sukses."""
        Config.AUTO_DELETE_AFTER_UPLOAD = True
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._write_fixture("indahjkt48", [self._video_fixture_item("v1")], [])
        database.upsert_tiktok_account("indahjkt48")
        monitor = TikTokMonitor(telegram=FakeTelegram(), youtube_pool=FailingYouTubePool())

        asyncio.run(monitor.run_once())
        row = database.get_tiktok_post("v1")
        self.assertEqual(row["status"], "done")
        self.assertTrue(row["telegram_message_ids"])
        self.assertFalse(row["youtube_video_id"])
        self.assertTrue(Path(row["media_path"]).exists())


class FailingYouTubePool(FakeYouTubePool):
    """Pool YouTube yang selalu gagal upload (mis. kuota harian habis)."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def upload_video(self, file_path, title, description=""):
        self.attempts += 1
        raise Exception("quotaExceeded: daily limit")


class TestYoutubeBacklog(TikTokMonitorTestCase):
    """Arsip yang sudah di Telegram tapi belum punya video YouTube dikejar ulang."""

    def _insert_telegram_only_post(self, post_id: str) -> Path:
        media = _make_video(self.base / f"{post_id}.mp4", seconds=1)
        database.insert_tiktok_post({
            "id": post_id,
            "unique_id": "indahjkt48",
            "kind": "video",
            "is_story": False,
            "title": f"video {post_id}",
            "created_at": "2026-09-20T00:00:00+00:00",
            "source_url": f"https://tiktok.invalid/{post_id}",
        })
        database.update_tiktok_post(
            post_id,
            status="done",
            telegram_message_ids="101",
            media_path=str(media),
        )
        return media

    def test_backlog_uploads_youtube_without_resending_telegram(self):
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._insert_telegram_only_post("b1")
        tg = FakeTelegram()
        pool = FakeYouTubePool()
        monitor = TikTokMonitor(telegram=tg, youtube_pool=pool)

        uploaded = asyncio.run(monitor.retry_youtube_backlog())
        self.assertEqual(uploaded, 1)
        row = database.get_tiktok_post("b1")
        self.assertEqual(row["youtube_video_id"], "yt-uji-1")
        self.assertEqual(row["status"], "done")
        # Telegram TIDAK dikirim ulang — arsipnya sudah ada di channel.
        self.assertEqual(tg.videos, [])
        self.assertEqual(tg.albums, [])

    def test_backlog_restores_status_and_stops_on_upload_failure(self):
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._insert_telegram_only_post("b1")
        self._insert_telegram_only_post("b2")
        pool = FailingYouTubePool()
        monitor = TikTokMonitor(telegram=FakeTelegram(), youtube_pool=pool)

        uploaded = asyncio.run(monitor.retry_youtube_backlog())
        self.assertEqual(uploaded, 0)
        # Berhenti di kegagalan pertama (kuota habis) — b2 tidak diotak-atik.
        self.assertEqual(pool.attempts, 1)
        row = database.get_tiktok_post("b1")
        self.assertEqual(row["status"], "done")
        self.assertFalse(row["youtube_video_id"])

    def test_backlog_skipped_when_youtube_disabled(self):
        self._insert_telegram_only_post("b1")
        monitor = TikTokMonitor(telegram=FakeTelegram())
        self.assertEqual(asyncio.run(monitor.retry_youtube_backlog()), 0)

    def test_backlog_query_filters_and_orders(self):
        self._insert_telegram_only_post("b1")
        self._insert_telegram_only_post("b2")
        database.update_tiktok_post("b2", created_at="2026-09-21T00:00:00+00:00")
        # Sudah punya YouTube → bukan backlog.
        self._insert_telegram_only_post("b3")
        database.update_tiktok_post("b3", youtube_video_id="sudah")
        # Ditahan pengelola → bukan backlog.
        self._insert_telegram_only_post("b4")
        database.update_tiktok_post("b4", visible=0)

        rows = database.get_tiktok_youtube_backlog()
        self.assertEqual([r["id"] for r in rows], ["b2", "b1"])  # terbaru dulu


class TestAllProvidersDownReporting(TikTokMonitorTestCase):
    """Ketika semua penyedia tumbang, log harus menyebut kondisi SETIAP penyedia.

    Insiden 1 Okt 2026: log hanya menyebut error penyedia TERAKHIR
    ("Semua penyedia gagal ... (yt-dlp listing gagal: JSONDecodeError)"), lalu
    diagnostik berjalan salah mengira yt-dlp penyebabnya. Padahal rantainya
    tikwm 403 → embed 503 → ytdlp: dua penyedia pertama sudah lama mati.
    """

    class BlockedProvider(BaseProvider):
        name = "tikwm-uji"

        def __init__(self):
            super().__init__(RateLimiter(0))

        async def fetch_user_posts(self, account, limit):
            raise ProviderBlocked("HTTP 403")

    class OverloadProvider(BaseProvider):
        name = "embed-uji"

        def __init__(self):
            super().__init__(RateLimiter(0))

        async def fetch_user_posts(self, account, limit):
            raise ProviderError("HTTP 503")

    class BrokenJsonProvider(BaseProvider):
        name = "ytdlp-uji"

        def __init__(self):
            super().__init__(RateLimiter(0))

        async def fetch_user_posts(self, account, limit):
            raise ProviderError("Failed to parse JSON")

    def _monitor(self, providers):
        monitor = TikTokMonitor(telegram=FakeTelegram())
        monitor._providers = providers  # type: ignore[assignment]
        return monitor

    def test_summary_names_every_provider_state(self):
        monitor = self._monitor([
            self.BlockedProvider(), self.OverloadProvider(), self.BrokenJsonProvider()
        ])
        with self.assertLogs("bot.tiktok_monitor", level="WARNING") as captured:
            items, provider = asyncio.run(
                monitor._collect({"unique_id": "freyajkt48"}, "fetch_user_posts", "posts", limit=10)
            )
        self.assertEqual(items, [])
        self.assertIsNone(provider)
        blob = "\n".join(captured.output)
        self.assertIn("tikwm-uji", blob)
        self.assertIn("embed-uji", blob)
        self.assertIn("ytdlp-uji", blob)
        self.assertIn("403", blob)
        self.assertIn("503", blob)
        self.assertIn("Failed to parse JSON", blob)

    def test_repeated_5xx_closes_health_gate(self):
        """503 berulang harus menutup penyedia, bukan ditembak 51× per siklus."""
        monitor = self._monitor([self.OverloadProvider()])
        provider = monitor._providers[0]
        account = {"unique_id": "freyajkt48"}

        with mock.patch.object(
            Config, "TIKTOK_PROVIDER_ERROR_STREAK_BEFORE_UNHEALTHY", 3
        ):
            for _ in range(2):
                asyncio.run(monitor._collect(account, "fetch_user_posts", "posts", limit=10))
                self.assertTrue(
                    provider.is_healthy("posts"),
                    "dua kegagalan belum cukup untuk menutup penyedia",
                )
            asyncio.run(monitor._collect(account, "fetch_user_posts", "posts", limit=10))

        self.assertFalse(
            provider.is_healthy("posts"),
            "setelah ambang tercapai, health gate harus menutup",
        )

    def test_streak_resets_after_success(self):
        monitor = self._monitor([self.OverloadProvider()])
        provider = monitor._providers[0]
        account = {"unique_id": "freyajkt48"}
        with mock.patch.object(
            Config, "TIKTOK_PROVIDER_ERROR_STREAK_BEFORE_UNHEALTHY", 3
        ):
            asyncio.run(monitor._collect(account, "fetch_user_posts", "posts", limit=10))
            asyncio.run(monitor._collect(account, "fetch_user_posts", "posts", limit=10))
            self.assertEqual(
                monitor._provider_error_streak[("embed-uji", "posts")], 2
            )

            # Pulih: penyedia dengan nama sama berhasil → streak harus bersih.
            class RecoveredProvider(BaseProvider):
                name = "embed-uji"

                def __init__(self):
                    super().__init__(RateLimiter(0))

                async def fetch_user_posts(self, account, limit):
                    return [TikTokItem(id="p1", unique_id="freyajkt48", kind="video")]

            monitor._providers = [RecoveredProvider()]  # type: ignore[assignment]
            items, _ = asyncio.run(
                monitor._collect(account, "fetch_user_posts", "posts", limit=10)
            )
        self.assertEqual([i.id for i in items], ["p1"])
        self.assertNotIn(("embed-uji", "posts"), monitor._provider_error_streak)


class TestScrapeItemsGetEnriched(TikTokMonitorTestCase):
    """Posting dari `scrape` hanya berisi id+waktu, jadi WAJIB diperkaya.

    `parse_scrape_profile` mengisi `created_at` dari snowflake ID (`id >> 32`),
    jadi setiap item hasil scrape SELALU punya `created_at`. Syarat enrich yang
    lama hanya mengecek `created_at`, sehingga posting seperti ini dilewati
    dan langsung gagal saat unduhan karena `video_url` serta foto kosong. Gejalanya
    persis yang dilaporkan: baris muncul di website, isinya tidak ada.
    """

    def test_item_dengan_created_at_tanpa_media_masih_di_enrich(self):
        class DetailProvider(BaseProvider):
            name = "tikwm-uji"

            def __init__(self):
                super().__init__(RateLimiter(0))
                self.calls = 0

            async def fetch_user_posts(self, account, limit):
                return []

            async def fetch_item_detail(self, page_url, unique_id):
                self.calls += 1
                return TikTokItem(
                    id=page_url.rsplit("/", 1)[-1], unique_id=unique_id,
                    kind="video", video_url="https://cdn.example/x.mp4",
                    cover_url="https://cdn.example/x.jpg",
                )

        monitor = TikTokMonitor(telegram=FakeTelegram())
        detail = DetailProvider()
        monitor._providers = [detail]  # type: ignore[assignment]

        item = TikTokItem(
            id="7431206717478159366", unique_id="jkt48.fahira", kind="video",
            created_at="2026-10-01T07:56:22+00:00",  # sudah terisi (dari snowflake)
        )
        asyncio.run(monitor._enrich_new_items([item], {"unique_id": "jkt48.fahira"}))

        self.assertEqual(detail.calls, 1, "item tanpa sumber media harus di-enrich")
        self.assertEqual(item.video_url, "https://cdn.example/x.mp4")
        self.assertEqual(item.cover_url, "https://cdn.example/x.jpg")

    def test_item_lengkap_tidak_di_enrich_berlebihan(self):
        """Posting yang sudah punya media tidak boleh menghabiskan kuota detail."""
        class DetailProvider(BaseProvider):
            name = "tikwm-uji"

            def __init__(self):
                super().__init__(RateLimiter(0))
                self.calls = 0

            async def fetch_user_posts(self, account, limit):
                return []

            async def fetch_item_detail(self, page_url, unique_id):
                self.calls += 1
                return None

        monitor = TikTokMonitor(telegram=FakeTelegram())
        detail = DetailProvider()
        monitor._providers = [detail]  # type: ignore[assignment]

        item = TikTokItem(
            id="7431206717478159367", unique_id="jkt48.fahira", kind="video",
            created_at="2026-10-01T07:56:22+00:00",
            video_url="https://cdn.example/y.mp4", cover_url="https://cdn.example/y.jpg",
        )
        asyncio.run(monitor._enrich_new_items([item], {"unique_id": "jkt48.fahira"}))

        self.assertEqual(detail.calls, 0, "media sudah ada, jangan=request detail")


class TestConcurrentAccountChecks(unittest.TestCase):
    """Beberapa akun boleh diperiksa bersamaan tanpa menaikkan laju request.

    Semua provider berbagi satu `RateLimiter` ber-lock, jadi request ke TikTok
    tetap berjarak `TIKTOK_REQUEST_INTERVAL_SECONDS` meski checking paralel.
    Yang selama ini terbuang adalah waktu menganggur: akun pertama memblokir
    akun berikutnya selama unduhan media + unggah.
    """

    def setUp(self):
        self._saved = {
            key: getattr(Config, key)
            for key in (
                "TIKTOK_ACCOUNTS_PER_CHECK",
                "TIKTOK_CONCURRENT_ACCOUNT_CHECKS",
                "TIKTOK_REQUEST_INTERVAL_SECONDS",
            )
        }
        Config.TIKTOK_ACCOUNTS_PER_CHECK = 3
        Config.TIKTOK_REQUEST_INTERVAL_SECONDS = 0.0
        # Disetel eksplisit: kelas lain mengesetnya ke 1, dan setUp-nya
        # hanya menyimpannya, jadi nilai yang diwarisi bisa saja 1.
        Config.TIKTOK_CONCURRENT_ACCOUNT_CHECKS = 2

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(Config, key, value)

    def _monitor(self, accounts, worker):
        monitor = TikTokMonitor(telegram=FakeTelegram())
        monitor._providers = []  # type: ignore[assignment]

        async def fake_check(account):
            return await worker(account)

        monitor._check_account = fake_check  # type: ignore[method-assign]

        original = database.get_tiktok_accounts
        database.get_tiktok_accounts = lambda: accounts  # type: ignore[assignment]
        return monitor, original

    def test_akun_diperiksa_bersamaan(self):
        started = asyncio.Event()
        active = {"now": 0, "peak": 0}
        seen: list[str] = []

        async def worker(account):
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            seen.append(account["unique_id"])
            # Tahan sampai dua akun lain ikut mulai -> hanya mungkin kalau
            # pemeriksaan tidak berurutan.
            if active["now"] >= 3:
                started.set()
            try:
                await asyncio.wait_for(started.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            active["now"] -= 1
            return 1

        accounts = [{"unique_id": f"akun{i}"} for i in range(3)]
        monitor, original = self._monitor(accounts, worker)
        try:
            total = asyncio.run(monitor.run_once())
        finally:
            database.get_tiktok_accounts = original  # type: ignore[assignment]

        self.assertEqual(active["peak"], 3, "tiga akun harus berjalan bersamaan")
        self.assertEqual(sorted(seen), ["akun0", "akun1", "akun2"])
        self.assertEqual(total, 3)

    def test_satu_akun_gagal_tidak_mematikan_sisanya(self):
        async def worker(account):
            if account["unique_id"] == "akun1":
                raise RuntimeError("provider meledak")
            return 2

        accounts = [{"unique_id": f"akun{i}"} for i in range(3)]
        monitor, original = self._monitor(accounts, worker)
        try:
            total = asyncio.run(monitor.run_once())
        finally:
            database.get_tiktok_accounts = original  # type: ignore[assignment]

        self.assertEqual(
            total, 4,
            "dua akun yang sehat tetap dihitung meski satu meledak",
        )

    def test_concurrency_satu_menampilkan_perilaku_lama(self):
        """Nilai 1 harus kembali ke pemeriksaan berurutan."""
        order: list[str] = []

        async def worker(account):
            order.append("start:" + account["unique_id"])
            await asyncio.sleep(0)
            order.append("end:" + account["unique_id"])
            return 0

        Config.TIKTOK_CONCURRENT_ACCOUNT_CHECKS = 1
        accounts = [{"unique_id": f"akun{i}"} for i in range(3)]
        monitor, original = self._monitor(accounts, worker)
        try:
            asyncio.run(monitor.run_once())
        finally:
            database.get_tiktok_accounts = original  # type: ignore[assignment]

        self.assertEqual(
            order,
            ["start:akun0", "end:akun0", "start:akun1", "end:akun1",
             "start:akun2", "end:akun2"],
        )

    def test_kursor_tetap_berputar_sesuai_rotasi(self):
        seen: list[str] = []

        async def worker(account):
            seen.append(account["unique_id"])
            return 0

        Config.TIKTOK_CONCURRENT_ACCOUNT_CHECKS = 2
        Config.TIKTOK_ACCOUNTS_PER_CHECK = 2
        accounts = [{"unique_id": f"akun{i}"} for i in range(3)]
        monitor, original = self._monitor(accounts, worker)
        try:
            asyncio.run(monitor.run_once())
            asyncio.run(monitor.run_once())
        finally:
            database.get_tiktok_accounts = original  # type: ignore[assignment]

        self.assertEqual(
            seen, ["akun0", "akun1", "akun2", "akun0"],
            "rotasi round-robin harus tetap berjalan meski pemrosesan paralel: "
            "siklus 1 ambil akun0+akun1, siklus 2 lanjut akun2+akun0",
        )


class TestRateLimiterNeverBelowMinimum(unittest.TestCase):
    """Jitter tidak boleh membuat interval turun di bawah batas tikwm.

    Batas gratis tikwm = 1 request/detik. Versi lama mengalikan `delay` dengan
    uniform(0,75, 1,25), sehingga dua request beruntun bisa berjarak
    1,1 x 0,75 = 0,825 detik - DI BAWAH batas. tikwm lalu membalas
    "Free Api Limit: 1 request/second." dan bot mengeskalasi jeda 5 detik,
    persis yang terlihat di log 1 Okt 2026 pukul 17:07.
    """

    def test_interval_tidak_pernah_di_bawah_minimum(self):
        limiter = RateLimiter(1.1)
        gaps: list[float] = []

        async def run():
            # Panggilan pertama dilewati: limiter belum pernah dipakai, jadi
            # `_last_at` masih 0 dan tidak ada jeda yang perlu dijaga.
            await limiter.wait()
            for _ in range(12):
                before = time.monotonic()
                await limiter.wait()
                gaps.append(time.monotonic() - before)

        asyncio.run(run())
        worst = min(gaps)
        self.assertGreaterEqual(
            worst, 1.1,
            f"interval terpendek {worst:.3f} dtk di bawah minimum 1,1 dtk",
        )

    def test_jitter_masih_bervariasi(self):
        """Jitter harus tetap ada: 51 akun dengan jeda identik terlihat metronomik."""
        limiter = RateLimiter(0.05)
        gaps: list[float] = []

        async def run():
            await limiter.wait()
            for _ in range(25):
                before = time.monotonic()
                await limiter.wait()
                gaps.append(time.monotonic() - before)

        asyncio.run(run())
        self.assertGreaterEqual(
            min(gaps), 0.05, "jitter tidak boleh mengurangi minimum"
        )
        self.assertGreater(
            max(gaps), min(gaps) * 1.1,
            "jeda harus bervariasi, tidak selalu sama (pola metronom)",
        )

    def test_nol_interval_tetap_boleh(self):
        limiter = RateLimiter(0)

        async def run():
            await limiter.wait()
            await limiter.wait()

        asyncio.run(run())  # tidak boleh menggantung


class TestSplitCapabilities(TikTokMonitorTestCase):
    """
    Kasus nyata 21 Sep 2026: Cloudflare memblokir `/user/posts` (403) sementara
    `/user/story` tetap 200. Bot WAJIB tetap mengambil story pada siklus yang sama
    — bukan melewatkannya karena judging satu flag global.
    """

    class BlockedPostsProvider(BaseProvider):
        name = "tikwm-uji"

        def __init__(self):
            super().__init__(RateLimiter(0))
            self.story_calls = 0

        async def fetch_user_posts(self, account, limit):
            raise ProviderBlocked("tikwm /user/posts: HTTP 403")

        async def fetch_user_stories(self, account):
            self.story_calls += 1
            return [
                TikTokItem(
                    id="story-1", unique_id="indahjkt48", kind="video",
                    title="story latihan", video_url="x", is_story=True,
                )
            ]

    def test_stories_still_collected_when_posts_blocked(self):
        self._write_fixture("indahjkt48", [], [])
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48")
        provider = self.BlockedPostsProvider()
        monitor = TikTokMonitor(telegram=FakeTelegram())
        monitor._providers = [provider]   # type: ignore[assignment]

        items = asyncio.run(monitor._fetch_items({"unique_id": "indahjkt48"}))

        self.assertEqual([i.id for i in items], ["story-1"])
        self.assertEqual(provider.story_calls, 1, "story harus tetap diambil")
        self.assertFalse(provider.is_healthy("posts"))
        self.assertTrue(provider.is_healthy("stories"))

    def test_posts_and_stories_can_come_from_different_providers(self):
        """Listing dari penyedia A (embed) + story dari penyedia B (tikwm)."""

        class PostsOnlyProvider(BaseProvider):
            name = "embed-uji"

            async def fetch_user_posts(self, account, limit):
                return [TikTokItem(id="post-1", unique_id="indahjkt48", kind="video")]

        class StoriesOnlyProvider(BaseProvider):
            name = "tikwm-uji"

            async def fetch_user_stories(self, account):
                return [TikTokItem(id="story-1", unique_id="indahjkt48", is_story=True)]

        monitor = TikTokMonitor(telegram=FakeTelegram())
        monitor._providers = [PostsOnlyProvider(RateLimiter(0)),
                              StoriesOnlyProvider(RateLimiter(0))]  # type: ignore[assignment]

        items = asyncio.run(monitor._fetch_items({"unique_id": "indahjkt48"}))
        self.assertEqual(sorted(i.id for i in items), ["post-1", "story-1"])

    def test_duplicate_ids_are_deduplicated(self):
        class BothProvider(BaseProvider):
            name = "ganda"

            async def fetch_user_posts(self, account, limit):
                return [TikTokItem(id="sama", unique_id="indahjkt48")]

            async def fetch_user_stories(self, account):
                return [TikTokItem(id="sama", unique_id="indahjkt48", is_story=True)]

        monitor = TikTokMonitor(telegram=FakeTelegram())
        monitor._providers = [BothProvider(RateLimiter(0))]  # type: ignore[assignment]
        items = asyncio.run(monitor._fetch_items({"unique_id": "indahjkt48"}))
        self.assertEqual(len(items), 1)


class TestItemFromDbRow(TikTokMonitorTestCase):
    def test_rebuilds_item_for_retry(self):
        database.insert_tiktok_post({
            "id": "p9", "unique_id": "indahjkt48", "kind": "photo",
            "is_story": False, "title": "foto", "created_at": "2026-09-16T00:00:00+00:00",
            "image_count": 2, "source_url": "https://tiktok.invalid/p9",
            "images": ["a.jpg", "b.jpg"],
        })
        item = item_from_db_row(database.get_tiktok_post("p9"))
        self.assertEqual(item.id, "p9")
        self.assertEqual(item.kind, "photo")
        self.assertEqual(item.images, ["a.jpg", "b.jpg"])
        self.assertEqual(item.page_url, "https://tiktok.invalid/p9")


class TestAccountsWithoutMember(TikTokMonitorTestCase):
    """Akun cadangan/tak berpasangan (`member_username` NULL) harus tetap aman.

    Di produksi `jkt48.u16` dan `jkt48.aurellia_` sengaja tidak dipetakan ke
    satu member, jadi siklus round-robin mustahil menghindarinya.
    """

    def test_unmatched_account_still_archives_from_scratch(self):
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._write_fixture("jkt48.u16", [self._video_fixture_item("u1")], [])
        database.upsert_tiktok_account("jkt48.u16")  # member_username = NULL
        row = database.get_tiktok_account("jkt48.u16")
        self.assertIsNone(row["member_username"])

        tg = FakeTelegram()
        pool = FakeYouTubePool()
        monitor = TikTokMonitor(telegram=tg, youtube_pool=pool)
        asyncio.run(monitor.run_once())

        post = database.get_tiktok_post("u1")
        self.assertEqual(post["status"], "done")
        self.assertEqual(len(tg.videos), 1)
        self.assertTrue(tg.videos[0].endswith(".mp4"))
        self.assertEqual(pool.uploads[0][1], "TIKTOK VIDEO jkt48.u16")
        self.assertNotIn("None", pool.uploads[0][1])

    def test_unmatched_account_reports_pending_then_retries(self):
        self._write_fixture("jkt48.u16", [self._photo_fixture_item("u2", 3)], [])
        database.upsert_tiktok_account("jkt48.u16")

        failing = FakeTelegram(fail_times=1)
        asyncio.run(TikTokMonitor(telegram=failing).run_once())
        self.assertEqual(database.get_tiktok_post("u2")["status"], "pending_upload")

        done = asyncio.run(TikTokMonitor(telegram=FakeTelegram()).retry_pending())
        self.assertEqual(done, 1)
        self.assertEqual(database.get_tiktok_post("u2")["status"], "done")


class _FakePreparedMedia:
    """PreparedMedia minimal dari file lokal (tanpa unduh jaringan)."""

    def __init__(self, path: Path) -> None:
        self.video_path = path
        self.images: list[Path] = []
        self.image_parts: list[list[Path]] = []
        self.duration_seconds = 1.0
        self.size_bytes = path.stat().st_size
        self.archive_video_path = path


class TestDownloadAttemptsFlow(TikTokMonitorTestCase):
    """Penghitung percobaan unduh: retry sementara vs gagal permanen."""

    def _insert_failed_ready_post(self, post_id: str, **extra) -> None:
        database.insert_tiktok_post({
            "id": post_id, "unique_id": "indahjkt48", "kind": "video",
            "is_story": extra.pop("is_story", False),
            "title": f"video {post_id}",
            "created_at": extra.pop("created_at", "2026-09-20T00:00:00+00:00"),
            "source_url": f"https://tiktok.invalid/{post_id}",
        })
        if extra:
            database.update_tiktok_post(post_id, **extra)

    def _run_process(self, post_id: str, exc: Optional[BaseException]) -> None:
        """Jalankan _process_item dengan prepare_post_media yang di-stub."""
        original = tiktok_monitor.prepare_post_media

        async def fake_prepare(item, build_slideshow_video=True):
            if exc is not None:
                raise exc
            return _FakePreparedMedia(self.base / f"{post_id}.mp4")

        tiktok_monitor.prepare_post_media = fake_prepare  # type: ignore[assignment]
        try:
            _make_video(self.base / f"{post_id}.mp4", seconds=1)
            item = item_from_db_row(database.get_tiktok_post(post_id))
            asyncio.run(
                TikTokMonitor(telegram=FakeTelegram())._process_item(
                    item, {"unique_id": "indahjkt48", "display_name": ""}
                )
            )
        finally:
            tiktok_monitor.prepare_post_media = original  # type: ignore[assignment]

    def test_transient_media_error_counts_and_stays_retryable(self):
        self._insert_failed_ready_post("t1")
        self._run_process("t1", MediaError("sementara"))
        row = database.get_tiktok_post("t1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["download_attempts"], 1)
        self.assertTrue(row["last_attempt_at"])
        # Masih di bawah batas → diambil kembali oleh retry (umur dipalsukan tua).
        database.update_tiktok_post("t1", last_attempt_at="2000-01-01 00:00:00")
        rows = database.get_tiktok_retryable_failed(
            limit=5, max_attempts=Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS, min_age_seconds=900
        )
        self.assertEqual([r["id"] for r in rows], ["t1"])

    def test_permanent_media_error_is_terminal_immediately(self):
        self._insert_failed_ready_post("p1")
        self._run_process("p1", MediaPermanentError("IP address is blocked"))
        row = database.get_tiktok_post("p1")
        self.assertEqual(row["status"], "failed")
        # Kegagalan permanen langsung menghabiskan kuota percobaan.
        self.assertEqual(row["download_attempts"], Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS)
        database.update_tiktok_post("p1", last_attempt_at="2000-01-01 00:00:00")
        self.assertEqual(
            database.get_tiktok_retryable_failed(limit=5, max_attempts=6, min_age_seconds=0),
            [],
        )

    def test_expired_story_failure_is_permanent_immediately(self):
        self._insert_failed_ready_post(
            "s1", is_story=True, created_at="2020-01-01T00:00:00+00:00"
        )
        self._run_process("s1", MediaError("sementara"))
        row = database.get_tiktok_post("s1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["download_attempts"], Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS)

    def test_unexpected_error_counts_eventually_dead_letters(self):
        self._insert_failed_ready_post("u1")
        Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS = 2
        for _ in range(2):
            self._run_process("u1", RuntimeError("boom"))
        row = database.get_tiktok_post("u1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["download_attempts"], 2)
        database.update_tiktok_post("u1", last_attempt_at="2000-01-01 00:00:00")
        self.assertEqual(
            database.get_tiktok_retryable_failed(limit=5, max_attempts=2, min_age_seconds=0),
            [],
        )

    def test_retry_pending_picks_up_retryable_failed(self):
        self._insert_failed_ready_post("f1", status="failed", download_attempts=1)
        database.update_tiktok_post("f1", last_attempt_at="2000-01-01 00:00:00")
        original = tiktok_monitor.prepare_post_media

        async def ok_prepare(item, build_slideshow_video=True):
            return _FakePreparedMedia(self.base / "f1.mp4")

        _make_video(self.base / "f1.mp4", seconds=1)
        tiktok_monitor.prepare_post_media = ok_prepare  # type: ignore[assignment]
        try:
            done = asyncio.run(TikTokMonitor(telegram=FakeTelegram()).retry_pending())
        finally:
            tiktok_monitor.prepare_post_media = original  # type: ignore[assignment]
        self.assertEqual(done, 1)
        self.assertEqual(database.get_tiktok_post("f1")["status"], "done")


class TestBacklogDownloadAttempts(TestYoutubeBacklog):
    """Aturan baru backlog YouTube: percobaan habis/error permanen → dilewati."""

    def test_backlog_skips_exhausted_attempts_permanently(self):
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._insert_telegram_only_post("x1")
        database.update_tiktok_post("x1", media_path="")  # media hilang → unduh ulang
        database.update_tiktok_post("x1", download_attempts=Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS)
        pool = FakeYouTubePool()
        monitor = TikTokMonitor(telegram=FakeTelegram(), youtube_pool=pool)

        with self.assertLogs("bot.tiktok_monitor", level="INFO") as captured:
            uploaded = asyncio.run(monitor.retry_youtube_backlog())
        self.assertEqual(uploaded, 0)
        self.assertEqual(pool.uploads, [])
        self.assertTrue(any("dilewati permanen" in m for m in captured.output))
        row = database.get_tiktok_post("x1")
        self.assertEqual(row["status"], "done")  # status backlog tidak diubah
        self.assertFalse(row["youtube_video_id"])

    def test_backlog_media_error_counts_attempt_and_continues(self):
        Config.TIKTOK_YT_UPLOAD_ENABLED = True
        self._insert_telegram_only_post("x1")
        database.update_tiktok_post("x1", media_path="")
        self._insert_telegram_only_post("x2")
        original = tiktok_monitor.prepare_post_media
        calls: list[str] = []

        async def flaky(item, build_slideshow_video=True):
            calls.append(item.id)
            if item.id == "x1":
                raise MediaError("sementara")
            return _FakePreparedMedia(self.base / "x2.mp4")

        tiktok_monitor.prepare_post_media = flaky  # type: ignore[assignment]
        try:
            monitor = TikTokMonitor(telegram=FakeTelegram(), youtube_pool=FakeYouTubePool())
            uploaded = asyncio.run(monitor.retry_youtube_backlog())
        finally:
            tiktok_monitor.prepare_post_media = original  # type: ignore[assignment]

        # Kegagalan media TIDAK menghentikan antrean (beda dengan gagal upload).
        self.assertEqual(calls, ["x1"])
        self.assertEqual(uploaded, 1)
        row = database.get_tiktok_post("x1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["download_attempts"], 1)


class TestEnrichCapabilityScoping(TikTokMonitorTestCase):
    """Blokir detail-fetch hanya menandai kapabilitas 'posts', bukan semua."""

    class BlockedDetailProvider(BaseProvider):
        name = "embed-uji"

        def __init__(self):
            super().__init__(RateLimiter(0))

        async def fetch_user_posts(self, account, limit):
            return [TikTokItem(id="d1", unique_id="indahjkt48", kind="photo")]

        async def fetch_item_detail(self, page_url, unique_id):
            raise ProviderBlocked("HTTP 403")

    def test_blocked_detail_marks_only_posts_capability(self):
        database.upsert_tiktok_account("indahjkt48")
        provider = self.BlockedDetailProvider()
        monitor = TikTokMonitor(telegram=FakeTelegram())
        monitor._providers = [provider]  # type: ignore[assignment]
        item = TikTokItem(id="d1", unique_id="indahjkt48", kind="photo")

        asyncio.run(monitor._enrich_new_items([item], {"unique_id": "indahjkt48"}))

        self.assertFalse(provider.is_healthy("posts"))
        # Kapabilitas lain TIDAK ikut ditandai tidak sehat.
        self.assertTrue(provider.is_healthy("stories"))
        self.assertTrue(provider.is_healthy())


if __name__ == "__main__":
    unittest.main()

