"""
test_tiktok_monitor.py - Unit test alur pemantauan arsip TikTok.

Alur diuji ujung-ke-ujung dengan penyedia `fixture` (tanpa jaringan) dan stub
Telegram/YouTube: deteksi postingan & story, pembagian album foto per part,
status akhir `done`, round-robin antar akun, dry-run, dan retry upload.
"""
import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from bot import database
from bot.config import Config
from bot.tiktok_client import BaseProvider, ProviderBlocked, RateLimiter, TikTokItem
from bot.tiktok_monitor import TikTokMonitor, item_from_db_row


def _make_image(path: Path, color: str = "red") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x568:d=1",
         "-frames:v", "1", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return path


def _make_video(path: Path, seconds: int = 2) -> Path:
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=800:duration={seconds}",
         "-c:v", "libx264", "-c:a", "aac", str(path)],
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


if __name__ == "__main__":
    unittest.main()

