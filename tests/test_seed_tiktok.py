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
    """DB sementara + file daftar akun, keduanya dikembalikan di tearDown."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._old_db = Config.DB_PATH
        Config.DB_PATH = str(base / "test.db")
        self.accounts_file = base / "tiktok_accounts.json"
        self.accounts_file.write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        database.init_db()
        for username, display in MEMBERS:
            database.upsert_member_hls(username, display)

    def tearDown(self):
        Config.DB_PATH = self._old_db
        self._tmp.cleanup()

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


if __name__ == "__main__":
    unittest.main()
