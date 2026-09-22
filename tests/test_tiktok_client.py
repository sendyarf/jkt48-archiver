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

from bot import tiktok_client
from bot.tiktok_client import (
    BaseProvider,
    EmbedProvider,
    FixtureProvider,
    ProviderError,
    RateLimiter,
    TikTokItem,
    TikwmProvider,
    YtDlpProvider,
    _extract_items,
    _next_cursor,
    build_providers,
    epoch_to_utc,
    extract_post_id,
    looks_like_photo,
    normalize_any,
    normalize_tikwm_item,
    normalize_ytdlp_entry,
    parse_embed_post,
    parse_embed_profile,
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

    def test_wmplay_is_never_used_as_video_url(self):
        """`wmplay` = varian berwatermark; tidak boleh jadi sumber unduhan."""
        item = normalize_tikwm_item(
            {"video_id": "1", "duration": 5, "wmplay": "https://cdn.invalid/wm.mp4"},
            "u",
        )
        self.assertEqual(item.video_url, "")

    def test_wmplay_ignored_when_other_keys_present(self):
        item = normalize_tikwm_item(
            {
                "video_id": "1",
                "duration": 5,
                "play": "https://cdn.invalid/clean.mp4",
                "wmplay": "https://cdn.invalid/wm.mp4",
            },
            "u",
        )
        self.assertEqual(item.video_url, "https://cdn.invalid/clean.mp4")


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
    def test_auto_tries_tikwm_then_embed_then_ytdlp(self):
        """Urutan auto: tikwm (lengkap+story) → embed (andal) → ytdlp (cadangan)."""
        self.assertEqual(
            [p.name for p in build_providers("auto")], ["tikwm", "embed", "ytdlp"]
        )

    def test_explicit_modes(self):
        self.assertEqual([p.name for p in build_providers("tikwm")], ["tikwm"])
        self.assertEqual([p.name for p in build_providers("embed")], ["embed"])
        self.assertEqual([p.name for p in build_providers("ytdlp")], ["ytdlp"])
        self.assertEqual([p.name for p in build_providers("fixture")], ["fixture"])

    def test_unknown_mode_falls_back_to_auto(self):
        self.assertEqual(
            [p.name for p in build_providers("ngawur")], ["tikwm", "embed", "ytdlp"]
        )


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

    def test_block_is_per_capability(self):
        """
        Cloudflare memblokir per-path: `/user/posts` 403 bisa terjadi sementara
        `/user/story` tetap 200 (bukti 21 Sep 2026), jadi health dipisah.
        Provider yang gagal mengambil posts harus TETAP dicoba untuk stories.
        """
        provider = BaseProvider(RateLimiter(0))
        provider.mark_unhealthy("posts 403", capability="posts")
        self.assertFalse(provider.is_healthy("posts"))
        self.assertTrue(provider.is_healthy("stories"), "story harus tetap dicoba")

    def test_global_block_blocks_every_capability(self):
        provider = BaseProvider(RateLimiter(0))
        provider.mark_unhealthy("penyedia mati")
        self.assertFalse(provider.is_healthy("posts"))
        self.assertFalse(provider.is_healthy("stories"))

    def test_missing_capability_is_permanent(self):
        provider = BaseProvider(RateLimiter(0))
        provider.mark_capability_missing("stories")
        self.assertFalse(provider.is_healthy("stories"))
        self.assertTrue(provider.is_healthy("posts"))

    def test_mark_unhealthy_logs_reason_and_capability(self):
        provider = BaseProvider(RateLimiter(0))
        with self.assertLogs("bot.tiktok_client", level="WARNING") as captured:
            provider.mark_unhealthy("uji blokir Cloudflare", capability="posts")
        self.assertTrue(any("uji blokir Cloudflare" in line for line in captured.output))
        self.assertTrue(any("posts" in line for line in captured.output))


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

        async def fake_call(path, params, *, method="GET"):
            return {"user": {"uniqueId": "indahjkt48", "nickname": "Indah", "secUid": SEC_UID}}

        provider._call = fake_call  # type: ignore[assignment]
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


EMBED_PROFILE_HTML = """
<!DOCTYPE html><html><body>
<script id="__FRONTITY_CONNECT_STATE__" type="application/json">
{
  "source": {
    "data": {
      "/embed/@indahjkt48": {
        "videoList": [
          {"id": "7685965837307596052", "desc": "twinnie",
           "playAddr": "https://v16.invalid/a.mp4",
           "coverUrl": "https://p16.invalid/a.jpg", "authorUniqueId": "indahjkt48"},
          {"id": "7679000000000000001", "desc": "foto hari ini",
           "coverUrl": "https://p16.invalid/b.jpg", "authorUniqueId": "indahjkt48"},
          {"id": "", "desc": "tanpa id", "playAddr": "x"}
        ]
      }
    }
  }
}
</script>
</body></html>
"""

EMBED_POST_PHOTO_HTML = """
<!DOCTYPE html><html><body>
<script id="__FRONTITY_CONNECT_STATE__" type="application/json">
{
  "source": {
    "data": {
      "/embed/v2/7679000000000000001": {
        "videoData": {
          "authorInfos": {"uniqueId": "indahjkt48"},
          "itemInfos": {
            "id": "7679000000000000001",
            "createTime": 1789540000,
            "covers": ["https://p16.invalid/cover.jpg"],
            "commentCount": 3,
            "video": {}
          },
          "imagePostInfo": {
            "displayImages": [
              {"urlList": ["https://p16.invalid/p1.jpg"]},
              {"urlList": ["https://p16.invalid/p2.jpg"]}
            ]
          }
        }
      }
    }
  }
}
</script>
</body></html>
"""

EMBED_POST_VIDEO_HTML = """
<!DOCTYPE html><html><body>
<script id="__FRONTITY_CONNECT_STATE__" type="application/json">
{
  "source": {
    "data": {
      "/embed/v2/7685965837307596052": {
        "videoData": {
          "authorInfos": {"uniqueId": "indahjkt48"},
          "itemInfos": {
            "id": "7685965837307596052",
            "createTime": 1789576980,
            "covers": ["https://p16.invalid/c.jpg"],
            "video": {
              "urls": ["https://v16.invalid/v.mp4"],
              "videoMeta": {"duration": 12500}
            }
          }
        }
      }
    }
  }
}
</script>
</body></html>
"""


class TestEmbedParsers(unittest.TestCase):
    """
    Parser halaman embed TikTok (metode dari proyek JKT48_TIKTOK).

    Listing profil embed = jalur paling andal saat Cloudflare memblokir tikwm dan
    halaman profil biasa hanya menjawab stub (sehingga yt-dlp gagal).
    """

    def test_profile_listing_marks_photo_without_playaddr(self):
        items = parse_embed_profile(EMBED_PROFILE_HTML, "indahjkt48")
        self.assertEqual(len(items), 2)   # entri tanpa id dilewati
        video, photo = items
        self.assertEqual(video.id, "7685965837307596052")
        self.assertEqual(video.kind, "video")
        self.assertEqual(video.title, "twinnie")
        self.assertEqual(video.video_url, "https://v16.invalid/a.mp4")
        self.assertEqual(photo.id, "7679000000000000001")
        self.assertEqual(photo.kind, "photo", "postingan tanpa playAddr = foto")
        self.assertEqual(photo.cover_url, "https://p16.invalid/b.jpg")
        self.assertEqual(
            photo.page_url, "https://www.tiktok.com/@indahjkt48/video/7679000000000000001"
        )

    def test_profile_without_state_returns_empty(self):
        self.assertEqual(parse_embed_profile("<html></html>", "indahjkt48"), [])
        self.assertEqual(parse_embed_profile("", "indahjkt48"), [])

    def test_post_detail_photo_carousel(self):
        item = parse_embed_post(EMBED_POST_PHOTO_HTML, "7679000000000000001", "indahjkt48")
        self.assertIsNotNone(item)
        self.assertEqual(item.kind, "photo")
        self.assertEqual(
            item.images, ["https://p16.invalid/p1.jpg", "https://p16.invalid/p2.jpg"]
        )
        self.assertEqual(item.image_count, 2)
        self.assertTrue(item.created_at.startswith("2026-09-1"))
        self.assertEqual(item.cover_url, "https://p16.invalid/cover.jpg")

    def test_post_detail_video_url_and_duration(self):
        item = parse_embed_post(EMBED_POST_VIDEO_HTML, "7685965837307596052", "indahjkt48")
        self.assertIsNotNone(item)
        self.assertEqual(item.kind, "video")
        self.assertEqual(item.video_url, "https://v16.invalid/v.mp4")
        self.assertEqual(item.duration_seconds, 12)   # 12500 ms → 12 detik
        self.assertEqual(item.images, [])

    def test_post_detail_falls_back_to_first_video_node(self):
        """
        Kunci node embed bisa berbeda dari `/embed/v2/<id>` (mis. saat TikTok
        mengganti bentuk respons), jadi node pertama yang punya `videoData`
        dipakai — perilaku ini SENGAJA agar parsing tidak rapuh.
        """
        other_key = EMBED_POST_VIDEO_HTML.replace(
            '"/embed/v2/7685965837307596052"', '"/embed/v2/lain"'
        )
        item = parse_embed_post(other_key, "7685965837307596052", "indahjkt48")
        self.assertIsNotNone(item)
        self.assertEqual(item.video_url, "https://v16.invalid/v.mp4")
        self.assertEqual(item.id, "7685965837307596052")

    def test_post_detail_without_state_returns_none(self):
        self.assertIsNone(parse_embed_post("<html></html>", "999", "indahjkt48"))
        self.assertIsNone(parse_embed_post("", "999", "indahjkt48"))

    def test_extract_post_id(self):
        self.assertEqual(
            extract_post_id("https://www.tiktok.com/@u/video/7685965837307596052"),
            "7685965837307596052",
        )
        self.assertEqual(
            extract_post_id("https://www.tiktok.com/@u/photo/7679000000000000001"),
            "7679000000000000001",
        )
        self.assertEqual(extract_post_id("7685965837307596052"), "7685965837307596052")
        self.assertEqual(extract_post_id("tidak-ada-id"), "")
        self.assertEqual(extract_post_id(""), "")


class TestStoryPagination(unittest.TestCase):
    """Story memakai path TUNGGAL `/user/story` + paginasi cursor tikwm."""

    def _provider(self):
        return TikwmProvider(RateLimiter(0))

    def test_next_cursor_handles_both_naming_styles(self):
        self.assertEqual(_next_cursor({"hasMore": True, "cursor": 123}), "123")
        self.assertEqual(_next_cursor({"has_more": True, "cursor": "456"}), "456")
        self.assertEqual(_next_cursor({"hasMore": False, "cursor": 123}), "")
        self.assertEqual(_next_cursor({"has_more": False, "cursor": "9"}), "")
        self.assertEqual(_next_cursor({"cursor": "0"}), "")
        self.assertEqual(_next_cursor({"cursor": 0}), "")
        self.assertEqual(_next_cursor({}), "")
        self.assertEqual(_next_cursor(None), "")

    def test_stories_use_singular_endpoint_and_paginate(self):
        provider = self._provider()
        calls: list[tuple] = []
        pages = [
            {"videos": [{"video_id": "s1", "create_time": 1789576980, "duration": 9,
                         "play": "https://cdn.invalid/1.mp4"}],
             "cursor": "30", "hasMore": True},
            {"videos": [{"video_id": "s2", "create_time": 1789576900, "play": "x"}],
             "cursor": "0", "hasMore": False},
        ]

        async def fake_call(path, params, *, method="GET"):
            calls.append((path, params, method))
            return pages[len(calls) - 1]

        provider._call = fake_call  # type: ignore[assignment]
        stories = asyncio.run(provider.fetch_user_stories({"unique_id": "indahjkt48"}))

        self.assertEqual([s.id for s in stories], ["s1", "s2"])
        self.assertTrue(all(s.is_story for s in stories))
        self.assertEqual([c[0] for c in calls], ["/user/story", "/user/story"])
        self.assertEqual(calls[0][1]["cursor"], "")
        self.assertEqual(calls[1][1]["cursor"], "30")
        self.assertTrue(all(c[2] == "GET" for c in calls))

    def test_stories_fall_back_to_post_when_get_fails(self):
        provider = self._provider()
        attempts: list[str] = []

        async def fake_call(path, params, *, method="GET"):
            attempts.append(method)
            if method == "GET":
                raise ProviderError("tikwm /user/story: HTTP 500")
            return {"videos": [{"video_id": "s9", "play": "y"}], "cursor": "0",
                    "hasMore": False}

        provider._call = fake_call  # type: ignore[assignment]
        stories = asyncio.run(provider.fetch_user_stories({"unique_id": "u"}))
        self.assertEqual([s.id for s in stories], ["s9"])
        self.assertEqual(attempts, ["GET", "POST"])

    def test_daily_quota_sets_long_cooldown(self):
        """Kuota harian → provider dijeda panjang (bukan dicoba-coba cepat)."""
        provider = self._provider()
        with self.assertLogs("bot.tiktok_client", level="WARNING") as captured:
            provider._note_limit("You have reached the rate limit 10000 request/ 1 day")
        self.assertTrue(provider.is_blocked())
        self.assertTrue(any("kuota harian" in line for line in captured.output))

    def test_stories_empty_when_no_items(self):
        provider = self._provider()

        async def fake_call(path, params, *, method="GET"):
            return {"videos": [], "cursor": "0", "hasMore": False}

        provider._call = fake_call  # type: ignore[assignment]
        self.assertEqual(asyncio.run(provider.fetch_user_stories({"unique_id": "u"})), [])


class TestPhotoDetection(unittest.TestCase):
    def test_images_list_wins(self):
        self.assertTrue(looks_like_photo({"images": ["a.jpg"]}))

    def test_explicit_flag(self):
        self.assertTrue(looks_like_photo({"is_photo": True}))

    def test_zero_duration_and_size_is_photo(self):
        self.assertTrue(looks_like_photo({"duration": 0, "size": 0}))

    def test_normal_video_is_not_photo(self):
        self.assertFalse(looks_like_photo({"duration": 12, "size": 1048576}))
        self.assertFalse(looks_like_photo({}))

    def test_normalize_marks_zero_duration_item_as_photo(self):
        item = normalize_tikwm_item({"video_id": "1", "duration": 0, "size": 0}, "u")
        self.assertEqual(item.kind, "photo")

    def test_normalize_accepts_alternative_video_keys(self):
        item = normalize_tikwm_item(
            {"video_id": "2", "duration": 5, "video_url": "https://cdn.invalid/v.mp4"}, "u"
        )
        self.assertEqual(item.video_url, "https://cdn.invalid/v.mp4")
        self.assertEqual(item.kind, "video")


class TestHttpRetry(unittest.TestCase):
    """Retry untuk status transient (embed TikTok sering 503 saat overload)."""

    def test_retries_transient_then_succeeds(self):
        calls: list[int] = []

        async def fake_get(url, *, headers=None, timeout=30.0):
            calls.append(1)
            return (503, "Service Unavailable") if len(calls) < 3 else (200, "<html>ok</html>")

        original = tiktok_client.http_get_text
        tiktok_client.http_get_text = fake_get  # type: ignore[assignment]
        try:
            status, body = asyncio.run(tiktok_client.http_get_text_retry("https://x"))
        finally:
            tiktok_client.http_get_text = original  # type: ignore[assignment]
        self.assertEqual(status, 200)
        self.assertEqual(body, "<html>ok</html>")
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_max_attempts(self):
        calls: list[int] = []

        async def fake_get(url, *, headers=None, timeout=30.0):
            calls.append(1)
            return 503, ""

        original = tiktok_client.http_get_text
        tiktok_client.http_get_text = fake_get  # type: ignore[assignment]
        try:
            status, _ = asyncio.run(
                tiktok_client.http_get_text_retry("https://x", attempts=2)
            )
        finally:
            tiktok_client.http_get_text = original  # type: ignore[assignment]
        self.assertEqual(status, 503)
        self.assertEqual(len(calls), 2)

    def test_does_not_retry_permanent_status(self):
        calls: list[int] = []

        async def fake_get(url, *, headers=None, timeout=30.0):
            calls.append(1)
            return 403, "forbidden"

        original = tiktok_client.http_get_text
        tiktok_client.http_get_text = fake_get  # type: ignore[assignment]
        try:
            status, _ = asyncio.run(tiktok_client.http_get_text_retry("https://x"))
        finally:
            tiktok_client.http_get_text = original  # type: ignore[assignment]
        self.assertEqual(status, 403)
        self.assertEqual(len(calls), 1, "403 (blokir) tidak perlu di-retry")


if __name__ == "__main__":
    unittest.main()
