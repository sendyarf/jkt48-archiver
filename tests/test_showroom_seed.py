"""
test_showroom_seed.py - Tes Fase 1: migrasi skema & seed room Showroom.

Yang diuji:
1. Migrasi kolom baru idempoten (init_db dua kali) dan baris lama jadi 'idn'.
2. set_member_showroom tidak menyentuh kolom IDN / enabled.
3. get_members_without_hls melewati member showroom-only.
4. build_rows menolak data tidak valid tanpa membuat baris rusak.
5. seed() menulis ke database, --dry-run tidak menulis apa pun.
6. insert_live / create_merge_group menandai platform dengan benar.
"""
import json
import tempfile
import unittest
from pathlib import Path

from bot import database, seed_showroom
from bot.config import Config

NEW_ROOM_COLUMNS = ("showroom_room_id", "showroom_name", "showroom_enabled", "showroom_only")

SAMPLE_ROOMS = {
    "note": "fixture",
    "rooms": [
        {
            "room_id": "111111",
            "display_name": "Lulu",
            "showroom_name": "Lulu / ルル（JKT48）",
            "idn_username": "jkt48_lulu",
            "profile_image": "",
        },
        {
            "room_id": "222222",
            "display_name": "Sona",
            "showroom_name": "Sona / ソナ (JKT48)",
            "idn_username": "jkt48_sona",
            "profile_image": "",
        },
        {
            "room_id": "333333",
            "display_name": "OnlyShowroom",
            "showroom_name": "OnlyShowroom (JKT48)",
            "idn_username": None,
            "profile_image": "",
        },
    ],
    "official_rooms": [
        {"room_id": "999999", "display_name": "JKT48 Official SHOWROOM",
         "showroom_name": "JKT48 Official SHOWROOM", "profile_image": ""},
    ],
}


class ShowroomBase(unittest.TestCase):
    """DB + file daftar room sementara, dikembalikan di tearDown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._old_db = Config.DB_PATH
        self._old_rooms_file = Config.SHOWROOM_ROOMS_FILE
        Config.DB_PATH = str(base / "test.db")
        self.rooms_file = base / "showroom_rooms.json"
        self.rooms_file.write_text(json.dumps(SAMPLE_ROOMS), encoding="utf-8")
        Config.SHOWROOM_ROOMS_FILE = str(self.rooms_file)
        database.init_db()

    def tearDown(self):
        Config.DB_PATH = self._old_db
        Config.SHOWROOM_ROOMS_FILE = self._old_rooms_file
        self._tmp.cleanup()

    def _columns(self, table: str) -> set[str]:
        import sqlite3
        conn = sqlite3.connect(Config.DB_PATH)
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def _member(self, username: str) -> dict:
        return database.get_member_hls(username) or {}


class TestMigration(ShowroomBase):
    def test_new_columns_exist(self):
        for table in ("live_sessions", "merge_groups"):
            self.assertIn("platform", self._columns(table))
        for column in NEW_ROOM_COLUMNS:
            self.assertIn(column, self._columns("member_hls"))

    def test_migration_is_idempotent(self):
        before = {t: self._columns(t) for t in ("live_sessions", "merge_groups", "member_hls")}
        database.init_db()
        database.init_db()
        after = {t: self._columns(t) for t in ("live_sessions", "merge_groups", "member_hls")}
        self.assertEqual(before, after)

    def test_legacy_rows_default_to_idn(self):
        database.insert_live(live_id="legacy_1", member_username="jkt48_lulu",
                             member_name="Lulu", started_at="2026-09-18T10:00:00")
        with database._get_conn() as conn:
            row = conn.execute(
                "SELECT platform FROM live_sessions WHERE live_id = 'legacy_1'"
            ).fetchone()
        self.assertEqual(row["platform"], "idn")

    def test_insert_live_and_merge_group_platform(self):
        database.insert_live(live_id="sr_1", member_username="jkt48_lulu",
                             platform="showroom")
        group_id = database.create_merge_group(
            member_username="jkt48_lulu", member_name="Lulu",
            started_at="2026-09-18T11:00:00", platform="showroom",
        )
        with database._get_conn() as conn:
            live = conn.execute(
                "SELECT platform FROM live_sessions WHERE live_id = 'sr_1'"
            ).fetchone()
            group = conn.execute(
                "SELECT platform FROM merge_groups WHERE id = ?", (group_id,)
            ).fetchone()
        self.assertEqual(live["platform"], "showroom")
        self.assertEqual(group["platform"], "showroom")

    def test_default_platform_is_idn(self):
        group_id = database.create_merge_group(
            member_username="jkt48_lulu", member_name="Lulu", started_at="2026-09-18T12:00:00"
        )
        with database._get_conn() as conn:
            group = conn.execute(
                "SELECT platform FROM merge_groups WHERE id = ?", (group_id,)
            ).fetchone()
        self.assertEqual(group["platform"], "idn")


class TestSetMemberShowroom(ShowroomBase):
    def test_creates_and_updates_row(self):
        database.set_member_showroom("jkt48_lulu", "111111", "Lulu / ルル", "Lulu")
        member = self._member("jkt48_lulu")
        self.assertEqual(member["showroom_room_id"], "111111")
        self.assertEqual(member["showroom_name"], "Lulu / ルル")
        self.assertEqual(member["showroom_only"], 0)

        database.set_member_showroom("jkt48_lulu", "111112", "Lulu Baru", "Lulu")
        member = self._member("jkt48_lulu")
        self.assertEqual(member["showroom_room_id"], "111112")
        self.assertEqual(member["showroom_name"], "Lulu Baru")

    def test_does_not_touch_idn_columns_or_enabled(self):
        database.upsert_member_hls("jkt48_lulu", "Lulu", hls_url="https://hls/lulu.m3u8",
                                   confirmed=True)
        self.assertTrue(database.set_member_enabled("jkt48_lulu", False))
        before = self._member("jkt48_lulu")

        database.set_member_showroom("jkt48_lulu", "111111", "Lulu / ルル", "Lulu")
        after = self._member("jkt48_lulu")

        self.assertEqual(after["hls_url"], before["hls_url"])
        self.assertEqual(after["hls_confirmed"], before["hls_confirmed"])
        self.assertEqual(after["enabled"], 0)
        self.assertEqual(after["showroom_room_id"], "111111")

    def test_does_not_clobber_human_display_name(self):
        database.upsert_member_hls("jkt48_raisha", "Raisha JKT48")
        database.set_member_showroom("jkt48_raisha", "400718", "Raisha / ライシャ", "Raisha")
        member = self._member("jkt48_raisha")
        self.assertEqual(member["display_name"], "Raisha JKT48")
        self.assertEqual(member["showroom_room_id"], "400718")

    def test_fills_placeholder_display_name(self):
        database.register_members_if_not_exists(["jkt48_lulu"])
        self.assertEqual(self._member("jkt48_lulu")["display_name"], "jkt48_lulu")

        database.set_member_showroom("jkt48_lulu", "318232", "Lulu / ルル", "Lulu")

        self.assertEqual(self._member("jkt48_lulu")["display_name"], "Lulu")

    def test_blank_room_id_stores_null(self):
        database.set_member_showroom("jkt48_lulu", "   ", "Lulu", "Lulu")
        self.assertIsNone(self._member("jkt48_lulu")["showroom_room_id"])

    def test_showroom_only_row_is_skipped_by_idn_discovery(self):
        database.set_member_showroom("showroom_amanda", None, "Amanda", "Amanda",
                                     showroom_only=True)
        database.set_member_showroom("jkt48_lulu", "111111", "Lulu", "Lulu")

        pending = [m["username"] for m in database.get_members_without_hls()]
        self.assertIn("jkt48_lulu", pending)
        self.assertNotIn("showroom_amanda", pending)

    def test_showroom_only_flag_does_not_affect_disabled_members(self):
        database.set_member_showroom("showroom_amanda", None, "Amanda", "Amanda",
                                     showroom_only=True)
        self.assertEqual(self._member("showroom_amanda")["enabled"], 1)


class TestBuildRows(ShowroomBase):
    def test_maps_idn_username_and_marks_showroom_only(self):
        rows, problems = seed_showroom.build_rows(SAMPLE_ROOMS["rooms"])
        by_user = {r["username"]: r for r in rows}
        self.assertEqual(problems, [])
        self.assertEqual(len(rows), 3)
        self.assertIn("jkt48_lulu", by_user)
        self.assertFalse(by_user["jkt48_lulu"]["showroom_only"])
        self.assertIn("showroom_onlyshowroom", by_user)
        self.assertTrue(by_user["showroom_onlyshowroom"]["showroom_only"])

    def test_rejects_invalid_and_duplicate_entries(self):
        rows, problems = seed_showroom.build_rows([
            {"room_id": "", "display_name": "TanpaId"},
            {"room_id": "abc", "display_name": "IdSalah"},
            {"room_id": "111111", "display_name": "Lulu", "idn_username": "jkt48_lulu"},
            {"room_id": "111111", "display_name": "Kembar", "idn_username": "jkt48_kembar"},
            {"room_id": "222222", "display_name": "Ganda", "idn_username": "jkt48_lulu"},
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "jkt48_lulu")
        self.assertEqual(len(problems), 4)


class TestSeed(ShowroomBase):
    def test_dry_run_writes_nothing(self):
        stats = seed_showroom.seed(dry_run=True)
        self.assertEqual(stats["written"], 0)
        self.assertEqual(database.get_all_member_hls(include_disabled=True), [])

    def test_seed_writes_all_member_rooms_and_skips_official(self):
        stats = seed_showroom.seed()
        self.assertEqual(stats["written"], 3)
        self.assertEqual(stats["official_skipped"], 1)
        self.assertEqual(self._member("jkt48_lulu")["showroom_room_id"], "111111")
        self.assertEqual(self._member("jkt48_sona")["showroom_room_id"], "222222")
        self.assertIsNone(database.get_member_hls("999999"))

    def test_seed_is_idempotent(self):
        seed_showroom.seed()
        first = database.get_all_member_hls(include_disabled=True)
        seed_showroom.seed()
        second = database.get_all_member_hls(include_disabled=True)
        self.assertEqual(len(first), len(second))
        self.assertEqual(sorted(m["username"] for m in first),
                         sorted(m["username"] for m in second))

    def test_seed_preserves_stopped_member(self):
        database.set_member_showroom("jkt48_lulu", None, "Lulu", "Lulu")
        database.set_member_enabled("jkt48_lulu", False)

        seed_showroom.seed()

        member = self._member("jkt48_lulu")
        self.assertEqual(member["enabled"], 0)
        self.assertEqual(member["showroom_room_id"], "111111")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            seed_showroom.load_rooms(str(self.rooms_file.parent / "tidak-ada.json"))


if __name__ == "__main__":
    unittest.main()