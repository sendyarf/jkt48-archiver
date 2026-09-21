"""
test_tiktok_client.py - Unit test normalisasi & penyedia data TikTok.

Tidak ada panggilan jaringan: yang diuji adalah normalisasi item (tikwm vs
yt-dlp), pemilihan penyedia, ekstraksi daftar item dari bentuk respons yang
berbeda-beda, RateLimiter, dan penyedia fixture.
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.tiktok_client import (
    BaseProvider,
    FixtureProvider,
    RateLimiter,
    TikTokItem,
    TikwmProvider,
    YtDlpProvider,
    _extract_items,
    build_providers,
    epoch_to_utc,
    normalize_any,
    normalize_tikwm_item,
    normalize_ytdlp_entry,
    ytdlp_command,
)

FIXTURE_DIR = "tests/fixtures/tiktok"
SEC_UID = "MS4wLjABAAAAS5xamZUblFiVkrQEoRVxM4rtJdriySi52oouUHkMLpvVtL5jnTTJB8gjAeRIKJ1-J"


class TestEpochToUtc(unittest.TestCase):
    def test_epoch_to_iso_utc(self):
        self.assertEqual(epoch_to_utc(0), "")
        self.assertEqual(epoch_to_utc(None), "")
        self.assertEqual(epoch_to_utc(""), "")
        # 1789576980 = 2026-09-16 (data nyata postingan "twinnie").
        self.assertTrue(epoch_to_utc(1789576980).startswith("2026-09-16T"))

    def test_upload_date_fallback(self):
        item = normalize_ytdlp_entry(
            {"id": "123", "title": "uji", "upload_date": "20260916"}, "indahjkt48"
        )
        self.assertEqual(item.created_at, "2026-09-16T00:00:00+00:00")


class TestNormalizeTikwm(unittest.TestCase):
    def test_video_item(self):
        item = normalize_tikwm_item(
            {
                "video_id": "7685965837307596052",
                "title": "twinnie",
                "create_time": 1789576980,
                "duration": 12,
                "hdplay": "https://cdn.invalid/hd.mp4",
                "play": "https://cdn.invalid/sd.mp4",
                "cover": "https://cdn.invalid/cover.jpg",
                "author": {"unique_id": "indahjkt48"},
            },
            "indahjkt48",
        )
        self.assertEqual(item.id, "7685965837307596052")
        self.assertEqual(item.kind, "video")
        self.assertEqual(item.duration_seconds, 12)
        self.assertEqual(item.video_url, "https://cdn.invalid/hd.mp4")
        self.assertEqual(item.image_count, 0)
        self.assertEqual(
            item.page_url, "https://www.tiktok.com/@indahjkt48/video/7685965837307596052"
        )

    def test_photo_item_uses_images_and_content_desc(self):
        item = normalize_tikwm_item(
            {
                "video_id": "7679000000000000001",
                "content_desc": ["foto hari ini", "#jkt48"],
                "create_time": 1789540000,
                "images": ["https://cdn.invalid/1.jpg", "https://cdn.invalid/2.jpg"],
            },
            "indahjkt48",
        )
        self.assertEqual(item.kind, "photo")
        self.assertEqual(item.image_count, 2)
        self.assertEqual(item.title, "foto hari ini #jkt48")

    def test_story_flag_is_preserved(self):
        item = normalize_tikwm_item({"video_id": "9", "play": "x"}, "u", is_story=True)
        self.assertTrue(item.is_story)
        self.assertEqual(item.kind, "video")

    def test_id_fallbacks_and_empty(self):
        self.assertEqual(normalize_tikwm_item({"aweme_id": "abc"}, "u").id, "abc")
        self.assertEqual(normalize_tikwm_item({}, "u").id, "")


class TestNormalizeYtDlp(unittest.TestCase):
    def test_video_entry(self):
        item = normalize_ytdlp_entry(
            {
                "id": "7685965837307596052",
                "title": "twinnie",
                "timestamp": 1789576980,
                "duration": 12,
                "webpage_url": "https://www.tiktok.com/@indahjkt48/video/7685965837307596052",
                "thumbnail": "https://cdn.invalid/t.jpg",
                "channel_id": SEC_UID,
                "uploader_id": "indahjkt48",
            },
            "indahjkt48",
        )
        self.assertEqual(item.kind, "video")
        self.assertEqual(item.cover_url, "https://cdn.invalid/t.jpg")
        self.assertEqual(item.raw.get("channel_id"), SEC_UID)

    def test_images_key_marks_photo(self):
        item = normalize_any(
            {"id": "55", "description": "slide", "images": ["a.jpg", "b.jpg", "c.jpg"]},
            "indahjkt48",
        )
        self.assertEqual(item.kind, "photo")
        self.assertEqual(item.image_count, 3)
        self.assertEqual(item.title, "slide")


class TestExtractItems(unittest.TestCase):
    def test_list_payload(self):
        self.assertEqual(len(_extract_items([{"a": 1}, "x"])), 1)

    def test_wrapped_payloads(self):
        self.assertEqual(len(_extract_items({"videos": [{"a": 1}]})), 1)
        self.assertEqual(len(_extract_items({"items": [{"a": 1}, {"b": 2}]})), 2)
        self.assertEqual(len(_extract_items({"aweme_list": [{"a": 1}]})), 1)
        self.assertEqual(len(_extract_items({"video": {"video_id": "1"}})), 1)

    def test_unknown_shapes_return_empty(self):
        self.assertEqual(_extract_items(None), [])
        self.assertEqual(_extract_items({}), [])
        self.assertEqual(_extract_items("teks"), [])



class TestRateLimiter(unittest.TestCase):
    def test_enforces_min_interval(self):
        limiter = RateLimiter(0.2)

        async def run_two():
            await limiter.wait()
            await limiter.wait()

        started = time.monotonic()
        asyncio.run(run_two())
        self.assertGreaterEqual(time.monotonic() - started, 0.18)

    def test_zero_interval_is_noop(self):
        limiter = RateLimiter(0)

        async def run_two():
            await limiter.wait()
            await limiter.wait()

        started = time.monotonic()
        asyncio.run(run_two())
        self.assertLess(time.monotonic() - started, 0.3)


class TestProviderSelection(unittest.TestCase):
    def test_auto_tries_tikwm_then_ytdlp(self):
        self.assertEqual([p.name for p in build_providers("auto")], ["tikwm", "ytdlp"])

    def test_explicit_modes(self):
        self.assertEqual([p.name for p in build_providers("tikwm")], ["tikwm"])
        self.assertEqual([p.name for p in build_providers("ytdlp")], ["ytdlp"])
        self.assertEqual([p.name for p in build_providers("fixture")], ["fixture"])

    def test_unknown_mode_falls_back_to_auto(self):
        self.assertEqual([p.name for p in build_providers("ngawur")], ["tikwm", "ytdlp"])


class TestYtDlpTarget(unittest.TestCase):
    def test_uses_secuid_when_known(self):
        self.assertEqual(
            YtDlpProvider._target_url({"unique_id": "indahjkt48", "sec_uid": SEC_UID}),
            f"tiktokuser:{SEC_UID}",
        )

    def test_falls_back_to_profile_url(self):
        self.assertEqual(
            YtDlpProvider._target_url({"unique_id": "indahjkt48"}),
            "https://www.tiktok.com/@indahjkt48",
        )

    def test_command_is_runnable(self):
        cmd = ytdlp_command()
        self.assertTrue(cmd)
        self.assertTrue(cmd[0])


class TestHealthFlag(unittest.TestCase):
    def test_unhealthy_until_expires(self):
        provider = BaseProvider(RateLimiter(0))
        self.assertTrue(provider.is_healthy())
        provider.mark_unhealthy("uji")
        self.assertFalse(provider.is_healthy())


class TestFixtureProvider(unittest.TestCase):
    def setUp(self):
        self.provider = FixtureProvider(fixture_dir=FIXTURE_DIR)

    def test_reads_posts_and_marks_photo(self):
        items = asyncio.run(self.provider.fetch_user_posts({"unique_id": "indahjkt48"}, 10))
        self.assertEqual(len(items), 3)
        kinds = {i.id: i.kind for i in items}
        self.assertEqual(kinds["7685965837307596052"], "video")
        self.assertEqual(kinds["7679000000000000001"], "photo")
        photo = next(i for i in items if i.id == "7679000000000000001")
        self.assertEqual(photo.image_count, 12)

    def test_respects_limit(self):
        items = asyncio.run(self.provider.fetch_user_posts({"unique_id": "indahjkt48"}, 1))
        self.assertEqual(len(items), 1)

    def test_reads_stories(self):
        stories = asyncio.run(self.provider.fetch_user_stories({"unique_id": "indahjkt48"}))
        self.assertEqual(len(stories), 1)
        self.assertTrue(stories[0].is_story)

    def test_unknown_account_returns_empty(self):
        items = asyncio.run(self.provider.fetch_user_posts({"unique_id": "tidak-ada"}, 5))
        self.assertEqual(items, [])

    def test_reads_account_info(self):
        info = asyncio.run(self.provider.fetch_user_info("indahjkt48"))
        self.assertEqual(info["sec_uid"], SEC_UID)
        self.assertEqual(info["nickname"], "Indah JKT48")


class TestLocalImagePathsSurvive(unittest.TestCase):
    """Path lokal di fixture harus dibaca apa adanya (dipakai uji media)."""

    def test_local_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            images = []
            for index in range(3):
                path = base / f"{index}.jpg"
                path.write_bytes(b"x")
                images.append(str(path))
            payload = {"id": "uji-1", "kind": "photo", "images": images}
            (base / "akunuji.json").write_text(
                json.dumps({"videos": [payload]}), encoding="utf-8"
            )

            provider = FixtureProvider(fixture_dir=str(base))
            items = asyncio.run(provider.fetch_user_posts({"unique_id": "akunuji"}, 5))
            self.assertEqual(items[0].images, images)


class TestTikTokItemToRow(unittest.TestCase):
    def test_to_db_row_shape(self):
        item = TikTokItem(
            id="1", unique_id="indahjkt48", kind="photo", title="tes",
            created_at="2026-09-16T00:00:00+00:00", images=["a.jpg", "b.jpg"],
        )
        row = item.to_db_row()
        self.assertEqual(row["id"], "1")
        self.assertEqual(row["image_count"], 2)
        self.assertEqual(row["images"], ["a.jpg", "b.jpg"])
        self.assertIn("indahjkt48/video/1", row["source_url"])


class TestProviderContract(unittest.TestCase):
    """
    Kontrak penyedia: kelas turunan WAJIB menimpa metode base.

    Insiden 21 Sep 2026: blok stub `BaseProvider` (fetch_user_posts /
    fetch_user_stories / close) ikut tersisip ke dalam `TikwmProvider`, sehingga
    stub menimpa implementasi asli dan `mark_unhealthy` kehilangan badannya.
    Unit test penyedia fixture tidak menangkapnya karena tidak memakai tikwm,
    jadi kontrak ini diuji langsung.
    """

    def test_providers_override_base_methods(self):
        for cls in (TikwmProvider, YtDlpProvider, FixtureProvider):
            self.assertIsNot(
                cls.fetch_user_posts, BaseProvider.fetch_user_posts,
                f"{cls.name}: fetch_user_posts masih stub base (implementasi ketimpa)",
            )
        for cls in (TikwmProvider, FixtureProvider):
            self.assertIsNot(
                cls.fetch_user_stories, BaseProvider.fetch_user_stories,
                f"{cls.name}: fetch_user_stories masih stub base",
            )
        self.assertIsNot(
            TikwmProvider.fetch_user_info, BaseProvider.fetch_user_info,
            "TikwmProvider harus punya fetch_user_info sendiri",
        )

    def test_mark_unhealthy_logs_reason_and_flags_provider(self):
        provider = BaseProvider(RateLimiter(0))
        with self.assertLogs("bot.tiktok_client", level="WARNING") as captured:
            provider.mark_unhealthy("uji blokir Cloudflare")
        self.assertFalse(provider.is_healthy())
        self.assertTrue(any("uji blokir Cloudflare" in line for line in captured.output))

    def test_tikwm_fetch_user_info_normalizes_payload(self):
        provider = TikwmProvider(RateLimiter(0))

        async def fake_get(path, params):
            return {"user": {"uniqueId": "indahjkt48", "nickname": "Indah", "secUid": SEC_UID}}

        provider._get = fake_get  # type: ignore[assignment]
        info = asyncio.run(provider.fetch_user_info("indahjkt48"))
        self.assertEqual(info["sec_uid"], SEC_UID)
        self.assertEqual(info["nickname"], "Indah")

    def test_ytdlp_fetch_user_info_returns_secuid(self):
        provider = YtDlpProvider(RateLimiter(0))

        async def fake_resolve(account, sample_page_url=""):
            return SEC_UID

        provider.resolve_sec_uid = fake_resolve  # type: ignore[assignment]
        info = asyncio.run(provider.fetch_user_info("indahjkt48"))
        self.assertEqual(info["sec_uid"], SEC_UID)

    def test_ytdlp_fetch_user_info_empty_when_unknown(self):
        provider = YtDlpProvider(RateLimiter(0))

        async def fake_resolve(account, sample_page_url=""):
            return ""

        provider.resolve_sec_uid = fake_resolve  # type: ignore[assignment]
        self.assertEqual(asyncio.run(provider.fetch_user_info("x")), {})


if __name__ == "__main__":
    unittest.main()
