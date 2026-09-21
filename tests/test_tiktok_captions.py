"""
test_tiktok_captions.py - Unit test caption Telegram & judul/deskripsi YouTube
untuk arsip TikTok (video, foto/slide, story).
"""
import unittest

from bot.config import Config
from bot.telegram_sender import build_tiktok_caption, build_tiktok_notification
from bot.youtube_uploader import YouTubeChannelPool

POSTED_AT = "2026-09-16T15:23:00+00:00"


class TestTelegramCaptions(unittest.TestCase):
    def test_video_caption(self):
        caption = build_tiktok_caption(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            created_at=POSTED_AT,
            title="twinnie",
            kind="video",
            part_number=1,
            total_parts=1,
            file_size_bytes=1024 * 1024 * 3,
            source_url="https://www.tiktok.com/@indahjkt48/video/7685965837307596052",
        )
        self.assertIn("TIKTOK VIDEO", caption)
        self.assertIn("Indah JKT48", caption)
        self.assertIn("@.indahjkt48", caption)          # anti-tagging Telegram
        self.assertIn("twinnie", caption)
        self.assertIn("3.0 MB", caption)
        self.assertIn("Lihat di TikTok", caption)
        self.assertNotIn("Part 1/", caption)

    def test_photo_caption_has_part_marker(self):
        caption = build_tiktok_caption(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            created_at=POSTED_AT,
            title="foto hari ini",
            kind="photo",
            part_number=2,
            total_parts=3,
            file_size_bytes=1024 * 1024 * 12,
        )
        self.assertIn("TIKTOK FOTO", caption)
        self.assertIn("[Part 2/3]", caption)
        self.assertIn("Part 2 dari 3", caption)

    def test_story_caption_uses_story_header(self):
        caption = build_tiktok_caption(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            created_at=POSTED_AT,
            kind="video",
            is_story=True,
        )
        self.assertIn("TIKTOK STORY", caption)
        self.assertNotIn("TIKTOK VIDEO", caption)

    def test_photo_story_has_its_own_header(self):
        caption = build_tiktok_caption(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            kind="photo",
            is_story=True,
        )
        self.assertIn("TIKTOK STORY (FOTO)", caption)

    def test_long_description_is_truncated(self):
        caption = build_tiktok_caption(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            title="x" * 2000,
            kind="video",
        )
        self.assertLess(len(caption), 900)

    def test_notification_contains_youtube_link(self):
        text = build_tiktok_notification(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            posted_at=POSTED_AT,
            video_id="abcdefghijk",
            title="twinnie",
            kind="photo",
        )
        self.assertIn("TIKTOK FOTO", text)
        self.assertIn("https://youtu.be/abcdefghijk", text)


class TestYouTubeTikTokTitles(unittest.TestCase):
    def test_video_title_follows_replay_format(self):
        title = YouTubeChannelPool.build_tiktok_title(
            "Indah JKT48", POSTED_AT, kind="video"
        )
        self.assertEqual(
            title,
            f"TIKTOK VIDEO INDAH JKT48 - 16 September 2026 | 22:23 {Config.DISPLAY_TIMEZONE_LABEL}",
        )

    def test_photo_and_story_titles(self):
        photo = YouTubeChannelPool.build_tiktok_title("Indah JKT48", POSTED_AT, kind="photo")
        story = YouTubeChannelPool.build_tiktok_title(
            "Indah JKT48", POSTED_AT, kind="video", is_story=True
        )
        self.assertTrue(photo.startswith("TIKTOK FOTO INDAH JKT48"))
        self.assertTrue(story.startswith("TIKTOK STORY INDAH JKT48"))

    def test_title_without_timestamp_falls_back_to_name_only(self):
        title = YouTubeChannelPool.build_tiktok_title("Indah JKT48", "", kind="video")
        self.assertEqual(title, "TIKTOK VIDEO INDAH JKT48")

    def test_title_is_capped_at_youtube_limit(self):
        long_name = "Nama Member Yang Sangat Panjang Sekali " * 4
        title = YouTubeChannelPool.build_tiktok_title(long_name, POSTED_AT, kind="video")
        self.assertLessEqual(len(title), 100)

    def test_description_mentions_slide_show_for_photos(self):
        description = YouTubeChannelPool.build_tiktok_description(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            posted_at=POSTED_AT,
            kind="photo",
            image_count=12,
            source_url="https://www.tiktok.com/@indahjkt48/video/7679000000000000001",
            caption_text="foto hari ini",
        )
        self.assertIn("JKT48 TikTok Archive", description)
        self.assertIn("12 foto", description)
        self.assertIn("#JKT48 #TikTok", description)
        self.assertIn("foto hari ini", description)

    def test_description_story_uses_story_tag(self):
        description = YouTubeChannelPool.build_tiktok_description(
            member_name="Indah JKT48",
            unique_id="indahjkt48",
            is_story=True,
        )
        self.assertIn("#JKT48 #TikTokStory", description)


if __name__ == "__main__":
    unittest.main()
