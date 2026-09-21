"""
test_jkt48_members.py - Tes daftar member resmi JKT48 (jkt48.com/api/v1).

Yang diuji:
1. Parser ringkasan & detail: field `tiktok_account`, foto dijadikan URL absolut,
   hanya field yang dipakai yang disimpan, tahan terhadap data rusak.
2. `merge_members`: ringkasan + detail digabung, detail menang, urut ID.
3. Cache JSON: putar-balik `save_cache`/`load_roster` + toleransi berkas rusak.
4. `handle_index` (sumber otoritatif pemetaan akun TikTok → member) + duplikat.
5. Lapisan HTTP (`fetch_json` / `fetch_summary` / `fetch_details` / `fetch_roster`)
   diuji TANPA jaringan dengan memalsukan `http_request`.
6. CLI: `--print` membaca cache, `--update` menulis cache, gagal → exit 1.

Semua tes offline; tidak ada request sungguhan ke jkt48.com.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import jkt48_members
from bot.config import Config
from bot.jkt48_members import (
    KEPT_FIELDS,
    LIST_URL,
    RosterError,
    handle_index,
    load_roster,
    member_for_tiktok,
    merge_members,
    nickname_variants,
    parse_member_detail,
    parse_member_list,
    photo_url,
    save_cache,
    summarize,
    text,
    tiktok_handle,
)

SUMMARY_PAYLOAD = {
    "status": True,
    "message": "Berhasil mendapatkan data",
    "data": [
        {
            "type": "PASSION", "code": "ABIGAIL_RACHEL", "name": "Abigail Rachel",
            "nickname": "Aralie", "photo": "media/jkt48-member/abigail_rachel.jpg",
            "jkt48_member_id": 1,
        },
        {
            "type": "TRAINEE", "code": "NUR_INTAN", "name": "Nur Intan",
            "nickname": "Intan",
            "photo": "https://jkt48.com/api/v1/storages/media/jkt48-member/Nur_Intan.jpg",
            "jkt48_member_id": 182,
        },
    ],
}

PHOTO_ABSOLUTE = (
    "https://jkt48.com/api/v1/storages/media/jkt48-member/abigail_rachel.jpg"
)


def detail_payload(member_id: int = 1, tiktok: str = "@JKT48.aralie", **overrides) -> dict:
    """Jawaban `GET /api/v1/members/<id>` (bentuk nyata, 21 Sep 2026)."""
    data = {
        "jkt48_member_id": member_id,
        "code": "ABIGAIL_RACHEL",
        "name": "Abigail Rachel",
        "nickname": "Aralie",
        "type": "PASSION",
        "photo": "media/jkt48-member/abigail_rachel.jpg",
        "photo_1": PHOTO_ABSOLUTE,
        "birth_date": "2008-08-05T17:00:00.000Z",
        "blood_type": "B",
        "tiktok_account": tiktok,
        "instagram_account": "jkt48.aralie",
        "twitter_account": "Aralie_JKT48",
    }
    data.update(overrides)
    return {"status": True, "message": "Berhasil mendapatkan data", "data": data}


class TestFieldHelpers(unittest.TestCase):
    def test_text_normalises_values(self):
        self.assertEqual(text(None), "")
        self.assertEqual(text("  a  "), "a")
        self.assertEqual(text(12), "12")

    def test_tiktok_handle_is_canonical(self):
        self.assertEqual(tiktok_handle("@JKT48.Aralie"), "jkt48.aralie")
        self.assertEqual(tiktok_handle(" jkt48.aralie "), "jkt48.aralie")
        self.assertEqual(tiktok_handle(None), "")

    def test_photo_url_is_made_absolute(self):
        self.assertEqual(
            photo_url("media/x.jpg"),
            "https://jkt48.com/api/v1/storages/media/x.jpg",
        )
        self.assertEqual(
            photo_url("/media/x.jpg"),
            "https://jkt48.com/api/v1/storages/media/x.jpg",
        )
        self.assertEqual(photo_url("https://cdn/x.jpg"), "https://cdn/x.jpg")
        self.assertEqual(photo_url(""), "")
        self.assertEqual(photo_url(None), "")


class TestParsers(unittest.TestCase):
    def test_list_parses_rows_and_absolutises_photo(self):
        members = parse_member_list(SUMMARY_PAYLOAD)
        self.assertEqual([m["jkt48_member_id"] for m in members], [1, 182])
        self.assertEqual(members[0]["nickname"], "Aralie")
        self.assertEqual(members[0]["photo"], PHOTO_ABSOLUTE)
        self.assertEqual(
            members[1]["photo"],
            "https://jkt48.com/api/v1/storages/media/jkt48-member/Nur_Intan.jpg",
        )

    def test_list_keeps_only_fields_from_the_summary_endpoint(self):
        # Endpoint daftar tidak punya akun sosial; detailnya diambil per member.
        members = parse_member_list(SUMMARY_PAYLOAD)
        expected = set(KEPT_FIELDS) - {
            "tiktok_account", "instagram_account", "twitter_account",
        }
        self.assertEqual(set(members[0]), expected)
        self.assertNotIn("tiktok_account", members[0])

    def test_list_tolerates_broken_rows(self):
        payload = {
            "data": [
                "bukan objek",
                {"jkt48_member_id": "1"},          # id bukan int → dibuang
                {"jkt48_member_id": None},
                {"jkt48_member_id": 5, "name": "Ok"},
            ]
        }
        self.assertEqual(
            [m["jkt48_member_id"] for m in parse_member_list(payload)], [5]
        )

    def test_list_of_wrong_shape_is_empty(self):
        for payload in (None, {}, {"data": {}}, {"data": None}, {"data": "x"}):
            with self.subTest(payload=payload):
                self.assertEqual(parse_member_list(payload), [])

    def test_detail_keeps_only_whitelisted_fields(self):
        member = parse_member_detail(detail_payload())
        self.assertEqual(set(member), set(KEPT_FIELDS))
        # Data pribadi yang tidak dipakai fitur apa pun tidak disimpan.
        self.assertNotIn("birth_date", member)
        self.assertNotIn("blood_type", member)

    def test_detail_canonicalises_tiktok_handle(self):
        self.assertEqual(
            parse_member_detail(detail_payload())["tiktok_account"], "jkt48.aralie"
        )
        empty = detail_payload(tiktok="")
        self.assertEqual(parse_member_detail(empty)["tiktok_account"], "")

    def test_detail_prefers_photo_1_and_falls_back_to_photo(self):
        self.assertEqual(parse_member_detail(detail_payload())["photo"], PHOTO_ABSOLUTE)
        fallback = parse_member_detail(detail_payload(photo_1=""))
        self.assertEqual(fallback["photo"], PHOTO_ABSOLUTE)

    def test_detail_of_empty_payload_is_empty(self):
        for payload in (None, {}, {"data": []}, {"data": None}, {"data": "x"}):
            with self.subTest(payload=payload):
                self.assertEqual(parse_member_detail(payload), {})


class TestMerge(unittest.TestCase):
    def test_summary_only_member_survives_without_tiktok(self):
        merged = merge_members(parse_member_list(SUMMARY_PAYLOAD), [])
        self.assertEqual([m["jkt48_member_id"] for m in merged], [1, 182])
        self.assertEqual(set(merged[0]), set(KEPT_FIELDS))
        self.assertEqual(merged[0]["tiktok_account"], "")

    def test_detail_wins_but_does_not_erase_summary(self):
        summary = parse_member_list({
            "data": [{
                "jkt48_member_id": 1, "nickname": "Aralie", "name": "",
                "photo": "", "code": "ABIGAIL_RACHEL",
            }]
        })
        details = [parse_member_detail(detail_payload(nickname="", name="Abigail Rachel"))]
        merged = merge_members(summary, details)

        self.assertEqual(merged[0]["name"], "Abigail Rachel")      # diisi detail
        self.assertEqual(merged[0]["nickname"], "Aralie")          # tidak dihapus
        self.assertEqual(merged[0]["tiktok_account"], "jkt48.aralie")
        self.assertEqual(merged[0]["photo"], PHOTO_ABSOLUTE)

    def test_result_is_sorted_and_fills_every_field(self):
        details = [parse_member_detail(detail_payload(member_id=182, tiktok="nurintanjkt48"))]
        merged = merge_members(parse_member_list(SUMMARY_PAYLOAD), details)
        self.assertEqual([m["jkt48_member_id"] for m in merged], [1, 182])
        self.assertEqual(merged[1]["tiktok_account"], "nurintanjkt48")
        for member in merged:
            self.assertEqual(set(member), set(KEPT_FIELDS))


class TestCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "jkt48_members.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _members(self) -> list[dict]:
        return merge_members(
            parse_member_list(SUMMARY_PAYLOAD),
            [parse_member_detail(detail_payload())],
        )

    def test_round_trip(self):
        members = self._members()
        self.assertEqual(save_cache(members, self.path), self.path)

        payload = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], len(members))
        self.assertEqual(payload["source"], LIST_URL)
        self.assertTrue(payload["updated_at"])
        self.assertEqual(load_roster(self.path), members)

    def test_missing_file_is_empty(self):
        self.assertEqual(load_roster(self.path), [])

    def test_broken_file_is_empty(self):
        Path(self.path).write_text("{bukan json", encoding="utf-8")
        self.assertEqual(load_roster(self.path), [])

    def test_payload_without_members_is_empty(self):
        for body in ({"note": "x"}, {"members": "x"}, {"members": None}, []):
            Path(self.path).write_text(json.dumps(body), encoding="utf-8")
            with self.subTest(body=body):
                self.assertEqual(load_roster(self.path), [])

    def test_loading_fills_fields_and_canonicalises_handle(self):
        Path(self.path).write_text(json.dumps({"members": [
            {"jkt48_member_id": 1, "nickname": "Aralie", "tiktok_account": "@JKT48.Aralie"},
            "bukan objek",
        ]}), encoding="utf-8")
        roster = load_roster(self.path)
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["tiktok_account"], "jkt48.aralie")
        self.assertEqual(roster[0]["instagram_account"], "")
        self.assertEqual(set(roster[0]), set(KEPT_FIELDS))


class TestHandleIndex(unittest.TestCase):
    def _roster(self) -> list[dict]:
        return merge_members(parse_member_list(SUMMARY_PAYLOAD), [
            parse_member_detail(detail_payload()),
            parse_member_detail(detail_payload(member_id=182, tiktok="nurintanjkt48")),
        ])

    def test_maps_handle_to_member(self):
        index = handle_index(self._roster())
        self.assertEqual(index["jkt48.aralie"]["nickname"], "Aralie")
        self.assertEqual(index["nurintanjkt48"]["jkt48_member_id"], 182)

    def test_lookup_accepts_any_handle_spelling(self):
        roster = self._roster()
        for handle in ("jkt48.aralie", "@JKT48.Aralie", " JKT48.aralie "):
            with self.subTest(handle=handle):
                self.assertEqual(member_for_tiktok(roster, handle)["jkt48_member_id"], 1)
        self.assertIsNone(member_for_tiktok(roster, "tidak-ada"))

    def test_members_without_tiktok_are_skipped(self):
        self.assertEqual(handle_index(merge_members(parse_member_list(SUMMARY_PAYLOAD), [])), {})

    def test_duplicate_handle_keeps_first(self):
        roster = [
            {"jkt48_member_id": 1, "nickname": "Pertama", "tiktok_account": "sama"},
            {"jkt48_member_id": 2, "nickname": "Kedua", "tiktok_account": "@Sama"},
        ]
        index = handle_index(roster)
        self.assertEqual(list(index), ["sama"])
        self.assertEqual(index["sama"]["nickname"], "Pertama")

    def test_nickname_variants_use_nickname_name_and_code(self):
        variants = nickname_variants(
            {"nickname": "Aralie", "name": "Abigail Rachel", "code": "ABIGAIL_RACHEL"}
        )
        self.assertEqual(variants, {"aralie", "abigail", "rachel"})
        self.assertEqual(nickname_variants({}), set())

    def test_summarize_counts_handles(self):
        stats = summarize(self._roster())
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["with_tiktok"], 2)
        self.assertEqual(stats["handles"], ["jkt48.aralie", "nurintanjkt48"])


class TestHttpLayer(unittest.TestCase):
    """`http_request` dipalsukan, jadi tidak ada request sungguhan."""

    def setUp(self):
        self.calls: list[str] = []

    def _fake_http(self, handler):
        async def fake(method: str, url: str, **kwargs):
            self.calls.append(url)
            return handler(url)

        return mock.patch.object(jkt48_members, "http_request", new=fake)

    def test_api_headers_target_the_json_api(self):
        headers = jkt48_members.api_headers()
        self.assertIn("application/json", headers["Accept"])
        self.assertEqual(headers["Referer"], "https://jkt48.com/")
        self.assertEqual(headers["Origin"], "https://jkt48.com")
        self.assertTrue(headers["User-Agent"])

    def test_fetch_json_parses_body(self):
        with self._fake_http(lambda url: (200, json.dumps({"a": 1}))):
            self.assertEqual(
                asyncio.run(jkt48_members.fetch_json(LIST_URL)), (200, {"a": 1})
            )

    def test_fetch_json_rejects_errors_and_garbage(self):
        replies = [
            ((403, ""), (403, None)),      # Cloudflare
            ((200, ""), (200, None)),      # body kosong
            ((200, "<html>"), (200, None)),  # bukan JSON
        ]
        for reply, expected in replies:
            with self.subTest(reply=reply):
                with self._fake_http(lambda url, r=reply: r):
                    self.assertEqual(asyncio.run(jkt48_members.fetch_json(LIST_URL)), expected)

    def test_fetch_summary_raises_on_blocked_or_empty(self):
        with self._fake_http(lambda url: (403, "")):
            with self.assertRaises(RosterError):
                asyncio.run(jkt48_members.fetch_summary())
        with self._fake_http(lambda url: (200, json.dumps({"status": True, "data": []}))):
            with self.assertRaises(RosterError):
                asyncio.run(jkt48_members.fetch_summary())

    def test_fetch_summary_returns_members(self):
        with self._fake_http(lambda url: (200, json.dumps(SUMMARY_PAYLOAD))):
            members = asyncio.run(jkt48_members.fetch_summary())
        self.assertEqual([m["jkt48_member_id"] for m in members], [1, 182])

    def test_fetch_details_skips_failing_members(self):
        def handler(url: str):
            if url.endswith("/182"):
                return (403, "")
            if url.endswith("/7"):
                return (200, "bukan json")
            return (200, json.dumps(detail_payload()))

        with self._fake_http(handler):
            details = asyncio.run(jkt48_members.fetch_details([1, 182, 7], interval=0))

        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(details), 1)
        # ID diambil dari yang diminta, bukan dari isi jawaban.
        self.assertEqual(details[0]["jkt48_member_id"], 1)

    def test_fetch_roster_merges_list_and_details(self):
        def handler(url: str):
            if url.rstrip("/") == LIST_URL.rstrip("/"):
                return (200, json.dumps(SUMMARY_PAYLOAD))
            member_id = int(url.rsplit("/", 1)[-1])
            return (200, json.dumps(
                detail_payload(member_id=member_id, tiktok=f"user{member_id}")
            ))

        with self._fake_http(handler):
            roster = asyncio.run(jkt48_members.fetch_roster(interval=0))

        self.assertEqual([m["jkt48_member_id"] for m in roster], [1, 182])
        self.assertEqual([m["tiktok_account"] for m in roster], ["user1", "user182"])
        self.assertEqual(len(self.calls), 3)  # 1 daftar + 2 detail


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "jkt48_members.json")
        save_cache(merge_members(parse_member_list(SUMMARY_PAYLOAD), []), self.path)
        self._old_file = Config.JKT48_MEMBERS_FILE
        Config.JKT48_MEMBERS_FILE = self.path

    def tearDown(self):
        Config.JKT48_MEMBERS_FILE = self._old_file
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> None:
        with mock.patch.object(sys, "argv", ["jkt48_members", *argv]):
            jkt48_members.main()

    def test_print_reads_cache_without_network(self):
        def boom(*args, **kwargs):
            raise AssertionError("--print tidak boleh memanggil API")

        with mock.patch.object(jkt48_members, "fetch_roster", new=boom):
            self._run(["--print", "--file", self.path])

    def test_update_writes_cache(self):
        target = str(Path(self._tmp.name) / "baru.json")

        async def fake_roster(interval: float = 0.35):
            return merge_members(parse_member_list(SUMMARY_PAYLOAD), [])

        with mock.patch.object(jkt48_members, "fetch_roster", new=fake_roster):
            self._run(["--update", "--file", target])

        self.assertTrue(Path(target).exists())
        self.assertEqual(len(load_roster(target)), 2)

    def test_update_failure_exits_nonzero(self):
        async def failing(interval: float = 0.35):
            raise RosterError("GET daftar member menjawab HTTP 403")

        with mock.patch.object(jkt48_members, "fetch_roster", new=failing):
            with self.assertRaises(SystemExit) as ctx:
                self._run(["--update", "--file", self.path])
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()