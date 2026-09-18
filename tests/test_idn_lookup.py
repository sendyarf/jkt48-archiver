"""
test_idn_lookup.py - Tests for the OPTIONAL IDN lookup (graceful degradation).

Tujuan utama: memastikan bot tetap aman ketika server IDN bermasalah — semua
kegagalan harus menghasilkan `None` (unknown) dan TIDAK PERNAH melempar exception.
"""
import asyncio
import unittest
from unittest.mock import patch

from bot.config import Config
from bot.idn_lookup import IDNLookup, live_key_from

LIVE_PAYLOAD = {
    "slug": "haii-260915223455",
    "title": "haii",
    "status": "live",
    "live_at": "2026-09-15T22:34:55Z",
    "playback_url": "https://example.invalid/channel.X.m3u8",
    "creator": {"username": "jkt48_lulu", "name": "Lulu JKT48"},
}

PROFILE_PAYLOAD = {"getPublicProfileByUsername": {"uuid": "uuid-1", "name": "Lulu JKT48"}}


class TestIDNLookup(unittest.TestCase):
    def setUp(self):
        self._orig_enabled = Config.IDN_LOOKUP_ENABLED
        self._orig_ttl = Config.IDN_LOOKUP_CACHE_SECONDS
        Config.IDN_LOOKUP_ENABLED = True
        Config.IDN_LOOKUP_CACHE_SECONDS = 30
        self.lookup = IDNLookup()

    def tearDown(self):
        Config.IDN_LOOKUP_ENABLED = self._orig_enabled
        Config.IDN_LOOKUP_CACHE_SECONDS = self._orig_ttl
        asyncio.run(self.lookup.close())

    def _patch_post(self, fake):
        """Patch _post di level INSTANCE (fake dipanggil tanpa self)."""
        return patch.object(self.lookup, "_post", side_effect=fake)

    def test_disabled_returns_none_without_request(self):
        Config.IDN_LOOKUP_ENABLED = False
        calls = []

        async def fake(query, variables, operation):
            calls.append(operation)
            return {}

        with self._patch_post(fake):
            result = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertIsNone(result)
        self.assertEqual(calls, [], "IDN dimatikan → tidak boleh ada request")

    def test_profile_lookup_failure_returns_none(self):
        async def fake(query, variables, operation):
            return None

        with self._patch_post(fake):
            result = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertIsNone(result, "IDN tidak menjawab → tidak diketahui (None)")

    def test_member_not_live_returns_empty_slug(self):
        async def fake(query, variables, operation):
            if operation == "Profile":
                return PROFILE_PAYLOAD
            return {"getLivestreams": []}

        with self._patch_post(fake):
            info = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertIsNotNone(info)
        self.assertEqual(info["slug"], "", "IDN menjawab jelas: member tidak live")
        self.assertEqual(info["uuid"], "uuid-1")

    def test_member_live_returns_slug_and_title(self):
        async def fake(query, variables, operation):
            if operation == "Profile":
                return PROFILE_PAYLOAD
            return {"getLivestreams": [LIVE_PAYLOAD]}

        with self._patch_post(fake):
            info = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertEqual(info["slug"], "haii-260915223455")
        self.assertEqual(info["title"], "haii")
        self.assertEqual(info["uuid"], "uuid-1")

    def test_livestream_of_other_member_is_ignored(self):
        other = dict(LIVE_PAYLOAD, creator={"username": "orang_lain", "name": "Lain"})

        async def fake(query, variables, operation):
            if operation == "Profile":
                return PROFILE_PAYLOAD
            return {"getLivestreams": [other]}

        with self._patch_post(fake):
            info = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertEqual(info["slug"], "", "live milik orang lain harus diabaikan")

    def test_result_is_cached(self):
        calls = []

        async def fake(query, variables, operation):
            calls.append(operation)
            if operation == "Profile":
                return PROFILE_PAYLOAD
            return {"getLivestreams": [LIVE_PAYLOAD]}

        with self._patch_post(fake):
            first = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
            second = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertEqual(first, second)
        self.assertEqual(calls.count("LiveByStreamer"), 1, "panggilan kedua harus dari cache")

    def test_network_exception_is_swallowed(self):
        async def fake(query, variables, operation):
            raise RuntimeError("IDN sedang down")

        with self._patch_post(fake):
            result = asyncio.run(self.lookup.fetch_member_live("jkt48_lulu"))
        self.assertIsNone(result, "exception tidak boleh bocor ke pemanggil")


class TestLiveKeyDerivation(unittest.TestCase):
    """
    Identitas live = JUDUL, bukan slug.

    Kasus nyata jkt48_daisy (13 Sep 2026): reconnect membuat slug BARU dengan judul
    SAMA → ketiganya harus menghasilkan live_key yang sama.
    """

    def test_same_title_different_slugs_share_key(self):
        keys = {
            live_key_from("haii-260913192155"),
            live_key_from("haii-260913201248"),
            live_key_from("haii-260913201503"),
        }
        self.assertEqual(keys, {"haii"})

    def test_title_field_wins_over_slug(self):
        self.assertEqual(live_key_from("haii-260913201248", "Haii"), "haii")

    def test_different_titles_give_different_keys(self):
        self.assertNotEqual(live_key_from("haii-260913192155"), live_key_from("makan-260916010000"))

    def test_slug_without_timestamp_is_kept(self):
        self.assertEqual(live_key_from("live-bareng"), "live-bareng")

    def test_empty_inputs(self):
        self.assertEqual(live_key_from("", ""), "")
        self.assertEqual(live_key_from("", "Haii"), "haii")

    def test_lookup_result_contains_live_key(self):
        async def fake_post(query, variables, operation):
            if operation == "Profile":
                return {"getPublicProfileByUsername": {"uuid": "uuid-1"}}
            return {"getLivestreams": [LIVE_PAYLOAD]}

        lookup = IDNLookup()
        try:
            with patch.object(lookup, "_post", side_effect=fake_post):
                info = asyncio.run(lookup.fetch_member_live("jkt48_lulu"))
        finally:
            asyncio.run(lookup.close())

        self.assertEqual(info["live_key"], "haii")
        self.assertEqual(info["slug"], "haii-260915223455")


if __name__ == "__main__":
    unittest.main()