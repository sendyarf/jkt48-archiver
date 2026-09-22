"""
test_showroom_integration.py - Tes integrasi jalur Showroom di main.py.

Yang dijaga:
  * Probe per platform: IDN memakai HLS, Showroom memakai API room (dan
    sebaliknya TIDAK saling tertukar).
  * Sesi Showroom tidak memanggil IDN lookup sama sekali.
  * Siklus `_check_showroom` memulai rekaman untuk room live, melewati room
    yang sudah direkam, dan tidak menyentuh jalur IDN.
  * `_record_showroom_task` menyimpan platform='showroom' + live_id berprefix
    'sr_' dan menyerahkan segmen ke merge manager dengan platform Showroom.
  * Merge group IDN dan Showroom untuk member yang sama TIDAK saling bercampur.
"""
import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import database, main as bot_main, merger
from bot.config import Config
from bot.showroom_monitor import RoomState, build_live_id

HLS_IDN = (
    "https://4b964ca68cf1.us-east-1.playback.live-video.net/api/video/v1/"
    "us-east-1.050891932989.channel.LuflGrysVdSz.m3u8"
)
COVER = "https://static.showroom-live.com/image/room/cover/x_s.jpeg"


class ShowroomIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._old_db = Config.DB_PATH
        self._old_enabled = Config.SHOWROOM_ENABLED
        self._old_resume_delay = Config.SHOWROOM_RESUME_DELAY_SECONDS
        # Resume rekaman tidak boleh membuat tes lambat (jeda nyata di-nol-kan).
        Config.SHOWROOM_RESUME_DELAY_SECONDS = 0
        Config.DB_PATH = str(base / "test.db")
        database.init_db()
        database.register_members_if_not_exists(["jkt48_lulu", "jkt48_feri"])
        database.update_member_hls_url("jkt48_lulu", HLS_IDN, "Lulu")
        # Feri hanya ada di Showroom (tanpa hls_url).
        database.set_member_showroom("jkt48_feri", "318222", "Olla", "", showroom_only=True)
        database.set_member_showroom("jkt48_lulu", "317738", "Feni", "", showroom_only=False)

    def tearDown(self):
        Config.DB_PATH = self._old_db
        Config.SHOWROOM_ENABLED = self._old_enabled
        Config.SHOWROOM_RESUME_DELAY_SECONDS = self._old_resume_delay
        self._tmp.cleanup()

    def _make_bot(self):
        with patch("bot.main.TelegramSender"), patch("bot.main.YouTubeChannelPool"):
            return bot_main.JKT48LiveBot()


class TestPlatformProbe(ShowroomIntegrationTestCase):
    def test_probe_showroom_memakai_api_room_bukan_hls(self):
        bot = self._make_bot()
        calls = {"room": [], "hls": []}

        async def fake_is_room_live(room_id):
            calls["room"].append(room_id)
            return True

        async def fake_is_stream_active(url):
            calls["hls"].append(url)
            return False

        bot.showroom.is_room_live = fake_is_room_live
        bot.hls_monitor.is_stream_active = fake_is_stream_active

        result = asyncio.run(bot._probe_member_active("jkt48_lulu", "showroom"))
        self.assertIs(result, True)
        self.assertEqual(calls["room"], ["317738"])
        self.assertEqual(calls["hls"], [])

    def test_probe_idn_memakai_hls_bukan_showroom(self):
        bot = self._make_bot()
        calls = {"room": [], "hls": []}

        async def fake_is_room_live(room_id):
            calls["room"].append(room_id)
            return True

        async def fake_is_stream_active(url):
            calls["hls"].append(url)
            return True

        bot.showroom.is_room_live = fake_is_room_live
        bot.hls_monitor.is_stream_active = fake_is_stream_active

        result = asyncio.run(bot._probe_member_active("jkt48_lulu", "idn"))
        self.assertIs(result, True)
        self.assertEqual(calls["hls"], [HLS_IDN])
        self.assertEqual(calls["room"], [])

    def test_probe_showroom_tanpa_room_id_mengembalikan_none(self):
        bot = self._make_bot()
        self.assertIsNone(asyncio.run(bot._probe_member_active("jkt48_lulu_x", "showroom")))

    def test_fetch_live_showroom_tidak_memanggil_idn(self):
        bot = self._make_bot()
        called = {"n": 0}

        async def fake_idn(username):
            called["n"] += 1
            return {"slug": "haii-1", "live_key": "judul"}

        bot.idn_lookup.fetch_member_live = fake_idn
        self.assertIsNone(asyncio.run(bot._fetch_member_live("jkt48_lulu", "showroom")))
        self.assertEqual(called["n"], 0)

    def test_fetch_live_idn_tetap_memanggil_idn(self):
        bot = self._make_bot()
        called = {"n": 0}

        async def fake_idn(username):
            called["n"] += 1
            return {"slug": "haii-1", "live_key": "judul"}

        bot.idn_lookup.fetch_member_live = fake_idn
        result = asyncio.run(bot._fetch_member_live("jkt48_lulu", "idn"))
        self.assertEqual(called["n"], 1)
        self.assertEqual(result, {"slug": "haii-1", "live_key": "judul"})
class TestCheckShowroomLoop(ShowroomIntegrationTestCase):
    def _live_room(self, username="jkt48_feri", room_id="318222"):
        return {
            "username": username,
            "display_name": username,
            "room_id": room_id,
            "hls_url": "https://cdn.showroom.example/room.m3u8",
            "room_url_key": "JKT48_Olla",
            "room_name": "Olla",
            "cover_image": COVER,
            "started_at": "2026-09-18T03:00:00+00:00",
            "showroom_live_id": 99,
        }

    def test_memulai_rekaman_untuk_room_live(self):
        bot = self._make_bot()
        started = []

        async def fake_find(rooms):
            return [self._live_room()]

        async def fake_record(room, live_id):
            started.append((room["username"], live_id))

        bot.showroom.find_live_rooms = fake_find
        bot._record_showroom_task = fake_record

        async def scenario():
            await bot._check_showroom()
            await asyncio.sleep(0)
        asyncio.run(scenario())

        self.assertEqual(len(started), 1)
        self.assertEqual(started[0][0], "jkt48_feri")
        self.assertTrue(started[0][1].startswith("sr_JKT48_Olla_"))
        self.assertIn("jkt48_feri", bot.active_showroom)

    def test_room_yang_sudah_direkam_dilewati(self):
        bot = self._make_bot()
        bot.active_showroom.add("jkt48_feri")
        seen = {}

        async def fake_find(rooms):
            seen["rooms"] = rooms
            return []

        async def fake_record(room, live_id):  # pragma: no cover
            raise AssertionError("tidak boleh merekam ulang")

        bot.showroom.find_live_rooms = fake_find
        bot._record_showroom_task = fake_record

        asyncio.run(bot._check_showroom())
        names = [r["username"] for r in seen["rooms"]]
        self.assertNotIn("jkt48_feri", names, "room yang sedang direkam tidak boleh dipindai")
        self.assertIn("jkt48_lulu", names)

    def test_tidak_ada_room_live_tidak_memulai_apa_pun(self):
        bot = self._make_bot()

class TestRecordShowroomTask(ShowroomIntegrationTestCase):
    def test_menyimpan_platform_showroom_dan_live_id_berprefix(self):
        bot = self._make_bot()
        video = Path(self._tmp.name) / "seg.mp4"
        video.write_bytes(b"\x00" * 1024)
        segmented = {}

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            return video

        async def fake_add_segment(**kwargs):
            segmented.update(kwargs)

        bot.merge_mgr.add_segment = fake_add_segment

        # Task kini probe liveness room setelah yt-dlp selesai — stub agar tes
        # tidak memanggil API Showroom sungguhan (room dianggap sudah berakhir).
        async def fake_room_offline(room_id):
            return False

        # Deteksi ganti sesi broadcast memanggil fetch_state — stub dengan
        # live_id yang SAMA (99) agar tidak dianggap sesi baru.
        async def fake_same_broadcast(room_id):
            return RoomState(
                room_id="318222", is_onlive=True, live_id=99,
                room_url_key="JKT48_Olla",
            )

        bot.showroom.is_room_live = fake_room_offline
        bot.showroom.fetch_state = fake_same_broadcast

        room = {
            "username": "jkt48_feri",
            "display_name": "Feri",
            "room_id": "318222",
            "hls_url": "https://cdn.showroom.example/room.m3u8",
            "room_url_key": "JKT48_Olla",
            "room_name": "Olla Live",
            "cover_image": COVER,
            "started_at": "2026-09-18T03:00:00+00:00",
            "showroom_live_id": 99,
        }
        state = RoomState(room_id="318222", is_onlive=True, room_url_key="JKT48_Olla")
        live_id = build_live_id(state, epoch=1789468597)

        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_showroom_task(room, live_id))

        row = database.get_session(live_id)
        self.assertIsNotNone(row, "sesi Showroom harus tercatat di database")
        assert row is not None
        self.assertEqual(row["platform"], "showroom")
        self.assertEqual(row["status"], "segment_done")
        self.assertEqual(row["live_id"], "sr_JKT48_Olla_1789468597")

        self.assertEqual(segmented.get("platform"), "showroom")
        self.assertEqual(segmented.get("thumbnail_url"), COVER)
        self.assertEqual(segmented.get("live_title"), "Olla Live")
        self.assertEqual(segmented.get("live_slug"), "")
        self.assertEqual(segmented.get("live_id"), live_id)

        # Bersih setelah selesai: tidak tertinggal sebagai "sedang direkam".
        self.assertNotIn("jkt48_feri", bot.active_showroom)
        self.assertNotIn("sr:jkt48_feri", bot.active_live_ids)

    def test_kegagalan_download_menandai_sesi_failed(self):
        bot = self._make_bot()
        live_id = "sr_JKT48_Olla_1"

        with patch("bot.main.download_stream", side_effect=bot_main.DownloadError("mati")):
            asyncio.run(bot._record_showroom_task(
                {"username": "jkt48_feri", "hls_url": "https://x/y.m3u8"}, live_id
            ))

        row = database.get_session(live_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertNotIn("jkt48_feri", bot.active_showroom)

        async def fake_find(rooms):
            return []

        bot.showroom.find_live_rooms = fake_find
        asyncio.run(bot._check_showroom())
        self.assertEqual(bot.active_showroom, set())

    def test_resume_setelah_ytdlp_berhenti_awal(self):
        """
        yt-dlp berhenti (error) padahal live masih jalan → task resume dengan
        bagian baru `_r1` memakai URL HLS segar, lalu berhenti saat room
        terbukti offline. Ini pencegah kehilangan potongan awal/tengah live
        seperti insiden 18 Sep 2026 (Sona).
        """
        bot = self._make_bot()
        video = Path(self._tmp.name) / "seg2.mp4"
        video.write_bytes(b"\x00" * 1024)
        segmented = []
        downloads = {"count": 0}
        probes = {"count": 0}

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            downloads["count"] += 1
            if downloads["count"] == 1:
                raise bot_main.DownloadError("stream belum feeding")
            return video

        async def fake_add_segment(**kwargs):
            segmented.append(kwargs)

        async def counting_is_room_live(room_id):
            probes["count"] += 1
            # Probe pertama (setelah kegagalan) → masih live;
            # probe kedua (setelah bagian kedua selesai) → berakhir.
            return probes["count"] <= 1

        async def fake_fresh_url(room_id):
            return "https://cdn.showroom.example/fresh.m3u8"

        bot.merge_mgr.add_segment = fake_add_segment
        bot.showroom.is_room_live = counting_is_room_live
        bot.showroom.scraper.get_live_streaming_url = fake_fresh_url

        room = {
            "username": "jkt48_feri",
            "display_name": "Feri",
            "room_id": "318222",
            "hls_url": "https://cdn.showroom.example/room.m3u8",
        }
        live_id = "sr_JKT48_Olla_123"

        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_showroom_task(room, live_id))

        # Dua kali percobaan download: gagal → resume sukses.
        self.assertEqual(downloads["count"], 2)
        # Hanya bagian kedua yang sampai ke merge manager (bagian 1 gagal).
        self.assertEqual(
            [s["live_id"] for s in segmented], [f"{live_id}_r1"]
        )
        row0 = database.get_session(live_id)
        row1 = database.get_session(f"{live_id}_r1")
        assert row0 is not None and row1 is not None
        self.assertEqual(row0["status"], "failed")
        self.assertEqual(row1["status"], "segment_done")
        # Bagian resume memakai URL HLS segar dari API Showroom.
        self.assertEqual(row1["hls_url"], "https://cdn.showroom.example/fresh.m3u8")
        self.assertNotIn("jkt48_feri", bot.active_showroom)

    def test_download_membawa_url_refresher_segar(self):
        """
        download_stream wajib menerima `url_refresher` yang meminta URL HLS
        segar ke API Showroom — penggantian URL di tengah retry adalah kunci
        lepas dari URL sesi broadcast yang sudah mati (insiden 22 Sep 2026).
        """
        bot = self._make_bot()
        video = Path(self._tmp.name) / "seg_refresh.mp4"
        video.write_bytes(b"\x00" * 1024)
        captured = {}

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            captured.update(kwargs)
            return video

        async def fake_add_segment(**kwargs):
            pass

        async def fake_room_offline(room_id):
            return False

        async def fake_fresh_url(room_id):
            return "https://cdn.showroom.example/fresh-refresher.m3u8"

        bot.merge_mgr.add_segment = fake_add_segment
        bot.showroom.is_room_live = fake_room_offline
        bot.showroom.scraper.get_live_streaming_url = fake_fresh_url

        room = {
            "username": "jkt48_feri",
            "display_name": "Feri",
            "room_id": "318222",
            "hls_url": "https://cdn.showroom.example/room.m3u8",
        }

        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_showroom_task(room, "sr_JKT48_Olla_9"))

        refresher = captured.get("url_refresher")
        self.assertIsNotNone(refresher, "download_stream harus diberi url_refresher")
        self.assertEqual(
            asyncio.run(refresher()),
            "https://cdn.showroom.example/fresh-refresher.m3u8",
        )

    def test_broadcast_berganti_sesi_mengakhiri_task_tanpa_resume(self):
        """
        Room masih live TETAPI sesi broadcast-nya sudah berganti (live_id API
        berbeda) → task berhenti TANPA resume. URL sesi lama digantung CDN
        selamanya; loop utama yang akan memulai rekaman sesi baru dengan URL
        segar (insiden 22 Sep 2026: ±1 jam retry sia-sia di URL mati).
        """
        bot = self._make_bot()
        downloads = {"count": 0}

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            downloads["count"] += 1
            raise bot_main.DownloadError("URL mati digantung CDN")

        async def fake_new_broadcast(room_id):
            return RoomState(
                room_id="318222", is_onlive=True, live_id=222,
                room_url_key="JKT48_Olla",
            )

        bot.showroom.fetch_state = fake_new_broadcast

        room = {
            "username": "jkt48_feri",
            "display_name": "Feri",
            "room_id": "318222",
            "hls_url": "https://cdn.showroom.example/lama.m3u8",
            "showroom_live_id": 111,
        }
        live_id = "sr_JKT48_Olla_111"

        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_showroom_task(room, live_id))

        self.assertEqual(
            downloads["count"], 1,
            "tidak boleh ada resume pada sesi broadcast yang sudah digantikan",
        )
        self.assertIsNone(
            database.get_session(f"{live_id}_r1"),
            "bagian resume tidak boleh dibuat untuk sesi broadcast lama",
        )
        row = database.get_session(live_id)
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertNotIn("jkt48_feri", bot.active_showroom)

    def test_broadcast_berganti_setelah_bagian_selesai(self):
        """
        Bagian selesai bersih, tetapi API menunjukkan sesi broadcast BARU →
        segmen tetap masuk merge manager, lalu task berhenti (tidak lanjut
        bagian _r1 pada sesi lama).
        """
        bot = self._make_bot()
        video = Path(self._tmp.name) / "seg_replaced.mp4"
        video.write_bytes(b"\x00" * 1024)
        segmented = []
        downloads = {"count": 0}

        async def fake_download(hls_url, member_username, live_id, **kwargs):
            downloads["count"] += 1
            return video

        async def fake_add_segment(**kwargs):
            segmented.append(kwargs)

        async def fake_new_broadcast(room_id):
            return RoomState(
                room_id="318222", is_onlive=True, live_id=222,
                room_url_key="JKT48_Olla",
            )

        bot.merge_mgr.add_segment = fake_add_segment
        bot.showroom.fetch_state = fake_new_broadcast

        room = {
            "username": "jkt48_feri",
            "display_name": "Feri",
            "room_id": "318222",
            "hls_url": "https://cdn.showroom.example/lama.m3u8",
            "showroom_live_id": 111,
        }
        live_id = "sr_JKT48_Olla_222"

        with patch("bot.main.download_stream", side_effect=fake_download):
            asyncio.run(bot._record_showroom_task(room, live_id))

        self.assertEqual(downloads["count"], 1)
        self.assertEqual([s["live_id"] for s in segmented], [live_id])
        row = database.get_session(live_id)
        assert row is not None
        self.assertEqual(row["status"], "segment_done")
        self.assertNotIn("jkt48_feri", bot.active_showroom)

    def test_kandidat_hanya_member_showroom_aktif(self):
        """Member yang Showroom-nya dimatikan tidak ikut dipindai."""
        database.set_member_showroom_enabled("jkt48_lulu", False)
        bot = self._make_bot()
        seen = {"rooms": []}

        async def fake_find(rooms):
            seen["rooms"] = rooms
            return []

        bot.showroom.find_live_rooms = fake_find
        asyncio.run(bot._check_showroom())

        names = sorted(r["username"] for r in seen["rooms"])
        self.assertEqual(names, ["jkt48_feri"])


class TestPlatformGroupSeparation(ShowroomIntegrationTestCase):
    def test_grup_idn_dan_showroom_tidak_bercampur(self):
        """
        Member yang sama bisa live di dua platform. Segmen Showroom TIDAK boleh
        masuk ke grup IDN yang sedang menunggu — kalau tidak, dua live berbeda
        tergabung jadi satu video.
        """
        idn_gid = database.create_merge_group(
            "jkt48_lulu", "Lulu", "2026-09-18T03:00:00+00:00", platform="idn"
        )
        sr_gid = database.create_merge_group(
            "jkt48_lulu", "Lulu", "2026-09-18T04:00:00+00:00", platform="showroom"
        )
        self.assertNotEqual(idn_gid, sr_gid)

        found_idn = database.get_active_merge_group("jkt48_lulu", 6000, platform="idn")
        found_sr = database.get_active_merge_group("jkt48_lulu", 6000, platform="showroom")
        self.assertIsNotNone(found_idn)
        self.assertIsNotNone(found_sr)
        assert found_idn is not None and found_sr is not None
        self.assertEqual(found_idn["id"], idn_gid)
        self.assertEqual(found_idn["platform"], "idn")
        self.assertEqual(found_sr["id"], sr_gid)
        self.assertEqual(found_sr["platform"], "showroom")

    def test_tanpa_filter_platform_perilaku_lama_tetap_berfungsi(self):
        """Pemanggil lama (tanpa platform) tetap menerima grup yang menunggu."""
        gid = database.create_merge_group("jkt48_lulu", "Lulu", "2026-09-18T03:00:00+00:00")
        found = database.get_active_merge_group("jkt48_lulu", 6000)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["id"], gid)
        self.assertEqual(found["platform"], "idn")

    def test_kunci_scope_memisahkan_timer_dan_rekaman_berjalan(self):
        mgr = merger.MergeManager()
        mgr.download_started("jkt48_lulu", "idn")
        self.assertIn(mgr._scope("jkt48_lulu", "idn"), mgr._active_downloads)
        self.assertNotIn(mgr._scope("jkt48_lulu", "showroom"), mgr._active_downloads)
        mgr.download_ended("jkt48_lulu", "idn")
        self.assertEqual(mgr._active_downloads, {})
class TestShowroomDecision(ShowroomIntegrationTestCase):
    def _decide_for_platform(self, platform):
        mgr = merger.MergeManager()
        calls = {"live": 0, "probe": 0}

        async def fake_live(username, plat="idn"):
            calls["live"] += 1
            return {"slug": "haii-1", "live_key": "judul-baru"}

        async def fake_probe(username, plat="idn"):
            calls["probe"] += 1
            return None

        mgr._fetch_live = fake_live
        mgr._probe_active = fake_probe

        gid = database.create_merge_group(
            "jkt48_lulu", "Lulu", "2026-09-18T03:00:00+00:00",
            live_title="judul-lama", platform=platform,
        )
        group = database.get_merge_group(gid)
        assert group is not None
        return asyncio.run(mgr._decide(group)), calls

    def test_showroom_tidak_memanggil_idn_lookup(self):
        """
        Judul IDN tidak relevan untuk grup Showroom. Kalau IDN dipanggil dan
        member kebetulan sedang live di IDN, finalisasi Showroom bisa tertahan
        tanpa alasan.
        """
        action, calls = self._decide_for_platform("showroom")
        self.assertEqual(calls["live"], 0)
        self.assertEqual(calls["probe"], 1)
        # idle sangat kecil → tunda, bukan finalize
        self.assertEqual(action[0], "defer")

    def test_idn_tetap_memanggil_idn_lookup(self):
        _, calls = self._decide_for_platform("idn")
        self.assertEqual(calls["live"], 1)


if __name__ == "__main__":
    unittest.main()