"""
test_tiktok_database.py - Unit test tabel & akses data arsip TikTok.

Memakai database sementara (Config.DB_PATH diarahkan ke tempfile) sehingga tidak
menyentuh database produksi.
"""
import json
import tempfile
import unittest
from pathlib import Path

from bot import database
from bot.config import Config


class TikTokDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = Config.DB_PATH
        Config.DB_PATH = str(Path(self._tmp.name) / "tiktok.db")
        database.init_db()

    def tearDown(self):
        Config.DB_PATH = self._old_db
        self._tmp.cleanup()

    @staticmethod
    def _post(post_id: str = "111", **kwargs) -> dict:
        base = {
            "id": post_id,
            "unique_id": "indahjkt48",
            "kind": "video",
            "is_story": False,
            "title": "twinnie",
            "created_at": "2026-09-16T15:23:00+00:00",
            "duration_seconds": 12,
            "image_count": 0,
            "cover_url": "https://cdn.invalid/c.jpg",
            "source_url": f"https://www.tiktok.com/@indahjkt48/video/{post_id}",
            "images": [],
        }
        base.update(kwargs)
        return base


class TestAccounts(TikTokDatabaseTestCase):
    def test_upsert_and_get(self):
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48", "jkt48_indah")
        accounts = database.get_tiktok_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["display_name"], "Indah JKT48")
        self.assertEqual(accounts[0]["member_username"], "jkt48_indah")
        self.assertEqual(accounts[0]["enabled"], 1)

    def test_upsert_does_not_overwrite_names(self):
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48", "jkt48_indah")
        database.upsert_tiktok_account("indahjkt48", "Nama Baru", "jkt48_lain")
        row = database.get_tiktok_account("indahjkt48")
        self.assertEqual(row["display_name"], "Indah JKT48")
        self.assertEqual(row["member_username"], "jkt48_indah")

    def test_disabled_accounts_are_excluded_by_default(self):
        database.upsert_tiktok_account("a_one", "Satu")
        database.upsert_tiktok_account("b_two", "Dua")
        database.set_tiktok_account_enabled("b_two", False)
        self.assertEqual([a["unique_id"] for a in database.get_tiktok_accounts()], ["a_one"])
        self.assertEqual(len(database.get_tiktok_accounts(include_disabled=True)), 2)

    def test_enable_switch_reports_missing_row(self):
        self.assertFalse(database.set_tiktok_account_enabled("tidak-ada", False))
        database.upsert_tiktok_account("a_one")
        self.assertTrue(database.set_tiktok_account_enabled("a_one", False))

    def test_meta_update_only_touches_given_fields(self):
        database.upsert_tiktok_account("indahjkt48", "Indah JKT48", "jkt48_indah")
        database.set_tiktok_account_meta("indahjkt48", sec_uid="SEC123")
        row = database.get_tiktok_account("indahjkt48")
        self.assertEqual(row["sec_uid"], "SEC123")
        self.assertEqual(row["display_name"], "Indah JKT48")

    def test_username_is_normalized(self):
        database.upsert_tiktok_account("@IndahJKT48")
        self.assertIsNotNone(database.get_tiktok_account("indahjkt48"))

    def test_mark_checked_keeps_latest_post_time(self):
        database.upsert_tiktok_account("indahjkt48")
        database.mark_tiktok_account_checked("indahjkt48", "2026-09-16T15:23:00+00:00")
        database.mark_tiktok_account_checked("indahjkt48", "2026-09-10T00:00:00+00:00")
        row = database.get_tiktok_account("indahjkt48")
        self.assertEqual(row["last_post_at"], "2026-09-16T15:23:00+00:00")
        self.assertTrue(row["last_checked_at"])


class TestPosts(TikTokDatabaseTestCase):
    def test_insert_and_exists(self):
        database.insert_tiktok_post(self._post("111"))
        self.assertTrue(database.tiktok_post_exists("111"))
        self.assertFalse(database.tiktok_post_exists("999"))
        row = database.get_tiktok_post("111")
        self.assertEqual(row["title"], "twinnie")
        self.assertEqual(row["status"], "detected")

    def test_insert_is_idempotent(self):
        database.insert_tiktok_post(self._post("111"))
        database.update_tiktok_post("111", status="done", telegram_message_ids="5")
        database.insert_tiktok_post(self._post("111", title="judul lain"))
        row = database.get_tiktok_post("111")
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["telegram_message_ids"], "5")
        self.assertEqual(row["title"], "twinnie")

    def test_update_rejects_unknown_column(self):
        database.insert_tiktok_post(self._post("111"))
        with self.assertRaises(ValueError):
            database.update_tiktok_post("111", tidak_ada=1)

    def test_update_accepts_whitelisted_columns(self):
        database.insert_tiktok_post(self._post("111"))
        database.update_tiktok_post(
            "111", status="uploading_youtube", youtube_video_id="yt1",
            media_path="/tmp/a.mp4", media_size_bytes=123, visible=1,
        )
        row = database.get_tiktok_post("111")
        self.assertEqual(row["youtube_video_id"], "yt1")
        self.assertEqual(row["media_size_bytes"], 123)

    def test_photos_are_stored_as_json(self):
        database.insert_tiktok_post(
            self._post("222", kind="photo", image_count=12, images=["a.jpg", "b.jpg"])
        )
        row = database.get_tiktok_post("222")
        self.assertEqual(row["kind"], "photo")
        self.assertEqual(row["image_count"], 12)
        self.assertEqual(json.loads(row["images_json"]), ["a.jpg", "b.jpg"])

    def test_listing_is_newest_first_and_filters_hidden(self):
        database.insert_tiktok_post(self._post("old", created_at="2026-09-10T00:00:00+00:00"))
        database.insert_tiktok_post(self._post("new", created_at="2026-09-20T00:00:00+00:00"))
        database.insert_tiktok_post(self._post("hidden", created_at="2026-09-21T00:00:00+00:00"))
        database.update_tiktok_post("hidden", visible=0)

        self.assertEqual([p["id"] for p in database.get_tiktok_posts()], ["new", "old"])
        self.assertEqual(
            [p["id"] for p in database.get_tiktok_posts(include_hidden=True)],
            ["hidden", "new", "old"],
        )

    def test_listing_can_be_filtered_by_account(self):
        database.insert_tiktok_post(self._post("a1"))
        database.insert_tiktok_post(self._post("b1", unique_id="jkt48.aralie"))
        ids = [p["id"] for p in database.get_tiktok_posts(unique_id="jkt48.aralie")]
        self.assertEqual(ids, ["b1"])

    def test_pending_posts_need_media_on_disk(self):
        database.insert_tiktok_post(self._post("p1"))
        database.update_tiktok_post("p1", status="pending_upload", media_path="/tmp/a.mp4")
        database.insert_tiktok_post(self._post("p2"))
        database.update_tiktok_post("p2", status="pending_upload")   # tanpa media_path
        database.insert_tiktok_post(self._post("p3"))
        database.update_tiktok_post("p3", status="done", media_path="/tmp/c.mp4")
        self.assertEqual([p["id"] for p in database.get_tiktok_pending_posts()], ["p1"])

    def test_replay_lookup_requires_message_ids(self):
        database.insert_tiktok_post(self._post("r1", kind="photo", image_count=12))
        self.assertIsNone(database.tiktok_posts_for_replay("r1"))
        database.update_tiktok_post("r1", telegram_message_ids="7,8,9")
        row = database.tiktok_posts_for_replay("r1")
        self.assertIsNotNone(row)
        self.assertEqual(row["telegram_message_ids"], "7,8,9")
        self.assertIsNone(database.tiktok_posts_for_replay("tidak-ada"))

    def test_count_by_account_only_public(self):
        database.insert_tiktok_post(self._post("c1"))
        database.insert_tiktok_post(self._post("c2"))
        database.insert_tiktok_post(self._post("c3", unique_id="jkt48.aralie"))
        database.update_tiktok_post("c2", visible=0)
        counts = database.count_tiktok_posts_by_account()
        self.assertEqual(counts, {"indahjkt48": 1, "jkt48.aralie": 1})


if __name__ == "__main__":
    unittest.main()

