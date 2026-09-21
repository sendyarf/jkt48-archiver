"""
test_seed_tiktok.py - Tes pencocokan akun TikTok ↔ member JKT48.

Yang diuji:
1. `core_name()` menormalkan bentuk username TikTok yang berbeda dari username
   IDN (`jkt48.lyn.s`, `jkt48.ella.a`, `kathrinjkt48`, `jkt48.aurellia_`).
2. Lapis 1 tetap varian nama persis; lapis 2 (nama inti) dipakai bila lapis 1
   gagal, termasuk pencocokan awalan `kathrin` → `kathrina`.
3. Nama inti yang ambigu (>1 member) DILEWATI, bukan salah pasang.
4. Akun cadangan `u16` tidak pernah dipetakan ke satu member dan dilaporkan
   terpisah oleh `seed()`.
5. `seed()` menulis ke DB (dan hanya itu), idempoten, `--dry-run` tidak menulis
   apa pun, serta mengisi `member_username` yang masih NULL saat diulang —
   jalur yang dipakai di VPS setelah pencocokan diperbaiki.
"""
import json
import tempfile
import unittest
from pathlib import Path

from bot import database, seed_tiktok
from bot.config import Config

MEMBERS = [
    ("jkt48_indah", "Indah JKT48"),
    ("jkt48_lyn", "Lyn JKT48"),
    ("jkt48_ella", "Ella JKT48"),
    ("jkt48_raisha", "Raisha JKT48"),
    ("jkt48_kathrina", "jkt48_kathrina"),
    ("jkt48_lulu", "Lulu JKT48"),
]

ACCOUNTS = {
    "note": "fixture",
    "accounts": [
        {"unique_id": "indahjkt48"},
        {"unique_id": "jkt48.lyn.s"},
        {"unique_id": "jkt48.ella.a"},
        {"unique_id": "jkt48.raisha.s"},
        {"unique_id": "kathrinjkt48"},
        {"unique_id": "jkt48.aurellia_"},
        {"unique_id": "jkt48.u16"},
    ],
}


class TikTokSeedBase(unittest.TestCase):
    """DB + berkas sementara (akun & cache roster), dikembalikan di tearDown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._old_db = Config.DB_PATH
        self._old_roster = Config.JKT48_MEMBERS_FILE
        Config.DB_PATH = str(base / "test.db")
        # Isolasi cache roster: berkas ini tidak dibuat, jadi pencocokan nama
        # diuji apa adanya. Test yang butuh roster menulisnya sendiri.
        self.roster_file = base / "jkt48_members.json"
        Config.JKT48_MEMBERS_FILE = str(self.roster_file)
        self.accounts_file = base / "tiktok_accounts.json"
        self.accounts_file.write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        database.init_db()
        for username, display in MEMBERS:
            database.upsert_member_hls(username, display)

    def tearDown(self):
        Config.DB_PATH = self._old_db
        Config.JKT48_MEMBERS_FILE = self._old_roster
        self._tmp.cleanup()

    def _write_roster(self, members: list[dict]) -> None:
        """Tulis cache roster resmi (bentuk `jkt48_members.json`)."""
        self.roster_file.write_text(
            json.dumps({"count": len(members), "members": members}), encoding="utf-8"
        )

    def _indexes(self) -> tuple[dict, dict]:
        return seed_tiktok.build_member_index(), seed_tiktok.build_member_core_index()

    def _match(self, unique_id: str):
        index, core_index = self._indexes()
        return seed_tiktok.match_member(unique_id, index, core_index)

    def _account_row(self, unique_id: str) -> dict:
        return database.get_tiktok_account(unique_id) or {}


class TestCoreName(unittest.TestCase):
    def test_strips_jkt48_digits_and_single_letter_initial(self):
        expected = {
            "jkt48.lyn.s": "lyn",
            "jkt48.ella.a": "ella",
            "jkt48.raisha.s": "raisha",
            "kathrinjkt48": "kathrin",
            "jkt48.aurellia_": "aurellia",
            "jkt48_indah": "indah",
            "jkt48u16": "",
            "jkt48.u16": "",
            "": "",
        }
        for value, want in expected.items():
            with self.subTest(value=value):
                self.assertEqual(seed_tiktok.core_name(value), want)

    def test_keeps_multi_part_names_joined(self):
        self.assertEqual(seed_tiktok.core_name("jkt48.heidi__"), "heidi")
        self.assertEqual(seed_tiktok.core_name("giaa.jkt48"), "giaa")

    def test_backup_account_detection(self):
        self.assertTrue(seed_tiktok.is_backup_account("jkt48.u16"))
        self.assertTrue(seed_tiktok.is_backup_account("JKT48.U16"))
        self.assertFalse(seed_tiktok.is_backup_account("jkt48.lyn.s"))
        self.assertFalse(seed_tiktok.is_backup_account("indahjkt48"))



class TestMatching(TikTokSeedBase):
    def test_layer1_exact_variants_unchanged(self):
        self.assertEqual(self._match("indahjkt48")["username"], "jkt48_indah")
        self.assertEqual(self._match("lulu_jkt48")["username"], "jkt48_lulu")

    def test_layer2_core_name_fixes_handle_mismatch(self):
        expected = {
            "jkt48.lyn.s": "jkt48_lyn",
            "jkt48.ella.a": "jkt48_ella",
            "jkt48.raisha.s": "jkt48_raisha",
        }
        for unique_id, member_username in expected.items():
            with self.subTest(unique_id=unique_id):
                member = self._match(unique_id)
                self.assertIsNotNone(member)
                self.assertEqual(member["username"], member_username)

    def test_layer2_prefix_match(self):
        member = self._match("kathrinjkt48")
        self.assertIsNotNone(member)
        self.assertEqual(member["username"], "jkt48_kathrina")

    def test_unknown_name_stays_unmatched(self):
        self.assertIsNone(self._match("jkt48.aurellia_"))

    def test_core_index_requires_minimum_length(self):
        index = seed_tiktok.build_member_core_index()
        self.assertNotIn("", index)
        self.assertIn("lyn", index)

    def test_ambiguous_core_is_skipped(self):
        database.upsert_member_hls("raishajkt48", "Raisha Kedua")
        index, core_index = self._indexes()
        self.assertEqual(len(core_index["raisha"]), 2)
        self.assertIsNone(seed_tiktok.match_member("jkt48.raisha.s", index, core_index))

    def test_ambiguous_prefix_is_skipped(self):
        database.upsert_member_hls("jkt48_kathrinaa", "Kathrinaa JKT48")
        index, core_index = self._indexes()
        self.assertIsNone(seed_tiktok.match_member("kathrinjkt48", index, core_index))

    def test_backup_account_never_mapped(self):
        index, core_index = self._indexes()
        self.assertIsNone(seed_tiktok.match_member("jkt48.u16", index, core_index))

    def test_core_layer_optional_for_backward_compat(self):
        index, _ = self._indexes()
        self.assertEqual(
            seed_tiktok.match_member("indahjkt48", index)["username"], "jkt48_indah"
        )
        self.assertIsNone(seed_tiktok.match_member("jkt48.lyn.s", index))



class TestSeedEndToEnd(TikTokSeedBase):
    def _seed(self, dry_run: bool = False) -> tuple[list[str], list[str]]:
        accounts = seed_tiktok.load_accounts(str(self.accounts_file))
        return seed_tiktok.seed(accounts, dry_run=dry_run)

    def test_seed_writes_matches_and_reports_leftovers(self):
        unmatched, backups = self._seed()

        self.assertEqual(self._account_row("indahjkt48")["member_username"], "jkt48_indah")
        self.assertEqual(self._account_row("jkt48.lyn.s")["member_username"], "jkt48_lyn")
        self.assertEqual(self._account_row("kathrinjkt48")["member_username"], "jkt48_kathrina")
        self.assertEqual(self._account_row("jkt48.lyn.s")["display_name"], "Lyn JKT48")

        self.assertEqual(unmatched, ["jkt48.aurellia_", "jkt48.u16"])
        self.assertEqual(backups, ["jkt48.u16"])
        self.assertIsNone(self._account_row("jkt48.u16")["member_username"])

    def test_seed_is_idempotent(self):
        self._seed()
        before = database.get_tiktok_accounts(include_disabled=True)
        self._seed()
        self.assertEqual(before, database.get_tiktok_accounts(include_disabled=True))

    def test_rerun_fills_member_username_that_was_null(self):
        self._seed()
        self.assertIsNone(self._account_row("jkt48.aurellia_")["member_username"])

        database.upsert_member_hls("jkt48_aurellia", "Aurellia JKT48")
        unmatched, _ = self._seed()

        self.assertEqual(
            self._account_row("jkt48.aurellia_")["member_username"], "jkt48_aurellia"
        )
        self.assertEqual(unmatched, ["jkt48.u16"])

    def test_dry_run_writes_nothing(self):
        self._seed(dry_run=True)
        self.assertEqual(database.get_tiktok_accounts(include_disabled=True), [])

    def test_accounts_file_from_repo_is_readable(self):
        """Daftar akun asli harus terbaca dan berisi 51 akun."""
        real_file = Path(Config.TIKTOK_ACCOUNTS_FILE)
        if not real_file.exists():
            self.skipTest("tiktok_accounts.json tidak ada")
        accounts = seed_tiktok.load_accounts(str(real_file))
        self.assertEqual(len(accounts), 51)

    def test_enabled_flag_follows_json(self):
        accounts = [{"unique_id": "indahjkt48", "enabled": False}]
        seed_tiktok.seed(accounts)
        self.assertEqual(self._account_row("indahjkt48")["enabled"], 0)
        self.assertEqual(database.get_tiktok_accounts(), [])


def roster_entry(member_id: int, nickname: str, handle: str = "", **overrides) -> dict:
    """Satu member di cache roster resmi (bentuk `jkt48_members.json`)."""
    entry = {
        "jkt48_member_id": member_id,
        "code": nickname.upper(),
        "name": nickname,
        "nickname": nickname,
        "type": "LOVE",
        "tiktok_account": handle,
        "instagram_account": "",
        "twitter_account": "",
        "photo": f"https://jkt48.com/api/v1/storages/media/jkt48-member/{nickname.lower()}.jpg",
    }
    entry.update(overrides)
    return entry


class TestRosterIntegration(TikTokSeedBase):
    """Roster resmi jkt48.com = sumber OTORITATIF pemetaan akun TikTok → member."""

    def _seed_accounts(self, *unique_ids: str):
        accounts = [{"unique_id": uid} for uid in unique_ids]
        return seed_tiktok.seed(accounts)

    def test_roster_link_and_reverse_need_cache(self):
        self.assertEqual(seed_tiktok.build_roster_link(), {})
        self.assertEqual(seed_tiktok.build_roster_reverse(), {})

    def test_roster_maps_account_the_name_matcher_cannot(self):
        """`jkt48.aurellia_` ↔ `jkt48_lia`: username IDN sama sekali beda."""
        database.upsert_member_hls("jkt48_lia", "jkt48_lia")  # nama masih placeholder
        self._write_roster([
            roster_entry(31, "Lia", "jkt48.aurellia_", name="Aurellia"),
        ])

        self.assertIsNone(self._match("jkt48.aurellia_"))  # pencocokan nama gagal
        unmatched, _ = self._seed_accounts("jkt48.aurellia_")

        row = self._account_row("jkt48.aurellia_")
        self.assertEqual(row["member_username"], "jkt48_lia")
        self.assertEqual(unmatched, [])
        # Nama placeholder `member_hls` diganti nama resmi roster.
        self.assertEqual(row["display_name"], "Lia JKT48")
        self.assertEqual(
            row["avatar_url"],
            "https://jkt48.com/api/v1/storages/media/jkt48-member/lia.jpg",
        )

    def test_roster_wins_over_name_matching(self):
        self._write_roster([
            roster_entry(31, "Ella", "jkt48.lyn.s", name="Gabriela Abigail"),
        ])
        self.assertEqual(self._match("jkt48.lyn.s")["username"], "jkt48_lyn")

        self._seed_accounts("jkt48.lyn.s")

        self.assertEqual(self._account_row("jkt48.lyn.s")["member_username"], "jkt48_ella")

    def test_name_matching_used_when_roster_silent(self):
        self._write_roster([roster_entry(116, "Indah", "indahjkt48")])

        self._seed_accounts("jkt48.lyn.s")

        row = self._account_row("jkt48.lyn.s")
        self.assertEqual(row["member_username"], "jkt48_lyn")
        self.assertIsNone(row["avatar_url"])

    def test_reverse_match_enriches_account_not_reported_by_api(self):
        """Akun seperti `jkt48.heidi__` tidak ada di API, tapi membernya jelas."""
        database.upsert_member_hls("jkt48_heidi", "Heidi JKT48")
        self._write_roster([roster_entry(111, "Heidi", "")])  # tiktok_account kosong

        self.assertNotIn("jkt48.heidi__", seed_tiktok.build_roster_link())
        self._seed_accounts("jkt48.heidi__")

        row = self._account_row("jkt48.heidi__")
        self.assertEqual(row["member_username"], "jkt48_heidi")
        self.assertEqual(row["display_name"], "Heidi JKT48")  # nama manusia menang
        self.assertTrue(row["avatar_url"].endswith("/heidi.jpg"))

    def test_avatar_and_name_are_not_overwritten_on_rerun(self):
        self._write_roster([roster_entry(116, "Indah", "indahjkt48")])
        self._seed_accounts("indahjkt48")
        with database._get_conn() as conn:
            conn.execute(
                "UPDATE tiktok_accounts SET avatar_url = 'https://contoh/manual.jpg' "
                "WHERE unique_id = 'indahjkt48'"
            )

        self._seed_accounts("indahjkt48")

        row = self._account_row("indahjkt48")
        self.assertEqual(row["avatar_url"], "https://contoh/manual.jpg")
        self.assertEqual(row["display_name"], "Indah JKT48")

    def test_manual_member_username_in_json_always_wins(self):
        self._write_roster([roster_entry(31, "Ella", "jkt48.lyn.s")])

        seed_tiktok.seed([
            {"unique_id": "jkt48.lyn.s", "member_username": "jkt48_lulu"},
        ])

        self.assertEqual(self._account_row("jkt48.lyn.s")["member_username"], "jkt48_lulu")

    def test_backup_account_stays_unmapped_with_roster(self):
        self._write_roster([roster_entry(116, "Indah", "indahjkt48")])

        unmatched, backups = self._seed_accounts("jkt48.u16")

        self.assertEqual((unmatched, backups), (["jkt48.u16"], ["jkt48.u16"]))
        self.assertIsNone(self._account_row("jkt48.u16")["member_username"])


if __name__ == "__main__":
    unittest.main()
