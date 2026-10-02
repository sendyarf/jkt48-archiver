"""
test_public_id.py - UUID publik untuk URL `/watch/<uuid>` dan deep-link Telegram.

Keluhan pemilik (2 Okt 2026): link seperti
`https://t.me/replay_48_bot?start=merged_177` dan
`https://jkt48.vidx.download/watch/merged_177` terlihat mentah karena
memwbocorkan identitas internal (grup merge / ID sesi). Sekarang keduanya
memakai UUID, sementara tautan lama harus tetap membuka rekaman yang benar.
"""
import asyncio
import re
import tempfile
import unittest
import uuid

from bot import database
from bot.config import Config
from bot.replay_bot import _valid_payload

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class PublicIdTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = Config.DB_PATH
        Config.DB_PATH = f"{self._tmp.name}/test.db"
        database.init_db()

    def tearDown(self):
        Config.DB_PATH = self._old_db
        self._tmp.cleanup()


class TestPublicIdGeneration(PublicIdTestCase):
    def test_sesi_baru_langsung_punya_uuid(self):
        database.insert_live(
            live_id="jkt48_daisy_123",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-10-02T10:00:00+00:00",
        )
        pid = database.get_public_id("jkt48_daisy_123")
        self.assertIsNotNone(pid)
        self.assertRegex(pid, UUID_RE, "public_id harus berupa UUID")
        self.assertNotIn("jkt48_daisy", pid)

    def test_insert_ulang_tidak_mengganti_uuid(self):
        """Reconnect/re-detect LiveSession yang sama tidak boleh mengubah link."""
        for _ in range(3):
            database.insert_live(
                live_id="jkt48_daisy_123",
                member_username="jkt48_daisy",
                member_name="Daisy",
                started_at="2026-10-02T10:00:00+00:00",
            )
        pid = database.get_public_id("jkt48_daisy_123")
        self.assertIsNotNone(pid)
        database.insert_live(
            live_id="jkt48_daisy_123",
            member_username="jkt48_daisy",
            member_name="Daisy",
            started_at="2026-10-02T10:00:00+00:00",
        )
        self.assertEqual(
            database.get_public_id("jkt48_daisy_123"), pid,
            "link yang sudah dibagikan harus tetap berlaku",
        )

    def test_grup_merge_bagikan_satu_uuid(self):
        """Semua segmen satu rekaman = satu konten = satu UUID.

        Kalau tiap segmen dapat UUID sendiri, satu rekaman multi-segmen akan
        muncul sebagai beberapa URL berbeda.
        """
        for seg in (1, 2, 3):
            database.insert_live(
                live_id=f"merged_177_r{seg}",
                member_username="jkt48_nachia",
                member_name="Nachia",
                started_at="2026-10-02T10:00:00+00:00",
            )
        gid = database.create_merge_group(
            "jkt48_nachia", "Nachia", "2026-10-02T10:00:00+00:00", platform="idn"
        )
        for seg in (1, 2, 3):
            database.set_session_merge_group(f"merged_177_r{seg}", gid)
        database.close_merge_group(gid, "/tmp/merged.mp4", "merged_177")
        database.set_session_fields(group_id=gid, content_uid="merged_177")

        shared = database.new_public_id()
        database.set_session_fields(group_id=gid, public_id=shared)

        ids = {database.get_public_id(f"merged_177_r{s}") for s in (1, 2, 3)}
        self.assertEqual(
            ids, {shared},
            "tiga segmen satu rekaman harus punya UUID yang sama",
        )

    def test_lookup_berdasarkan_uuid(self):
        # live_id riil (bukan `merged_*`): `set_session_fields` sengaja
        # mengabaikan id berawalan `merged_` karena itu id grup sintetis.
        database.insert_live(
            live_id="sr_JKT48_Nachia_1790838576",
            member_username="jkt48_nachia",
            member_name="Nachia",
            started_at="2026-10-02T10:00:00+00:00",
        )
        pid = database.get_public_id("sr_JKT48_Nachia_1790838576")
        database.set_session_fields(
            live_id="sr_JKT48_Nachia_1790838576", telegram_message_ids="[969]"
        )
        found = database.get_archived_session_by_public_id(pid)
        self.assertIsNotNone(found, "arsip harus ditemukan lewat UUID")
        self.assertEqual(found["live_id"], "sr_JKT48_Nachia_1790838576")

    def test_uuid_tidak_ada_untuk_konten_bukan_arsip(self):
        database.insert_live(
            live_id="merged_178",
            member_username="jkt48_x",
            member_name="X",
            started_at="2026-10-02T10:00:00+00:00",
        )
        self.assertIsNone(database.get_archived_session_by_public_id(str(uuid.uuid4())))


class TestReplayBotAcceptsUuid(PublicIdTestCase):
    def test_uuid_diterima_sebagai_payload(self):
        self.assertTrue(_valid_payload(str(uuid.uuid4())))

    def test_payload_lama_tetap_diterima(self):
        for payload in (
            "merged_177",
            "sr_JKT48_Nachia_1790838576",
            "jkt48_daisy_1790844069",
            "dQw4w9WgXcQ",  # YouTube ID
            "notify_dQw4w9WgXcQ",
            "tt_7691614629851614472",
        ):
            with self.subTest(payload=payload):
                self.assertTrue(
                    _valid_payload(payload),
                    "tautan lama tidak boleh rusak setelah migrasi ke UUID",
                )

    def test_payload_asing_ditolak(self):
        self.assertFalse(_valid_payload("../../../etc/passwd"))
        self.assertFalse(_valid_payload(""))
        self.assertFalse(_valid_payload("bukan-uuid-sama-sekali"))


class TestBackfillMigration(PublicIdTestCase):
    def test_baris_lama_dapat_uuid_satu_per_konten(self):
        """Migrasi harus mengisi UUID untuk baris yang belum punya."""
        import sqlite3

        conn = sqlite3.connect(Config.DB_PATH)
        # Kosongkan public_id seperti database lama yang belum punya kolom ini
        # terisi (DROP COLUMN tidak bisa dipakai karena ada index).
        conn.execute("UPDATE live_sessions SET public_id = ''")
        conn.execute(
            "INSERT INTO live_sessions (live_id, member_username, content_uid, public_id) "
            "VALUES ('seg_a', 'jkt48_nachia', 'merged_177', '')"
        )
        conn.execute(
            "INSERT INTO live_sessions (live_id, member_username, content_uid, public_id) "
            "VALUES ('seg_b', 'jkt48_nachia', 'merged_177', '')"
        )
        conn.execute(
            "INSERT INTO live_sessions (live_id, member_username, content_uid, public_id) "
            "VALUES ('seg_c', 'jkt48_nachia', 'merged_177', '')"
        )
        conn.commit()
        conn.close()

        database.init_db()

        a = database.get_public_id("seg_a")
        b = database.get_public_id("seg_b")
        c = database.get_public_id("seg_c")
        self.assertRegex(a, UUID_RE)
        self.assertEqual({a, b, c}, {a}, "satu konten harus mendapat satu UUID")


if __name__ == "__main__":
    unittest.main()