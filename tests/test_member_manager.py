"""
test_member_manager.py - Unit tests for member/channel management.

Covers: add / stop / resume / remove / set-hls, members.txt sync rules,
monitor filtering (enabled), and admin bot message splitting.
"""
import tempfile
import unittest
from pathlib import Path

from bot import database, member_manager
from bot.admin_bot import split_text
from bot.config import Config

SAMPLE_MEMBERS = """# Daftar username IDN member yang ingin dipantau.
# Ambil dari URL: https://www.idn.app/jkt48_delynn tulis: jkt48_delynn
# Jika file ini KOSONG bot memantau SEMUA member JKT48

jkt48_delynn
jkt48_lulu
jkt48-official
"""

HLS_LULU = (
    "https://4b964ca68cf1.us-east-1.playback.live-video.net/api/video/v1/"
    "us-east-1.050891932989.channel.LuflGrysVdSz.m3u8"
)


class MemberManagerTestCase(unittest.TestCase):
    """Base class: temp DB + temp members.txt, restored in tearDown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._old_db = Config.DB_PATH
        self._old_members = Config.MEMBERS_FILE
        Config.DB_PATH = str(base / "test.db")
        Config.MEMBERS_FILE = str(base / "members.txt")
        (base / "members.txt").write_text(SAMPLE_MEMBERS, encoding="utf-8")
        database.init_db()
        # Simulasi langkah bot saat startup (main.sync_members_whitelist)
        database.register_members_if_not_exists(list(Config.load_members()))
        self.members_file = base / "members.txt"

    def tearDown(self):
        Config.DB_PATH = self._old_db
        Config.MEMBERS_FILE = self._old_members
        self._tmp.cleanup()

    def _members_text(self) -> str:
        return self.members_file.read_text(encoding="utf-8")


class TestUsernameHelpers(MemberManagerTestCase):
    def test_normalize_username(self):
        cases = {
            "jkt48_lulu": "jkt48_lulu",
            "  JKT48_LULU  ": "jkt48_lulu",
            "@jkt48_lulu": "jkt48_lulu",
            "jkt48_lulu/": "jkt48_lulu",
            "https://www.idn.app/jkt48_lulu": "jkt48_lulu",
            "https://www.idn.app/@jkt48_lulu?tab=live": "jkt48_lulu",
            "jkt48-official": "jkt48-official",
            "": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(member_manager.normalize_username(raw), expected, msg=raw)

    def test_channel_id_from_hls(self):
        self.assertEqual(member_manager.channel_id_from_hls(HLS_LULU), "LuflGrysVdSz")
        self.assertEqual(member_manager.channel_id_from_hls(""), "")

    def test_validate_username(self):
        self.assertIsNone(member_manager.validate_username("jkt48_lulu"))
        self.assertIsNone(member_manager.validate_username("jkt48-official"))
        self.assertIsNotNone(member_manager.validate_username(""))
        self.assertIsNotNone(member_manager.validate_username("bad name"))
class TestStopResumeAddRemove(MemberManagerTestCase):
    def test_add_member_registers_enables_and_syncs_file(self):
        result = member_manager.add_member("jkt48_newbie", display_name="Newbie JKT48")
        self.assertTrue(result["ok"])
        self.assertTrue(result["created"])

        row = database.get_member_hls("jkt48_newbie")
        self.assertIsNotNone(row)
        self.assertEqual(row["enabled"], 1)
        self.assertEqual(row["display_name"], "Newbie JKT48")

        # members.txt: entry baru ditulis, komentar header tidak hilang
        text = self._members_text()
        self.assertIn("\njkt48_newbie", text)
        self.assertIn("https://www.idn.app/jkt48_delynn", text)

    def test_add_member_with_manual_hls_is_confirmed(self):
        result = member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        self.assertTrue(result["ok"])
        row = database.get_member_hls("jkt48_lulu")
        self.assertEqual(row["hls_url"], HLS_LULU)
        self.assertEqual(row["hls_confirmed"], 1)

    def test_stop_member_disables_and_keeps_hls(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        result = member_manager.stop_member("jkt48_lulu")
        self.assertTrue(result["ok"])

        row = database.get_member_hls("jkt48_lulu")
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(row["hls_url"], HLS_LULU)  # HLS tetap tersimpan

        # Tidak di-probe monitor & tidak akan di-discovery ulang
        monitored = [m["username"] for m in database.get_all_member_hls(include_disabled=False)]
        self.assertNotIn("jkt48_lulu", monitored)
        self.assertNotIn("jkt48_lulu", [m["username"] for m in database.get_members_without_hls()])

        # members.txt: entry ditandai STOP, komentar header tetap utuh
        text = self._members_text()
        self.assertIn("# STOPPED: jkt48_lulu", text)
        self.assertNotIn("\njkt48_lulu\n", text)
        self.assertIn("https://www.idn.app/jkt48_delynn", text)

    def test_stop_member_twice_is_idempotent(self):
        member_manager.stop_member("jkt48-official")
        second = member_manager.stop_member("jkt48-official")
        self.assertTrue(second["ok"])
        self.assertFalse(second["was_enabled"])

    def test_whitelist_sync_does_not_resurrect_stopped_member(self):
        member_manager.stop_member("jkt48-official")
        self.assertEqual(database.get_member_hls("jkt48-official")["enabled"], 0)

        # Simulasi sync members.txt tiap siklus (hot-reload)
        database.register_members_if_not_exists(list(Config.load_members()))
        self.assertEqual(database.get_member_hls("jkt48-official")["enabled"], 0)

    def test_resume_member_reactivates_with_existing_hls(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        member_manager.stop_member("jkt48_lulu")

        result = member_manager.resume_member("jkt48_lulu")
        self.assertTrue(result["ok"])

        row = database.get_member_hls("jkt48_lulu")
        self.assertEqual(row["enabled"], 1)
        self.assertEqual(row["hls_url"], HLS_LULU)
        self.assertIn("\njkt48_lulu", self._members_text())

    def test_resume_member_accepts_short_name_without_creating_new_member(self):
        member_manager.stop_member("jkt48-official")
        before = len(database.get_all_member_hls(include_disabled=True))

        result = member_manager.resume_member("official")
        self.assertTrue(result["ok"])
        self.assertEqual(result["username"], "jkt48-official")

        # Tidak boleh membuat member baru bernama 'official'
        self.assertIsNone(database.get_member_hls("official"))
        self.assertEqual(
            len(database.get_all_member_hls(include_disabled=True)), before
        )
        self.assertEqual(database.get_member_hls("jkt48-official")["enabled"], 1)

    def test_remove_member_deletes_row_and_entry(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        result = member_manager.remove_member("jkt48_lulu")
        self.assertTrue(result["ok"])
        self.assertIsNone(database.get_member_hls("jkt48_lulu"))
        self.assertNotIn("jkt48_lulu", self._members_text())

    def test_set_member_hls_validates_and_stores(self):
        bad = member_manager.set_member_hls("jkt48_lulu", "ftp://nope")
        self.assertFalse(bad["ok"])

        good = member_manager.set_member_hls("jkt48_lulu", HLS_LULU)
        self.assertTrue(good["ok"])
        row = database.get_member_hls("jkt48_lulu")
        self.assertEqual(row["hls_url"], HLS_LULU)
        self.assertEqual(row["hls_confirmed"], 1)
        self.assertEqual(row["enabled"], 1)

    def test_add_member_rejects_invalid_username(self):
        result = member_manager.add_member("bad name!")
        self.assertFalse(result["ok"])
class TestListing(MemberManagerTestCase):
    def test_list_modes_and_filters(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        member_manager.stop_member("jkt48-official")

        # member yang hanya ditulis manual di members.txt (belum di-sync ke DB)
        with open(self.members_file, "a", encoding="utf-8") as f:
            f.write("jkt48_manual\n")

        all_rows = {r["username"]: r for r in member_manager.list_members("all")}
        self.assertIn("jkt48_delynn", all_rows)
        self.assertTrue(all_rows["jkt48_delynn"]["in_db"])
        self.assertTrue(all_rows["jkt48-official"]["in_db"])
        self.assertFalse(all_rows["jkt48_manual"]["in_db"])
        self.assertTrue(all_rows["jkt48_manual"]["in_members_file"])
        self.assertFalse(all_rows["jkt48-official"]["in_whitelist"])
        self.assertTrue(all_rows["jkt48-official"]["in_members_file"])

        stopped = [r["username"] for r in member_manager.list_members("stopped")]
        self.assertEqual(stopped, ["jkt48-official"])

        active = [r["username"] for r in member_manager.list_members("active")]
        self.assertIn("jkt48_lulu", active)
        self.assertNotIn("jkt48-official", active)

        unknown = [r["username"] for r in member_manager.list_members("unknown")]
        self.assertIn("jkt48_delynn", unknown)
        self.assertNotIn("jkt48_lulu", unknown)

    def test_stopped_marker_only_member_still_resolvable(self):
        # Member yang hanya ada di members.txt, lalu di-stop
        with open(self.members_file, "a", encoding="utf-8") as f:
            f.write("jkt48_temp\n")

        member_manager.stop_member("jkt48_temp")
        self.assertIn("# STOPPED: jkt48_temp", self._members_text())

        row, error = member_manager.resolve_member_token("jkt48_temp")
        self.assertEqual(error, "")
        self.assertFalse(row["enabled"])

        result = member_manager.resume_member("jkt48_temp")
        self.assertTrue(result["ok"])
        self.assertNotIn("# STOPPED: jkt48_temp", self._members_text())

    def test_resolve_member_token_with_short_name(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        row, error = member_manager.resolve_member_token("lulu")
        self.assertEqual(error, "")
        self.assertEqual(row["username"], "jkt48_lulu")

        row, error = member_manager.resolve_member_token("tidak-ada")
        self.assertIsNone(row)
        self.assertIn("tidak ditemukan", error)

    def test_format_member_list_contains_status_and_hls(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        member_manager.stop_member("jkt48-official")
        text = member_manager.format_member_list(member_manager.list_members("all"), "all")
        self.assertIn("jkt48_lulu", text)
        self.assertIn("LuflGrysVdSz", text)
        self.assertIn("STOP", text)

    def test_format_status_summary_counts(self):
        member_manager.add_member("jkt48_lulu", hls_url=HLS_LULU)
        member_manager.stop_member("jkt48-official")
        summary = member_manager.format_status_summary(member_manager.list_members("all"))
        self.assertIn("Total member: 3", summary)
        self.assertIn("HLS diketahui: 1/3", summary)


class TestAdminBotHelpers(unittest.TestCase):
    def test_split_text_short(self):
        self.assertEqual(split_text("halo"), ["halo"])

    def test_split_text_respects_limit(self):
        text = "\n".join(f"baris-{i:04d} " + "x" * 60 for i in range(200))
        chunks = split_text(text, limit=500)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 500)
        self.assertEqual("\n".join(chunks), text)

    def test_config_admin_ids(self):
        old_admin = Config.ADMIN_CHAT_ID
        old_extra = Config.TELEGRAM_ADMIN_IDS
        try:
            Config.ADMIN_CHAT_ID = 111
            Config.TELEGRAM_ADMIN_IDS = "222, 333 ,x"
            self.assertEqual(Config.admin_ids(), [111, 222, 333])
            Config.TELEGRAM_ADMIN_IDS = "111"
            self.assertEqual(Config.admin_ids(), [111])
        finally:
            Config.ADMIN_CHAT_ID = old_admin
            Config.TELEGRAM_ADMIN_IDS = old_extra


if __name__ == "__main__":
    unittest.main()