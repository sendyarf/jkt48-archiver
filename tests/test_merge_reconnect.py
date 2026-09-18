"""
test_merge_reconnect.py - Tests for merge/reconnect decision logic (packages A-D).

Menguji:
  * REGRESI: window grup dihitung dari SEGMEN TERAKHIR, bukan `created_at`
    (bug: live panjang dengan reconnect bisa pecah jadi beberapa video)
  * matriks keputusan finalize/defer (hard cap, rekaman aktif, IDN slug, HLS probe,
    percepatan idle, sisa window)
  * live baru (slug beda) → grup lama di-finalize, grup baru dibuat
  * segmen rusak/hilang dilewati saat merge
  * ffmpeg concat (varian 1 & fallback) benar-benar menghasilkan file
"""
import asyncio
import contextlib
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

from bot import database, merger
from bot.config import Config
from bot.idn_lookup import live_key_from
from tests.test_member_manager import HLS_LULU, MemberManagerTestCase

MEMBER = "jkt48_lulu"
NAME = "Lulu JKT48"


class MergeTestCase(MemberManagerTestCase):
    """Base: temp DB + MergeManager dengan callback palsu yang bisa diatur."""

    def setUp(self):
        super().setUp()
        self.scratch = Path(self._tmp.name) / "videos"
        self.scratch.mkdir(exist_ok=True)

        self.uploads: list[dict] = []
        self.probe_calls = 0
        self.live_calls = 0
        self.probe_result = None       # True / False / None
        self.live_result = None        # None / {} / {"slug": ..., "title": ...}
        self.probe_raises = False
        self.live_raises = False
        self.probe_platforms: list[str] = []
        self.live_platforms: list[str] = []

        self._orig_consts = (
            merger.MERGE_WINDOW,
            merger.IDLE_FINALIZE_SECONDS,
            merger.MAX_GROUP_SECONDS,
            merger.DEFER_SECONDS,
            merger.SPLIT_ON_TITLE_CHANGE,
        )
        merger.MERGE_WINDOW = 3600
        merger.IDLE_FINALIZE_SECONDS = 1800
        merger.MAX_GROUP_SECONDS = 6 * 3600
        merger.DEFER_SECONDS = 300
        merger.SPLIT_ON_TITLE_CHANGE = True

        self.mgr = merger.MergeManager(
            on_upload_ready=self._fake_upload,
            probe_active=self._fake_probe,
            fetch_live=self._fake_live,
        )

    def tearDown(self):
        with contextlib.suppress(Exception):
            asyncio.run(self.mgr.shutdown())
        (merger.MERGE_WINDOW,
         merger.IDLE_FINALIZE_SECONDS,
         merger.MAX_GROUP_SECONDS,
         merger.DEFER_SECONDS,
         merger.SPLIT_ON_TITLE_CHANGE) = self._orig_consts
        super().tearDown()

    # ── fakes ────────────────────────────────────────────────────────────
    async def _fake_upload(self, **kwargs):
        self.uploads.append(kwargs)

    async def _fake_probe(self, username, platform="idn"):
        self.probe_calls += 1
        self.probe_platforms.append(platform)
        if self.probe_raises:
            raise RuntimeError("HLS probe down")
        return self.probe_result

    async def _fake_live(self, username, platform="idn"):
        self.live_calls += 1
        self.live_platforms.append(platform)
        if self.live_raises:
            raise RuntimeError("IDN down")
        return self.live_result
# ── helpers ─────────────────────────────────────────────────────────
    def _dummy_video(self, name: str, size: int = 4096) -> Path:
        path = self.scratch / name
        path.write_bytes(b"\x00" * size)
        return path

    def _make_group(
        self,
        *,
        age_seconds: int = 0,
        idle_seconds: int = 0,
        slug: str = "",
        live_key: str = "",
        with_segment: bool = True,
        file_name: str = "seg.mp4",
        file_size: int = 4096,
    ) -> int:
        """Buat merge group + (opsional) satu segmen valid dengan timestamp custom."""
        gid = database.create_merge_group(MEMBER, NAME, datetime.now().isoformat())
        if slug or live_key:
            # live_key = identitas sesi live (judul) — wajib diisi agar deteksi
            # "live baru" (judul berubah) bisa diuji.
            database.update_merge_group_slug(
                gid, slug, "", live_key or live_key_from(slug)
            )

        if with_segment:
            live_id = f"{MEMBER}_seg{datetime.now().timestamp()}"
            path = self._dummy_video(file_name, file_size)
            database.insert_live(
                live_id=live_id,
                member_username=MEMBER,
                member_name=NAME,
                started_at=datetime.now().isoformat(),
                hls_url=HLS_LULU,
            )
            database.update_status(
                live_id, "segment_done",
                file_path=str(path),
                file_size_bytes=file_size,
                download_ended_at=datetime.now().isoformat(),
            )
            database.set_session_merge_group(live_id, gid)

        with database._get_conn() as conn:
            conn.execute(
                """UPDATE merge_groups
                   SET created_at = datetime('now', ?),
                       last_segment_at = datetime('now', ?)
                   WHERE id = ?""",
                (f"-{age_seconds} seconds", f"-{idle_seconds} seconds", gid),
            )
        return gid

    def _decide(self, group_id: int) -> tuple:
        group = database.get_merge_group(group_id)
        return asyncio.run(self.mgr._decide(group))


class TestMergeWindowRegression(MergeTestCase):
    """REGRESI: window harus dihitung dari segmen TERAKHIR, bukan `created_at`."""

    def test_long_live_with_reconnect_stays_one_group(self):
        gid = self._make_group()

        # seg2 selesai 40 menit setelah grup dibuat
        with database._get_conn() as conn:
            conn.execute(
                "UPDATE merge_groups SET created_at = datetime('now','-40 minutes'), "
                "last_segment_at = datetime('now') WHERE id = ?", (gid,))
        found = database.get_active_merge_group(MEMBER, 3600)
        self.assertIsNotNone(found, "seg2 harus masuk grup yang sama")
        self.assertEqual(found["id"], gid)

        # seg3 selesai 90 menit setelah grup dibuat, tapi gap dari seg2 hanya 50 menit
        with database._get_conn() as conn:
            conn.execute(
                "UPDATE merge_groups SET created_at = datetime('now','-90 minutes'), "
                "last_segment_at = datetime('now','-50 minutes') WHERE id = ?", (gid,))
        found = database.get_active_merge_group(MEMBER, 3600)
        self.assertIsNotNone(
            found, "live 90 menit dengan gap 50 menit tetap SATU live (satu video)"
        )
        self.assertEqual(found["id"], gid)

    def test_group_beyond_window_is_not_reused(self):
        gid = self._make_group(idle_seconds=4000)
        self.assertIsNone(database.get_active_merge_group(MEMBER, 3600))
        # Grup tidak dihapus, hanya tidak dianggap aktif
        self.assertEqual(database.get_merge_group(gid)["status"], "waiting")
class TestMergeDecision(MergeTestCase):
    """Matriks keputusan finalize/defer."""

    def test_hard_cap_forces_finalize(self):
        gid = self._make_group(age_seconds=7 * 3600)
        action, _ = self._decide(gid)
        self.assertEqual(action, "finalize")

    def test_hard_cap_waits_while_recording(self):
        gid = self._make_group(age_seconds=7 * 3600)
        self.mgr.download_started(MEMBER)
        action, delay = self._decide(gid)
        self.assertEqual(action, "defer", "jangan potong rekaman yang sedang jalan")
        self.assertEqual(delay, merger.DEFER_SECONDS)

    def test_active_recording_defers(self):
        gid = self._make_group(idle_seconds=3600)
        self.mgr.download_started(MEMBER)
        action, _ = self._decide(gid)
        self.assertEqual(action, "defer")

    def test_idn_same_title_defers_even_with_new_slug(self):
        # KASUS NYATA jkt48_daisy: reconnect 50 menit dengan JUDUL SAMA tapi slug BARU
        # (haii-260913192155 → haii-260913201248). Harus tetap satu video.
        gid = self._make_group(idle_seconds=3400, slug="haii-260913192155")
        self.live_result = {
            "slug": "haii-260913201248", "title": "haii", "live_key": "haii",
        }
        action, _ = self._decide(gid)
        self.assertEqual(action, "defer", "slug beda tapi judul sama = masih live yang sama")

    def test_idn_different_title_is_new_live(self):
        gid = self._make_group(idle_seconds=300, slug="haii-260913192155")
        self.live_result = {
            "slug": "makan-260916010000", "title": "makan", "live_key": "makan",
        }
        action, _ = self._decide(gid)
        self.assertEqual(action, "finalize", "judul berubah = live baru → upload sekarang")

    def test_idn_not_live_does_not_finalize_early(self):
        # "IDN bilang tidak live" BUKAN bukti live selesai: reconnect membuat record
        # IDN baru (kasus daisy: jeda 50 menit), jadi harus tetap menunggu window.
        gid = self._make_group(idle_seconds=120)   # baru 2 menit
        self.live_result = {}                      # IDN: member tidak live
        self.probe_result = False                  # HLS: offline
        action, delay = self._decide(gid)
        self.assertEqual(action, "defer")
        self.assertAlmostEqual(delay, 3480, delta=5)

    def test_hls_active_defers_even_when_idn_unavailable(self):
        gid = self._make_group(idle_seconds=3500)
        self.live_result = None      # IDN tidak diketahui
        self.probe_result = True     # HLS masih mengalir
        action, _ = self._decide(gid)
        self.assertEqual(action, "defer")

    def test_window_elapsed_finalizes(self):
        # PENTING: kalau window sudah habis, HARUS finalize. (Bug sebelumnya: kondisi
        # ini hanya menghasilkan defer 5 detik berulang → video tidak pernah diupload.)
        gid = self._make_group(idle_seconds=3700)
        self.live_result = None
        self.probe_result = False
        action, delay = self._decide(gid)
        self.assertEqual(action, "finalize")
        self.assertEqual(delay, 0)

    def test_window_elapsed_finalizes_without_any_callback(self):
        mgr = merger.MergeManager()          # tanpa probe_active / fetch_live
        gid = self._make_group(idle_seconds=5000)
        action, _ = asyncio.run(mgr._decide(database.get_merge_group(gid)))
        self.assertEqual(action, "finalize")

    def test_window_elapsed_wins_over_hls_still_active(self):
        # Window adalah penentu utama: kalau stream "terlihat hidup" terus (mis. playlist
        # menggantung) tapi tidak ada segmen baru selama > window, video tetap diupload
        # agar tidak tertahan selamanya (hard cap 6 jam masih jadi jaring pengaman).
        gid = self._make_group(idle_seconds=4000)
        self.live_result = None
        self.probe_result = True
        action, _ = self._decide(gid)
        self.assertEqual(action, "finalize")

    def test_idle_threshold_finalizes_when_idn_unavailable(self):
        gid = self._make_group(idle_seconds=1900)
        self.live_result = None
        self.probe_result = False
        action, _ = self._decide(gid)
        self.assertEqual(action, "finalize")

    def test_short_idle_waits_remaining_window(self):
        gid = self._make_group(idle_seconds=600)
        self.live_result = None
        self.probe_result = False      # sudah offline, tapi baru 10 menit
        action, delay = self._decide(gid)
        self.assertEqual(action, "defer")
        self.assertAlmostEqual(delay, 3000, delta=5)

    def test_idn_and_probe_failures_are_ignored(self):
        gid = self._make_group(idle_seconds=1900)
        self.live_raises = True
        self.probe_raises = True
        action, _ = self._decide(gid)   # tidak boleh melempar exception
        self.assertEqual(action, "finalize", "fallback ke idle threshold")

    def test_without_callbacks_only_window_applies(self):
        mgr = merger.MergeManager()     # tanpa probe_active / fetch_live
        gid = self._make_group(idle_seconds=60)
        group = database.get_merge_group(gid)
        action, delay = asyncio.run(mgr._decide(group))
        self.assertEqual(action, "defer")
        self.assertAlmostEqual(delay, 3540, delta=5)
class TestAddSegmentSlugHandling(MergeTestCase):
    """Perilaku add_segment terhadap slug sesi IDN (live baru vs reconnect)."""

    def _add(self, live_id: str, path: Path, live_slug: str = "", title: str = ""):
        """Simulasi satu segmen selesai: baris sesi DB + add_segment (seperti main.py)."""
        database.insert_live(
            live_id=live_id, member_username=MEMBER, member_name=NAME,
            started_at=datetime.now().isoformat(), hls_url=HLS_LULU,
        )
        database.update_status(
            live_id, "segment_done",
            file_path=str(path),
            file_size_bytes=path.stat().st_size,
            download_ended_at=datetime.now().isoformat(),
        )
        if live_slug:
            database.set_session_live_slug(live_id, live_slug)

        asyncio.run(self.mgr.add_segment(
            live_id=live_id,
            member_username=MEMBER,
            member_name=NAME,
            started_at=datetime.now().isoformat(),
            file_path=str(path),
            live_slug=live_slug,
            live_title=title,
        ))

    def test_new_live_slug_finalizes_previous_group(self):
        old_gid = self._make_group(slug="haii-260915223455", file_name="old.mp4")
        new_path = self._dummy_video("new.mp4")

        self._add("jkt48_lulu_newlive", new_path,
                  live_slug="makan-260916010000", title="makan")

        # Grup lama langsung ditutup & diupload (tidak digabung dengan live baru)
        self.assertEqual(database.get_merge_group(old_gid)["status"], "done")
        self.assertEqual(len(self.uploads), 1)

        # Grup baru dibuat untuk live baru, membawa slug + judul barunya
        active = database.get_active_merge_group(MEMBER, 3600)
        self.assertIsNotNone(active)
        self.assertNotEqual(active["id"], old_gid)
        self.assertEqual(active["live_slug"], "makan-260916010000")
        self.assertEqual(active["live_title"], "makan")

    def test_same_slug_extends_existing_group(self):
        gid = self._make_group(slug="haii-260915223455", file_name="a.mp4")
        self._add("jkt48_lulu_seg2", self._dummy_video("b.mp4"),
                  live_slug="haii-260915223455", title="haii")

        self.assertEqual(database.get_merge_group(gid)["status"], "waiting")
        self.assertEqual(len(database.get_merge_segments(gid)), 2)
        self.assertEqual(self.uploads, [], "reconnect tidak boleh upload terpisah")

    def test_empty_slug_falls_back_to_window_behaviour(self):
        gid = self._make_group(slug="haii-260915223455", file_name="a.mp4")
        self._add("jkt48_lulu_seg2", self._dummy_video("b.mp4"), live_slug="")

        self.assertEqual(database.get_merge_group(gid)["status"], "waiting")
        self.assertEqual(len(database.get_merge_segments(gid)), 2)


class TestSegmentFiltering(MergeTestCase):
    def test_missing_and_empty_segments_are_skipped(self):
        gid = database.create_merge_group(MEMBER, NAME, datetime.now().isoformat())

        def add_segment_row(live_id: str, file_path: str, size: int):
            database.insert_live(
                live_id=live_id, member_username=MEMBER, member_name=NAME,
                started_at=datetime.now().isoformat(), hls_url=HLS_LULU,
            )
            database.update_status(
                live_id, "segment_done", file_path=file_path,
                file_size_bytes=size, download_ended_at=datetime.now().isoformat(),
            )
            database.set_session_merge_group(live_id, gid)

        good = self._dummy_video("good.mp4", 2048)
        empty = self.scratch / "empty.mp4"
        empty.write_bytes(b"")

        add_segment_row("seg-good", str(good), 2048)
        add_segment_row("seg-missing", str(self.scratch / "nope.mp4"), 10)
        add_segment_row("seg-empty", str(empty), 0)

        asyncio.run(self.mgr._merge_and_upload(gid, MEMBER))

        # Hanya segmen valid yang diupload
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(self.uploads[0]["file_path"], str(good))
        # Segmen rusak ditandai failed (tidak diadopsi ulang selamanya)
        self.assertEqual(database.get_session("seg-missing")["status"], "failed")
        self.assertEqual(database.get_session("seg-empty")["status"], "failed")


class TestConcatFiles(MergeTestCase):
    def _make_clip(self, name: str, seconds: int = 1) -> Path:
        path = self.scratch / name
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=15",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return path

    def test_concat_two_clips_produces_playable_file(self):
        old_dir = Config.DOWNLOAD_DIR
        Config.DOWNLOAD_DIR = str(self.scratch)
        try:
            clip1 = self._make_clip("clip1.mp4")
            clip2 = self._make_clip("clip2.mp4")

            mgr = merger.MergeManager()
            merged = asyncio.run(mgr._concat_files([str(clip1), str(clip2)], MEMBER, 4321))

            self.assertIsNotNone(merged, "concat harus berhasil")
            merged_path = Path(merged)
            self.assertTrue(merged_path.exists())
            self.assertGreater(merged_path.stat().st_size, 0)
            # File list sementara dibersihkan
            self.assertFalse((Path(Config.DOWNLOAD_DIR) / "merge_4321.txt").exists())
        finally:
            Config.DOWNLOAD_DIR = old_dir
class TestDaisyReconnectRegression(TestAddSegmentSlugHandling):
    """
    REGRESI dari bukti nyata (jkt48_daisy, 13 Sep 2026).

    Satu live yang lag/reconnect berkali-kali menghasilkan SLUG BERBEDA dengan JUDUL SAMA:
        haii-260913192155  →  haii-260913201248  →  haii-260913201503
    Semua harus masuk ke SATU merge group (= satu video), karena identitas live
    adalah JUDUL-nya, bukan slug.
    """

    DAISY_SLUGS = [
        "haii-260913192155",
        "haii-260913201248",
        "haii-260913201503",
    ]

    def test_three_slugs_same_title_stay_in_one_group(self):
        for idx, slug in enumerate(self.DAISY_SLUGS, start=1):
            self._add(
                f"daisy_seg{idx}",
                self._dummy_video(f"daisy{idx}.mp4"),
                live_slug=slug,
                title="haii",
            )

        group = database.get_active_merge_group(MEMBER, merger.MERGE_WINDOW)
        self.assertIsNotNone(group, "harus ada satu grup aktif")
        self.assertEqual(
            len(database.get_merge_segments(group["id"])), 3,
            "ketiga reconnect harus berada di grup yang sama",
        )
        self.assertEqual(self.uploads, [], "reconnect tidak boleh memicu upload terpisah")
        self.assertEqual(group["live_key"], "haii")
        # slug terakhir tercatat (untuk audit) — dan tetap satu grup
        self.assertEqual(group["live_slug"], self.DAISY_SLUGS[-1])
        # hanya ada SATU grup untuk member ini
        with database._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM merge_groups WHERE member_username = ?", (MEMBER,)
            ).fetchone()[0]
        self.assertEqual(count, 1, "tidak boleh ada grup kedua")

    def test_title_change_starts_second_video(self):
        self._add("seg1", self._dummy_video("s1.mp4"), "haii-260913192155", "haii")
        g1 = database.get_active_merge_group(MEMBER, merger.MERGE_WINDOW)

        self._add("seg2", self._dummy_video("s2.mp4"), "makan-260916010000", "makan")
        g2 = database.get_active_merge_group(MEMBER, merger.MERGE_WINDOW)

        self.assertNotEqual(g1["id"], g2["id"], "judul beda = video beda")
        self.assertEqual(database.get_merge_group(g1["id"])["status"], "done")
        self.assertEqual(len(self.uploads), 1, "live lama langsung diupload")
        self.assertEqual(g2["live_key"], "makan")

    def test_split_disabled_merges_different_titles(self):
        merger.SPLIT_ON_TITLE_CHANGE = False
        self._add("seg1", self._dummy_video("s1.mp4"), "haii-260913192155", "haii")
        self._add("seg2", self._dummy_video("s2.mp4"), "makan-260916010000", "makan")

        group = database.get_active_merge_group(MEMBER, merger.MERGE_WINDOW)
        self.assertEqual(len(database.get_merge_segments(group["id"])), 2)
        self.assertEqual(self.uploads, [], "SPLIT_ON_TITLE_CHANGE=false → selalu gabung")

    def test_idle_acceleration_off_by_default(self):
        # Tanpa percepatan idle (IDLE_FINALIZE_SECONDS = 0), reconnect panjang
        # (mis. 33 menit, kasus daisy) TIDAK boleh membuat video terpecah.
        merger.IDLE_FINALIZE_SECONDS = 0
        gid = self._make_group(idle_seconds=2000)
        self.live_result = None
        self.probe_result = False
        action, delay = self._decide(gid)
        self.assertEqual(action, "defer")
        self.assertAlmostEqual(delay, 1600, delta=5)
class TestCarissaTitleEditRegression(TestAddSegmentSlugHandling):
    """
    REGRESI dari bukti nyata (jkt48_carissa, 15 Sep 2026).

    Member MENGEDIT JUDUL di tengah satu live; tiap (re)connect menghasilkan slug baru
    dengan judul baru — semuanya dalam 11 menit:
        my-bestie-ku-sini-join-260915164353   (16:43:53)
        my-bestie-ku-sini-join-260915165114   (16:51:14)
        sini-my-bestieee-260915165258         (16:52:58)
        sini-my-bestie-akuu-260915165451      (16:54:51)
    Ini SATU live → harus SATU video. Karena itu heuristik "judul berubah = live baru"
    DEFAULT NONAKTIF (MERGE_SPLIT_ON_TITLE_CHANGE=false).
    """

    CARISSA_SLUGS = [
        "my-bestie-ku-sini-join-260915164353",
        "my-bestie-ku-sini-join-260915165114",
        "sini-my-bestieee-260915165258",
        "sini-my-bestie-akuu-260915165451",
    ]

    def test_title_edits_do_not_split_when_flag_disabled(self):
        merger.SPLIT_ON_TITLE_CHANGE = False   # default produksi
        for idx, slug in enumerate(self.CARISSA_SLUGS, start=1):
            self._add(
                f"carissa_seg{idx}",
                self._dummy_video(f"carissa{idx}.mp4"),
                live_slug=slug,
            )

        group = database.get_active_merge_group(MEMBER, merger.MERGE_WINDOW)
        self.assertEqual(
            len(database.get_merge_segments(group["id"])), 4,
            "4 slug dengan 3 judul berbeda tetap SATU live",
        )
        self.assertEqual(self.uploads, [], "tidak boleh ada upload prematur")
        with database._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM merge_groups WHERE member_username = ?", (MEMBER,)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_decide_defers_while_live_even_if_title_changed(self):
        # Saat _decide menemukan judul live berbeda tapi fitur split dimatikan,
        # harus DEFER (tunggu jeda/window), bukan finalize.
        merger.SPLIT_ON_TITLE_CHANGE = False
        gid = self._make_group(idle_seconds=200, live_key="my-bestie-ku-sini-join")
        self.live_result = {
            "slug": "sini-my-bestie-akuu-260915165451",
            "title": "sini-my-bestie-akuu",
            "live_key": "sini-my-bestie-akuu",
        }
        action, _ = self._decide(gid)
        self.assertEqual(action, "defer")

    def test_flag_enabled_still_splits_on_title_change(self):
        # Fitur opsional: kalau dinyalakan, judul berubah = live baru (perilaku lama).
        merger.SPLIT_ON_TITLE_CHANGE = True
        gid = self._make_group(idle_seconds=200, live_key="haii")
        self.live_result = {"slug": "makan-x", "title": "makan", "live_key": "makan"}
        action, _ = self._decide(gid)
        self.assertEqual(action, "finalize")