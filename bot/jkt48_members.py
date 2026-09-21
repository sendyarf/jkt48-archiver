"""
jkt48_members.py - Daftar member resmi JKT48 dari API publik jkt48.com.

Kenapa modul ini ada:
- `tiktok_accounts.json` memuat 51 akun TikTok member, tetapi sebagian username
  TikTok TIDAK sama dengan username IDN di `member_hls`
  (mis. `jkt48.aurellia_` ↔ `jkt48_lia`, `kathrinjkt48` ↔ `jkt48_kathrina`).
- API resmi menyediakan field `tiktok_account` per member → sumber OTORITATIF
  untuk memetakan akun TikTok ke member (dipakai `bot/seed_tiktok.py`) dan foto
  member (dipakai kolom `avatar_url` halaman /tiktok).

Endpoint (uji 21 Sep 2026):
    GET https://jkt48.com/api/v1/members/       → ringkasan semua member
    GET https://jkt48.com/api/v1/members/<id>   → detail (ada `tiktok_account`)

Catatan Cloudflare: request biasa dijawab **403 "Just a moment…"**, tetapi lolos
dengan `curl_cffi` + impersonate browser (200 `application/json`) — sama seperti
kasus tikwm/TikTok di proyek ini. Karena itu modul ini memakai curl_cffi lebih
dulu; httpx hanya cadangan (dan biasanya akan 403).

Hasilnya disimpan sebagai cache JSON (default `jkt48_members.json`) supaya seed
tidak perlu memanggil API 58× setiap kali. Jalankan di server:
    python3 -m bot.jkt48_members --update     # ambil dari API lalu tulis cache
    python3 -m bot.jkt48_members --print      # lihat isi cache
"""
import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bot.config import Config

# Memakai ulang lapisan HTTP `bot/tiktok_client`: request biasa ke jkt48.com
# dijawab Cloudflare 403, sedangkan curl_cffi + impersonate Chrome balas 200
# (diuji 21 Sep 2026). Tidak ada logika TLS kedua di sini — satu sumber saja.
# `curl_cffi` di tiktok_client bersifat opsional, jadi impor ini tetap aman di
# mesin yang belum memasangnya.
from bot.tiktok_client import RateLimiter, browser_headers, http_request

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

LIST_URL = "https://jkt48.com/api/v1/members"
MEMBER_URL = "https://jkt48.com/api/v1/members/{member_id}"
STORAGE_BASE = "https://jkt48.com/api/v1/storages/"
DEFAULT_IMPERSONATE = "chrome131"

# Field yang disimpan di cache. Sengaja dipilih saja (tidak menyimpan data
# pribadi seperti tanggal lahir/golongan darah yang tidak dipakai fitur apa pun).
KEPT_FIELDS = (
    "jkt48_member_id", "code", "name", "nickname", "type",
    "tiktok_account", "instagram_account", "twitter_account", "photo",
)


def text(value: Any) -> str:
    """String bersih dari nilai apa pun (None → '')."""
    return str(value).strip() if value is not None else ""


def tiktok_handle(value: Any) -> str:
    """Bentuk kanonik username TikTok: tanpa '@', huruf kecil."""
    return text(value).lstrip("@").lower()


def photo_url(value: Any) -> str:
    """Ubah nilai `photo` (absolut atau relatif) menjadi URL yang bisa dipakai web."""
    raw = text(value)
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    return STORAGE_BASE + raw.lstrip("/")


def parse_member_list(payload: Any) -> list[dict]:
    """
    Ringkasan member dari `GET /api/v1/members/`.

    Tidak ada `tiktok_account` di endpoint ini (dicek 21 Sep 2026), jadi hanya
    dipakai untuk tahu daftar ID yang perlu diambil detailnya.
    """
    data = (payload or {}).get("data")
    if not isinstance(data, list):
        return []
    members: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        member_id = item.get("jkt48_member_id")
        if not isinstance(member_id, int):
            continue
        members.append({
            "jkt48_member_id": member_id,
            "code": text(item.get("code")),
            "name": text(item.get("name")),
            "nickname": text(item.get("nickname")),
            "type": text(item.get("type")),
            "photo": photo_url(item.get("photo")),
        })
    return members


def parse_member_detail(payload: Any) -> dict:
    """Detail satu member. Return {} bila `data` tidak ada/bukan objek."""
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return {}
    member = {
        "jkt48_member_id": data.get("jkt48_member_id"),
        "code": text(data.get("code")),
        "name": text(data.get("name")),
        "nickname": text(data.get("nickname")),
        "type": text(data.get("type")),
        "tiktok_account": tiktok_handle(data.get("tiktok_account")),
        "instagram_account": text(data.get("instagram_account")),
        "twitter_account": text(data.get("twitter_account")),
        "photo": photo_url(data.get("photo_1") or data.get("photo")),
    }
    return {k: v for k, v in member.items() if k in KEPT_FIELDS}


def merge_members(summary: list[dict], details: list[dict]) -> list[dict]:
    """
    Gabungkan ringkasan + detail, urut `jkt48_member_id`.

    Detail menang untuk field yang diisinya; ringkasan dipakai sebagai fallback
    (mis. bila detail gagal diambil, member tetap ada dengan nama + foto).
    """
    merged: dict[int, dict] = {}
    for item in summary:
        merged[item["jkt48_member_id"]] = dict(item)
    for item in details:
        member_id = item.get("jkt48_member_id")
        if not isinstance(member_id, int):
            continue
        base = merged.get(member_id, {"jkt48_member_id": member_id})
        base.update({k: v for k, v in item.items() if v or k not in base})
        merged[member_id] = base
    result = []
    for member_id in sorted(merged):
        result.append({k: merged[member_id].get(k, "") if k != "jkt48_member_id"
                       else member_id for k in KEPT_FIELDS})
    return result


# ─── Cache ─────────────────────────────────────────────────────────────────
def save_cache(members: list[dict], path: Optional[str] = None) -> str:
    """Tulis roster ke berkas JSON (dipakai seed tanpa memanggil API lagi)."""
    target = Path(path or Config.JKT48_MEMBERS_FILE)
    payload = {
        "note": "Daftar member resmi JKT48 (jkt48.com/api/v1). "
                "Dihasilkan oleh bot/jkt48_members.py.",
        "source": LIST_URL,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(members),
        "members": members,
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(target)


def load_roster(path: Optional[str] = None) -> list[dict]:
    """
    Baca roster dari cache. Return [] bila berkas tidak ada/rusak.

    Sengaja tidak melempar exception: seed harus tetap jalan (memakai
    pencocokan nama) walau cache belum pernah dibuat di server.
    """
    target = Path(path or Config.JKT48_MEMBERS_FILE)
    if not target.exists():
        logger.info("Cache roster belum ada (%s) — lewati data resmi.", target)
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("Cache roster %s tidak terbaca: %s", target, exc)
        return []
    members = data.get("members") if isinstance(data, dict) else data
    if not isinstance(members, list):
        logger.warning("Cache roster %s tidak berisi daftar 'members'.", target)
        return []
    cleaned: list[dict] = []
    for item in members:
        if not isinstance(item, dict):
            continue
        member_id = item.get("jkt48_member_id")
        row = {"jkt48_member_id": member_id if isinstance(member_id, int) else 0}
        for field in KEPT_FIELDS:
            if field == "jkt48_member_id":
                continue
            row[field] = text(item.get(field))
        row["tiktok_account"] = tiktok_handle(row.get("tiktok_account"))
        cleaned.append(row)
    return cleaned


def handle_index(members: list[dict]) -> dict:
    """
    Peta username TikTok → member, dari field `tiktok_account` resmi.

    Satu handle bisa diklaim dua member (mis. akun bersama) atau muncul dua kali
    di cache; yang pertama terpakai dan duplikat dilaporkan ke log.
    """
    index: dict = {}
    for member in members:
        handle = tiktok_handle(member.get("tiktok_account"))
        if not handle:
            continue
        if handle in index:
            logger.warning(
                "Handle TikTok %s diklaim %s dan %s — memakai yang pertama.",
                handle, index[handle].get("nickname"), member.get("nickname"),
            )
            continue
        index[handle] = member
    return index


def member_for_tiktok(members: list[dict], unique_id: str) -> Optional[dict]:
    """Member resmi pemilik sebuah username TikTok (None bila tidak ada)."""
    return handle_index(members).get(tiktok_handle(unique_id))


def nickname_variants(member: dict) -> set:
    """
    Nama-nama yang bisa dipakai mencocokkan member roster ke `member_hls`.

    `nickname` (mis. 'Lia', 'Kathrina') adalah kunci utama, ditambah `code` dan
    `name` sebagai cadangan saat nickname-nya beda dari username IDN.
    """
    variants: set = set()
    for field in ("nickname", "name", "code"):
        for word in re.split(r"[^A-Za-z]+", text(member.get(field))):
            if len(word) >= 2:
                variants.add(word.lower())
    return variants


# ─── CLI ───────────────────────────────────────────────────────────────────
def summarize(members: list[dict]) -> dict:
    """Statistik ringkas cache, untuk log CLI."""
    with_tiktok = [m for m in members if m.get("tiktok_account")]
    return {
        "total": len(members),
        "with_tiktok": len(with_tiktok),
        "handles": sorted(m["tiktok_account"] for m in with_tiktok),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daftar member resmi JKT48 (jkt48.com) untuk pemetaan akun TikTok."
    )
    parser.add_argument("--update", action="store_true",
                        help="ambil dari API jkt48.com lalu tulis cache JSON")
    parser.add_argument("--print", dest="show", action="store_true",
                        help="tampilkan isi cache")
    parser.add_argument("--file", default=Config.JKT48_MEMBERS_FILE,
                        help="berkas cache (default: %(default)s)")
    parser.add_argument("--interval", type=float, default=Config.JKT48_MEMBERS_INTERVAL_SECONDS,
                        help="jeda antar-request detail (default: %(default)s detik)")
    args = parser.parse_args()

    if args.update:
        try:
            members = asyncio.run(fetch_roster(interval=args.interval))
        except RosterError as exc:
            logger.error("Gagal mengambil roster: %s", exc)
            sys.exit(1)
        path = save_cache(members, args.file)
        stats = summarize(members)
        logger.info(
            "Roster tersimpan di %s: %d member, %d punya akun TikTok.",
            path, stats["total"], stats["with_tiktok"],
        )
    else:
        members = load_roster(args.file)

    if args.show or not args.update:
        stats = summarize(members)
        if not stats["total"]:
            logger.info("Cache kosong. Jalankan: python3 -m bot.jkt48_members --update")
            return
        for member in members:
            logger.info(
                "%-4s %-24s %-18s %s",
                member.get("jkt48_member_id") or "-",
                member.get("name") or "-",
                member.get("nickname") or "-",
                member.get("tiktok_account") or "-",
            )
        logger.info(
            "Total %d member, %d punya akun TikTok di API resmi.",
            stats["total"], stats["with_tiktok"],
        )


# ─── Lapisan HTTP ──────────────────────────────────────────────────────────
# `browser_headers` / `http_request` / `RateLimiter` diimpor di atas dari
# `bot/tiktok_client`: request biasa ke jkt48.com dijawab Cloudflare 403,
# sedangkan curl_cffi + impersonate Chrome balas 200 (diuji 21 Sep 2026).
# Tidak ada logika TLS kedua di sini — satu sumber saja.
class RosterError(RuntimeError):
    """Gagal mengambil daftar member dari API jkt48.com."""


def api_headers() -> dict:
    """Header khusus API jkt48.com (JSON + referer situsnya)."""
    headers = browser_headers()
    headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://jkt48.com/",
        "Origin": "https://jkt48.com",
    })
    return headers


async def fetch_json(url: str, timeout: float = 30.0) -> tuple[int, Any]:
    """GET JSON dari jkt48.com. Return `(status, payload|None)`."""
    status, body = await http_request(
        "GET", url, headers=api_headers(), timeout=timeout
    )
    if status != 200 or not body:
        return status, None
    try:
        return status, json.loads(body)
    except ValueError:
        logger.warning("Jawaban bukan JSON dari %s (%d byte)", url, len(body))
        return status, None


async def fetch_summary(timeout: float = 30.0) -> list[dict]:
    """Ringkasan semua member (`GET /api/v1/members/`)."""
    status, payload = await fetch_json(LIST_URL, timeout=timeout)
    if status != 200:
        raise RosterError(f"GET {LIST_URL} menjawab HTTP {status}")
    members = parse_member_list(payload)
    if not members:
        raise RosterError(f"GET {LIST_URL} tidak memuat daftar member")
    return members


async def fetch_details(
    member_ids: list[int], interval: float = 0.35, timeout: float = 30.0
) -> list[dict]:
    """
    Detail tiap member satu per satu (`tiktok_account` hanya ada di sini).

    Satu ID yang gagal tidak menggagalkan seluruh proses — member itu tetap
    ikut lewat data ringkasan (tanpa akun TikTok), dan dicatat di log.
    """
    limiter = RateLimiter(interval)
    details: list[dict] = []
    failed: list[int] = []
    for member_id in member_ids:
        await limiter.wait()
        status, payload = await fetch_json(
            MEMBER_URL.format(member_id=member_id), timeout=timeout
        )
        if status != 200:
            failed.append(member_id)
            logger.warning("Detail member %s: HTTP %s", member_id, status)
            continue
        member = parse_member_detail(payload)
        if member:
            member["jkt48_member_id"] = member_id
            details.append(member)
    if failed:
        logger.warning("%d member tanpa detail: %s", len(failed), failed)
    return details


async def fetch_roster(interval: float = 0.35) -> list[dict]:
    """Roster lengkap (ringkasan + detail) dari API resmi."""
    summary = await fetch_summary()
    details = await fetch_details(
        [m["jkt48_member_id"] for m in summary], interval=interval
    )
    return merge_members(summary, details)


if __name__ == "__main__":
    main()

