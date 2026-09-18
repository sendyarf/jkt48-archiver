"""
idn_lookup.py - Best-effort lookup live aktif seorang member di IDN (OPSIONAL).

Modul ini SENGAJA dibuat tidak wajib: bot tetap berfungsi penuh dengan HLS-only.
Dipakai hanya untuk dua hal:
  1. Membedakan "reconnect pada live yang sama" vs "live baru" memakai slug sesi IDN
     (contoh slug: `haii-260915223455` = judul + timestamp sesi, unik per live).
  2. Mengambil judul live (slug IDN dibuat dari judul yang diinput member).

Aturan penting (karena server IDN pernah down saat banyak member live):
  - Timeout pendek, retry maksimal 1x, TIDAK PERNAH melempar exception.
  - Hasil dilaporkan sebagai:
        None                 → tidak diketahui (IDN down / timeout / dimatikan)
        {"slug": "", ...}    → IDN tersedia, member TIDAK sedang live
        {"slug": "...", ...} → member sedang live
  - Cache pendek supaya beberapa grup merge yang finalize bersamaan tidak
    menembak IDN berulang kali.
  - UUID member (permanen) di-cache tanpa kedaluwarsa.
"""
import asyncio
import logging
import re
import time
from typing import Optional

import httpx

from bot.config import Config

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.idn.app/graphql"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

QUERY_PROFILE = """
query Profile($username: String!){
  getPublicProfileByUsername(username: $username){
    uuid
    username
    name
  }
}
"""

QUERY_LIVESTREAM_BY_STREAMER = """
query LiveByStreamer($streamerID: String, $category: String, $page: Int){
  getLivestreams(streamerID: $streamerID, category: $category, page: $page){
    slug
    title
    status
    live_at
    playback_url
    creator { name username uuid }
  }
}
"""

# Hasil "IDN ada, tapi member tidak live"
NO_LIVE: dict = {
    "slug": "", "title": "", "live_key": "", "live_at": "",
    "playback_url": "", "status": "", "uuid": "",
}

# Timestamp di akhir slug IDN: `haii-260913192155` → `260913` (YYMMDD) + `192155` (HHMMSS)
SLUG_TIMESTAMP_RE = re.compile(r"-\d{12}$")


def live_key_from(slug: str, title: str = "") -> str:
    """
    Identitas sebuah SESI LIVE untuk keperluan merge.

    PENTING (pelajaran nyata dari live jkt48_daisy, 13 Sep 2026):
    saat member LAG lalu RECONNECT, IDN membuat **slug BARU dengan judul SAMA**:
        haii-260913192155  →  haii-260913201248  →  haii-260913201503
    Jadi slug (beserta timestamp-nya) BUKAN identitas live — kalau dipakai sebagai
    identitas, satu live akan terpecah menjadi beberapa video.

    Yang jadi identitas = **judul live**. Fungsi ini mengembalikan judul tsb;
    bila judul dari API kosong, judul diekstrak dari slug dengan membuang
    timestamp di belakangnya (`haii-260913192155` → `haii`).
    """
    key = (title or "").strip().lower()
    if key:
        return key
    key = (slug or "").strip().lower()
    if not key:
        return ""
    key = SLUG_TIMESTAMP_RE.sub("", key)
    return key.strip("-").strip()


class IDNLookup:
    """Lookup live IDN per member secara hemat & aman (never raises)."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._uuid_cache: dict[str, str] = {}          # username -> uuid (permanen)
        self._live_cache: dict[str, tuple[float, dict]] = {}  # username -> (ts, info)
        self._lock = asyncio.Lock()

    # ── plumbing ───────────────────────────────────────────────────────
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=Config.IDN_LOOKUP_TIMEOUT_SECONDS,
                headers=HEADERS,
            )
        return self._client

    async def _post(self, query: str, variables: dict, operation: str) -> Optional[dict]:
        """POST ke GraphQL IDN. Return None kalau gagal (tidak pernah raise)."""
        payload = {"query": query, "variables": variables, "operationName": operation}
        for attempt in (1, 2):
            try:
                resp = await self._get_client().post(GRAPHQL_URL, json=payload)
                if resp.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                body = resp.json()
                if body.get("errors"):
                    logger.debug("IDN GraphQL error (%s): %s", operation, body["errors"])
                    return None
                return body.get("data") or {}
            except Exception as exc:
                if attempt == 1:
                    await asyncio.sleep(1.5)
                    continue
                logger.debug("IDN lookup %s gagal (diabaikan): %s", operation, exc)
                return None
        return None

    async def _get_uuid(self, username: str) -> Optional[str]:
        """UUID member (di-cache permanen karena tidak pernah berubah)."""
        cached = self._uuid_cache.get(username)
        if cached:
            return cached

        data = await self._post(QUERY_PROFILE, {"username": username}, "Profile")
        if data is None:
            return None
        profile = data.get("getPublicProfileByUsername") or {}
        uuid = (profile.get("uuid") or "").strip()
        if uuid:
            self._uuid_cache[username] = uuid
            return uuid
        return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "IDNLookup":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── public API ─────────────────────────────────────────────────────
    async def fetch_member_live(self, username: str) -> Optional[dict]:
        """
        Cek apakah `username` sedang live di IDN.

        Return:
          None                 → tidak diketahui (dimatikan / IDN down / timeout)
          NO_LIVE (slug="")    → IDN menjawab: member TIDAK live
          {"slug", "title", "live_at", "playback_url", "uuid"} → member live
        """
        username = (username or "").strip().lower()
        if not username:
            return None
        if not Config.IDN_LOOKUP_ENABLED:
            return None

        # cache
        now = time.monotonic()
        cached = self._live_cache.get(username)
        if cached and (now - cached[0]) < Config.IDN_LOOKUP_CACHE_SECONDS:
            return cached[1]

        try:
            info = await self._fetch_uncached(username)
        except Exception as exc:
            # Jaring pengaman terakhir: modul ini TIDAK BOLEH melempar exception,
            # karena bot harus tetap jalan dengan HLS-only saat IDN bermasalah.
            logger.debug("IDN lookup %s gagal total (diabaikan): %s", username, exc)
            return cached[1] if cached else None

        # Cache HANYA hasil yang pasti (bukan None) supaya saat IDN down kita tidak
        # memaksa request ulang terus-menerus dari beberapa pemanggil.
        if info is not None:
            self._live_cache[username] = (now, info)
        elif cached:
            # IDN sedang bermasalah → pakai hasil terakhir yang diketahui
            return cached[1]
        return info

    async def _fetch_uncached(self, username: str) -> Optional[dict]:
        async with self._lock:
            # double-check cache setelah menunggu lock
            now = time.monotonic()
            cached = self._live_cache.get(username)
            if cached and (now - cached[0]) < Config.IDN_LOOKUP_CACHE_SECONDS:
                return cached[1]

            uuid = await self._get_uuid(username)
            if not uuid:
                return None  # IDN tidak memberi jawaban → unknown

            data = await self._post(
                QUERY_LIVESTREAM_BY_STREAMER,
                {"streamerID": uuid, "category": "all", "page": 1},
                "LiveByStreamer",
            )
            if data is None:
                return None

            lives = data.get("getLivestreams") or []
            for live in lives:
                creator = live.get("creator") or {}
                live_username = (creator.get("username") or "").lower()
                # Pastikan ini benar-benar milik member tersebut
                if live_username and live_username != username:
                    continue
                slug = (live.get("slug") or "").strip()
                title = (live.get("title") or "").strip()
                return {
                    "slug": slug,
                    "title": title,
                    # Identitas sesi live untuk merge (judul, bukan slug — lihat live_key_from)
                    "live_key": live_key_from(slug, title),
                    "live_at": live.get("live_at") or "",
                    "playback_url": live.get("playback_url") or "",
                    "status": live.get("status") or "",
                    "uuid": uuid,
                }

            # IDN menjawab dengan jelas: tidak ada live aktif
            return dict(NO_LIVE, uuid=uuid)


async def fetch_member_live(username: str) -> Optional[dict]:
    """Helper sekali pakai (membuat instance sementara). Untuk loop bot, pakai IDNLookup."""
    async with IDNLookup() as lookup:
        return await lookup.fetch_member_live(username)