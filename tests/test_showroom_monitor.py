"""
test_showroom_monitor.py - Tes pemantauan status live Showroom.

Fokus: SEMANTIK DEBOUNCE OFFLINE. Aturan yang dijaga:
  * "offline" hanya sah setelah N pembacaan offline BERTURUT-TURUT;
  * sebelum terkonfirmasi hasilnya None ("tidak diketahui"), BUKAN False —
    ini yang mencegah satu hiccup API memecah satu live menjadi beberapa video;
  * kegagalan pembacaan (timeout/HTTP error) tidak pernah dihitung sebagai
    bukti offline;
  * pembacaan live apa pun mereset penghitung.
"""
import asyncio
import unittest

from bot.showroom_monitor import (
    LIVE_ID_PREFIX,
    RoomState,
    ShowroomMonitor,
    build_live_id,
    is_showroom_live_id,
)


class FakeScraper:
    """Scraper palsu: mengembalikan profil dari antrean, tanpa jaringan."""

    def __init__(self, profiles=None, streams=None):
        self.profiles = list(profiles or [])
        self.streams = dict(streams or {})
        self.profile_calls: list[str] = []
        self.stream_calls: list[str] = []
        self.closed = False

    async def get_room_profile(self, room_id):
        self.profile_calls.append(str(room_id))
        if not self.profiles:
            return None
        return self.profiles.pop(0)

    async def get_live_streaming_url(self, room_id):
        self.stream_calls.append(str(room_id))
        return self.streams.get(str(room_id))

    async def close(self):
        self.closed = True


def live_profile(room_id="317738", key="JKT48_Feni", name="Feni"):
    return {
        "room_id": room_id,
        "is_onlive": True,
        "live_id": 555001,
        "room_url_key": key,
        "room_name": name,
        "image": "https://static.showroom-live.com/cover.jpg",
        "current_live_started_at": 1789468597,
    }


def offline_profile(room_id="317738"):
    return {"room_id": room_id, "is_onlive": False, "live_id": 0, "room_url_key": ""}


class TestOfflineDebounce(unittest.TestCase):
    def _monitor(self, profiles, confirmations=3):
        return ShowroomMonitor(
            scraper=FakeScraper(profiles=profiles),
            offline_confirmations=confirmations,
        )

    def test_offline_belum_terkonfirmasi_mengembalikan_none(self):
        """Pembacaan offline 1..N-1 harus None, bukan False."""
        monitor = self._monitor([offline_profile() for _ in range(3)], confirmations=3)
        results = [asyncio.run(monitor.is_room_live("317738")) for _ in range(3)]
        self.assertEqual(results, [None, None, False])

    def test_offline_langsung_terkonfirmasi_bila_ambang_satu(self):
        monitor = self._monitor([offline_profile()], confirmations=1)
        self.assertIs(asyncio.run(monitor.is_room_live("317738")), False)

    def test_pembacaan_live_mereset_penghitung(self):
        """
        Pola offline-offline-LIVE-offline-offline harus berakhir None, bukan
        False: dua pembacaan offline pertama tidak boleh diakumulasi.
        """
        monitor = self._monitor(
            [offline_profile(), offline_profile(), live_profile(),
             offline_profile(), offline_profile()],
            confirmations=3,
        )
        self.assertIsNone(asyncio.run(monitor.is_room_live("317738")))
        self.assertIsNone(asyncio.run(monitor.is_room_live("317738")))
        self.assertIs(asyncio.run(monitor.is_room_live("317738")), True)
        self.assertEqual(monitor.offline_readings("317738"), 0)
        self.assertIsNone(asyncio.run(monitor.is_room_live("317738")))
        self.assertIsNone(asyncio.run(monitor.is_room_live("317738")))

    def test_kegagalan_pembacaan_bukan_bukti_offline(self):
        """
        Scraper selalu gagal (None) → hasil harus None selamanya, tidak pernah
        False, seberapa pun banyak percobaan.
        """
        scraper = FakeScraper(profiles=[])
        monitor = ShowroomMonitor(scraper=scraper, offline_confirmations=2)
        for _ in range(5):
            self.assertIsNone(asyncio.run(monitor.is_room_live("317738")))
        self.assertEqual(monitor.offline_readings("317738"), 0)
        self.assertGreater(len(scraper.profile_calls), 0)

    def test_forget_menghapus_riwayat(self):
        monitor = self._monitor([offline_profile() for _ in range(3)], confirmations=3)
        asyncio.run(monitor.is_room_live("317738"))
        asyncio.run(monitor.is_room_live("317738"))
        self.assertEqual(monitor.offline_readings("317738"), 2)
        monitor.forget("317738")
        self.assertEqual(monitor.offline_readings("317738"), 0)
        # Setelah direset, satu pembacaan offline berikutnya kembali None.
        self.assertIsNone(asyncio.run(monitor.is_room_live("317738")))


class TestRoomStateMapping(unittest.TestCase):
    def test_fetch_state_memetakan_field_api(self):
        monitor = ShowroomMonitor(scraper=FakeScraper(profiles=[live_profile()]))
        state = asyncio.run(monitor.fetch_state("317738"))
        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state.is_onlive)
        self.assertEqual(state.room_url_key, "JKT48_Feni")
        self.assertEqual(state.room_name, "Feni")
        self.assertEqual(state.live_id, 555001)
        self.assertTrue(state.cover_image.endswith("cover.jpg"))
        # Epoch detik dari API harus jadi ISO UTC berpenanda.
        self.assertIsNotNone(state.started_at)
        assert state.started_at is not None
        self.assertIn("+00:00", state.started_at)

    def test_fetch_state_gagal_mengembalikan_none(self):
        monitor = ShowroomMonitor(scraper=FakeScraper(profiles=[]))
        self.assertIsNone(asyncio.run(monitor.fetch_state("317738")))

    def test_started_at_tidak_valid_tidak_menghancurkan_mapping(self):
        profile = live_profile()
        profile["current_live_started_at"] = "bukan-angka"
        monitor = ShowroomMonitor(scraper=FakeScraper(profiles=[profile]))
        state = asyncio.run(monitor.fetch_state("317738"))
        self.assertIsNotNone(state)
        assert state is not None
        self.assertIsNone(state.started_at)

    def test_konfirmasi_offline_minimal_satu(self):
        """Ambang 0/negatif tidak boleh membuat room offline tertahan selamanya."""
        monitor = ShowroomMonitor(scraper=FakeScraper(), offline_confirmations=0)
        self.assertEqual(monitor.offline_confirmations, 1)


class TestLiveIdIdentity(unittest.TestCase):
    def test_live_id_memakai_prefix_dan_room_url_key(self):
        state = RoomState(room_id="317738", is_onlive=True, room_url_key="JKT48_Feni")
        self.assertEqual(build_live_id(state, epoch=1789468597), "sr_JKT48_Feni_1789468597")

    def test_live_id_jatuh_ke_room_id_bila_key_kosong(self):
        state = RoomState(room_id="317738", is_onlive=True, room_url_key="")
        self.assertEqual(build_live_id(state, epoch=5), "sr_317738_5")

    def test_prefix_mencegah_tabrakan_dengan_idn(self):
        """live_id IDN '<username>_<epoch>' tidak boleh terdeteksi sebagai Showroom."""
        self.assertTrue(is_showroom_live_id("sr_JKT48_Feni_1789468597"))
        self.assertFalse(is_showroom_live_id("jkt48_feni_1789468597"))
        self.assertFalse(is_showroom_live_id(""))
        self.assertEqual(LIVE_ID_PREFIX, "sr_")


class TestFindLiveRooms(unittest.TestCase):
    def test_hanya_room_live_yang_dikembalikan(self):
        scraper = FakeScraper(
            profiles=[live_profile("1", "K1", "Satu"), offline_profile("2")],
            streams={"1": "https://cdn.example/1.m3u8"},
        )
        monitor = ShowroomMonitor(scraper=scraper)
        live = asyncio.run(monitor.find_live_rooms([
            {"username": "u1", "room_id": "1"},
            {"username": "u2", "room_id": "2"},
        ]))
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["username"], "u1")
        self.assertEqual(live[0]["hls_url"], "https://cdn.example/1.m3u8")
        self.assertEqual(live[0]["room_url_key"], "K1")
        self.assertEqual(live[0]["showroom_live_id"], 555001)

    def test_room_live_tanpa_url_hls_dilewati(self):
        """Status live tetapi HLS tidak tersedia → jangan rekam (tidak ada sumber)."""
        scraper = FakeScraper(profiles=[live_profile("1")], streams={})
        monitor = ShowroomMonitor(scraper=scraper)
        live = asyncio.run(monitor.find_live_rooms([{"username": "u1", "room_id": "1"}]))
        self.assertEqual(live, [])

    def test_url_hls_tidak_diminta_untuk_room_offline(self):
        """Hemat request: HLS hanya diambil untuk room yang benar-benar live."""
        scraper = FakeScraper(profiles=[offline_profile("9")], streams={"9": "x"})
        monitor = ShowroomMonitor(scraper=scraper)
        asyncio.run(monitor.find_live_rooms([{"username": "u9", "room_id": "9"}]))
        self.assertEqual(scraper.stream_calls, [])

    def test_room_tanpa_room_id_dilewati_tanpa_request(self):
        scraper = FakeScraper(profiles=[live_profile()])
        monitor = ShowroomMonitor(scraper=scraper)
        self.assertEqual(asyncio.run(monitor.find_live_rooms([{"username": "u"}])), [])
        self.assertEqual(scraper.profile_calls, [])

    def test_daftar_kosong_tidak_menyentuh_api(self):
        scraper = FakeScraper(profiles=[live_profile()])
        monitor = ShowroomMonitor(scraper=scraper)
        self.assertEqual(asyncio.run(monitor.find_live_rooms([])), [])
        self.assertEqual(scraper.profile_calls, [])

    def test_exception_satu_room_tidak_menggagalkan_lainnya(self):
        """Kegagalan satu probe tidak boleh membatalkan seluruh siklus."""

        class ExplodingScraper(FakeScraper):
            async def get_room_profile(self, room_id):
                self.profile_calls.append(str(room_id))
                if str(room_id) == "boom":
                    raise RuntimeError("API meledak")
                return live_profile(str(room_id), "K", "N")

            async def get_live_streaming_url(self, room_id):
                return "https://cdn.example/ok.m3u8"

        monitor = ShowroomMonitor(scraper=ExplodingScraper())
        live = asyncio.run(monitor.find_live_rooms([
            {"username": "bad", "room_id": "boom"},
            {"username": "good", "room_id": "1"},
        ]))
        self.assertEqual([r["username"] for r in live], ["good"])

    def test_close_menutup_scraper_dan_idempoten(self):
        scraper = FakeScraper()
        monitor = ShowroomMonitor(scraper=scraper)
        asyncio.run(monitor.close())
        asyncio.run(monitor.close())
        self.assertTrue(scraper.closed)


if __name__ == "__main__":
    unittest.main()
