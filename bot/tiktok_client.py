"""
tiktok_client.py - Sumber data arsip TikTok untuk JKT48 Live Bot.

Satu postingan TikTok bisa berupa VIDEO atau FOTO (slide). Story juga diambil
dan disimpan sebagai video. Semua akses publik — tanpa login TikTok.

Tiga penyedia data (`Config.TIKTOK_PROVIDER`):
  - ``tikwm``    : API JSON publik tikwm.com (paling ringkas; batas ±1 req/detik
                   untuk paket gratis, jadi dipanggil dengan RateLimiter)
  - ``ytdlp``    : ekstraktor TikTok bawaan yt-dlp (tanpa login, sudah dipakai
                   bot untuk IDN/Showroom). Butuh ``sec_uid`` akun supaya
                   listing profil stabil — lihat `YtDlpProvider.fetch_user_posts`.
  - ``fixture``  : membaca JSON lokal (folder `Config.TIKTOK_FIXTURE_DIR`),
                   dipakai unit test & verifikasi tanpa jaringan.
  - ``auto``     : coba ``tikwm`` lalu jatuh ke ``ytdlp`` bila diblokir.

Catatan lapangan (21 Sep 2026): tikwm.com & halaman profil TikTok sering
dijawab tantangan Cloudflare (HTTP 403 / halaman stub ±1,4 KB) bila diminta
dari IP datacenter dalam jumlah banyak. Karena itu setiap kegagalan membuat
penyedia ditandai "tidak sehat" sementara (`_UNHEALTHY_SECONDS`) dan bot
berpindah ke penyedia berikutnya — bot tidak pernah berhenti karenanya.

Format file fixture::

    {
      "account": {"unique_id": "indahjkt48", "nickname": "Indah JKT48",
                  "sec_uid": "MS4wLjAB..."},
      "videos":  [ <item gaya tikwm / yt-dlp> ],
      "stories": [ <item gaya tikwm / yt-dlp> ]
    }

Item foto memakai kunci ``images`` (daftar URL; boleh path lokal untuk uji),
item video memakai ``play``/``hdplay`` (URL) atau ``url`` (halaman TikTok).
"""
import asyncio
import json
import logging
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from bot.config import Config

logger = logging.getLogger(__name__)

TIKWM_API_BASE = "https://www.tikwm.com/api"
TIKTOK_WEB_BASE = "https://www.tiktok.com"

# Header browser: tanpa ini tikwm menjawab tantangan Cloudflare.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tikwm.com/",
}

# Berapa lama penyedia yang gagal tidak dipakai lagi (detik).
_UNHEALTHY_SECONDS = 900


class ProviderError(RuntimeError):
    """Kegagalan umum penyedia data TikTok."""


class ProviderBlocked(ProviderError):
    """Penyedia diblokir (Cloudflare/403) atau terkena rate-limit."""


@dataclass
class TikTokItem:
    """Satu postingan/story TikTok yang sudah dinormalisasi."""

    id: str
    unique_id: str
    kind: str = "video"          # 'video' | 'photo'
    title: str = ""
    created_at: str = ""         # UTC ISO8601 ('' bila tidak diketahui)
    duration_seconds: int = 0
    images: list[str] = field(default_factory=list)   # kind='photo'
    video_url: str = ""          # URL halaman/CDN video (kind='video')
    cover_url: str = ""
    is_story: bool = False
    source_url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def image_count(self) -> int:
        return len(self.images or [])

    @property
    def page_url(self) -> str:
        """URL halaman TikTok (input paling andal untuk yt-dlp)."""
        if self.source_url:
            return self.source_url
        return f"{TIKTOK_WEB_BASE}/@{self.unique_id}/video/{self.id}"

    def to_db_row(self) -> dict:
        """Bentuk yang diterima `database.insert_tiktok_post()`."""
        return {
            "id": self.id,
            "unique_id": self.unique_id,
            "kind": self.kind,
            "is_story": self.is_story,
            "title": self.title,
            "created_at": self.created_at,
            "duration_seconds": self.duration_seconds,
            "image_count": self.image_count,
            "cover_url": self.cover_url,
            "source_url": self.page_url,
            "images": self.images,
        }


def epoch_to_utc(value: Any) -> str:
    """Epoch detik → ISO8601 UTC (dengan penanda zona waktu); '' bila tidak valid."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _looks_like_cloudflare(text: str) -> bool:
    lowered = (text or "")[:2000].lower()
    return "just a moment" in lowered or "cf-mitigated" in lowered or "slardar" in lowered


class RateLimiter:
    """
    Pembatas laju sederhana (jeda minimum antar request).

    Dipakai agar tidak menabrak batas gratis tikwm (±1 request/detik) dan supaya
    pemantauan 51 akun tidak dianggap scraping agresif oleh TikTok.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._last_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._min_interval - (now - self._last_at)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_at = time.monotonic()


def ytdlp_command() -> list[str]:
    """
    Perintah yt-dlp yang tersedia di mesin ini.

    Produksi (VPS) memakai binary `yt-dlp`; bila belum terpasang, jatuh ke modul
    Python (`python -m yt_dlp`) seperti di mesin pengembangan.
    """
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    return [sys.executable or "python", "-m", "yt_dlp"]

def normalize_ytdlp_entry(entry: dict, unique_id: str, is_story: bool = False) -> TikTokItem:
    """Ubah satu entri yt-dlp (atau hasil `--dump-single-json`) menjadi TikTokItem."""
    video_id = str(entry.get("id") or "").strip()
    images = [u for u in (entry.get("images") or []) if u]
    created = epoch_to_utc(entry.get("timestamp"))
    if not created:
        upload_date = str(entry.get("upload_date") or "")
        if re.fullmatch(r"\d{8}", upload_date):
            created = (
                f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                "T00:00:00+00:00"
            )
    return TikTokItem(
        id=video_id,
        unique_id=(entry.get("uploader_id") or entry.get("channel_id") or unique_id or "").strip(),
        kind="photo" if images else (entry.get("kind") or "video"),
        title=(entry.get("title") or entry.get("description") or "").strip(),
        created_at=created,
        duration_seconds=int(entry.get("duration") or 0),
        images=images,
        video_url=entry.get("url") or entry.get("webpage_url") or "",
        cover_url=entry.get("thumbnail") or "",
        is_story=is_story,
        source_url=entry.get("webpage_url") or "",
        raw=entry,
    )


def normalize_tikwm_item(item: dict, unique_id: str, is_story: bool = False) -> TikTokItem:
    """
    Ubah item respons tikwm menjadi TikTokItem.

    tikwm memakai `video_id`/`id` untuk ID, `create_time` (epoch) untuk waktu,
    `images` untuk postingan foto (tanpa `images` = video), dan
    `play`/`hdplay` untuk URL video tanpa watermark.
    """
    video_id = str(item.get("video_id") or item.get("id") or item.get("aweme_id") or "").strip()
    images = [u for u in (item.get("images") or []) if u]
    title = item.get("title")
    if not title:
        desc = item.get("content_desc")
        title = " ".join(desc) if isinstance(desc, list) else (desc or "")
    author = item.get("author") or {}
    return TikTokItem(
        id=video_id,
        unique_id=str(author.get("unique_id") or unique_id or "").strip(),
        kind="photo" if images else "video",
        title=str(title or "").strip(),
        created_at=epoch_to_utc(item.get("create_time")),
        duration_seconds=int(item.get("duration") or 0),
        images=images,
        video_url=item.get("hdplay") or item.get("play") or "",
        cover_url=item.get("cover") or item.get("origin_cover") or "",
        is_story=is_story,
        source_url=(
            f"{TIKTOK_WEB_BASE}/@{author.get('unique_id') or unique_id}/video/{video_id}"
            if video_id else ""
        ),
        raw=item,
    )


class BaseProvider:
    """Kontrak penyedia data arsip TikTok."""

    name = "base"

    def __init__(self, rate_limiter: Optional[RateLimiter] = None) -> None:
        self._limiter = rate_limiter or RateLimiter(Config.TIKTOK_REQUEST_INTERVAL_SECONDS)
        self._unhealthy_until = 0.0

    # ── kesehatan penyedia ────────────────────────────────────────────────
    def is_healthy(self) -> bool:
        return time.monotonic() >= self._unhealthy_until

    def mark_unhealthy(self, reason: str) -> None:
        self._unhealthy_until = time.monotonic() + _UNHEALTHY_SECONDS
        logger.warning(
            "Penyedia TikTok '%s' ditandai tidak sehat %d menit: %s",
            self.name, _UNHEALTHY_SECONDS // 60, reason,
        )

    # ── API publik ─────────────────────────────────────────────────────────
    async def fetch_user_posts(
        self, account: dict, limit: int
    ) -> list[TikTokItem]:
        """Postingan terbaru sebuah akun (urut terbaru dulu)."""
        raise NotImplementedError

    async def fetch_user_stories(self, account: dict) -> list[TikTokItem]:
        """Story aktif sebuah akun. Default: tidak didukung → []."""
        return []

    async def fetch_user_info(self, unique_id: str) -> dict:
        """Profil akun (nickname / secUid). Default: tidak didukung → {}."""
        return {}

    async def close(self) -> None:
        """Lepaskan resource (koneksi HTTP)."""
        return None


class TikwmProvider(BaseProvider):
    """
    Penyedia berbasis API JSON tikwm.com.

    Endpoint yang dipakai:
      GET /api/user/posts?unique_id=<u>&count=<n>&cursor=<c>
          → {code:0, data:{videos:[…], cursor:<int>, hasMore:<bool>}}
      GET /api/user/stories?unique_id=<u>
          → {code:0, data:[…]}  (bila endpoint ini tidak tersedia: 404 → [])
      GET /api/user/info?unique_id=<u>   → profil (secUid/nickname)
      GET /api/?url=<halaman TikTok>     → detail satu postingan

    Semantik respons dijaga defensif: nama wadah item bisa `videos`, `items`,
    `aweme_list`, atau `data` berupa list langsung — semuanya diterima.
    """

    name = "tikwm"

    def __init__(self, rate_limiter: Optional[RateLimiter] = None) -> None:
        super().__init__(rate_limiter)
        self._client: Optional[httpx.AsyncClient] = None
        self._stories_supported = True

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0, headers=_BROWSER_HEADERS, follow_redirects=True
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> Any:
        """
        Panggil satu endpoint tikwm. Return payload `data`.

        Raises:
            ProviderBlocked: 403 / tantangan Cloudflare / rate-limit.
            ProviderError: respons sukses tapi badannya bukan JSON.
        """
        await self._limiter.wait()
        client = self._get_client()
        try:
            response = await client.get(f"{TIKWM_API_BASE}{path}", params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"tikwm {path}: {exc}") from exc

        text = response.text or ""
        if response.status_code in (403, 429):
            raise ProviderBlocked(f"tikwm {path}: HTTP {response.status_code}")
        if _looks_like_cloudflare(text):
            raise ProviderBlocked(f"tikwm {path}: tantangan Cloudflare")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"tikwm {path}: bukan JSON ({text[:120]!r})") from exc

        code = payload.get("code")
        if code is not None and str(code) != "0":
            raise ProviderError(f"tikwm {path}: code={code} msg={payload.get('msg')}")
        return payload.get("data")

    async def fetch_user_posts(self, account: dict, limit: int) -> list[TikTokItem]:
        """Ambil `limit` postingan terbaru (mengikuti cursor bila perlu)."""
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id:
            return []
        wanted = max(1, int(limit))
        collected: list[TikTokItem] = []
        cursor: Any = ""
        while len(collected) < wanted:
            page = await self._get(
                "/user/posts",
                {
                    "unique_id": unique_id,
                    "count": min(35, wanted - len(collected)),
                    "cursor": cursor or "",
                },
            )
            raw_items = _extract_items(page)
            if not raw_items:
                break
            for raw in raw_items:
                item = normalize_tikwm_item(raw, unique_id)
                if item.id:
                    collected.append(item)
                if len(collected) >= wanted:
                    break
            if not isinstance(page, dict):
                break
            next_cursor = page.get("cursor")
            if not page.get("hasMore") or not next_cursor or str(next_cursor) == str(cursor):
                break
            cursor = next_cursor
        return collected[:wanted]

    async def fetch_user_stories(self, account: dict) -> list[TikTokItem]:
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id or not self._stories_supported:
            return []
        try:
            payload = await self._get("/user/stories", {"unique_id": unique_id})
        except ProviderBlocked:
            raise
        except ProviderError as exc:
            # Endpoint story tidak tersedia di tikwm → jangan dicoba terus.
            self._stories_supported = False
            logger.info("tikwm tanpa dukungan story (%s); story dilewati.", exc)
            return []
        items: list[TikTokItem] = []
        for raw in _extract_items(payload):
            item = normalize_tikwm_item(raw, unique_id, is_story=True)
            if item.id:
                items.append(item)
        return items

    async def fetch_user_info(self, unique_id: str) -> dict:
        """Profil akun (nickname & secUid). Return {} bila tidak tersedia."""
        payload = await self._get("/user/info", {"unique_id": unique_id})
        if not isinstance(payload, dict):
            return {}
        user = payload.get("user") or payload
        return {
            "unique_id": user.get("unique_id") or user.get("uniqueId") or unique_id,
            "nickname": user.get("nickname") or "",
            "sec_uid": user.get("sec_uid") or user.get("secUid") or "",
        }



def _extract_items(payload: Any) -> list[dict]:
    """
    Ambil daftar item dari respons tikwm apa pun bentuknya.

    Bentuk yang pernah terlihat: `data.videos`, `data.items`,
    `data.aweme_list`, atau `data` sendiri berupa list.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        for key in ("videos", "items", "aweme_list", "itemList", "posts"):
            value = payload.get(key)
            if isinstance(value, list):
                return [i for i in value if isinstance(i, dict)]
        single = payload.get("video")   # detail satu postingan
        if isinstance(single, dict):
            return [single]
    return []


def normalize_any(raw: dict, unique_id: str, is_story: bool = False) -> TikTokItem:
    """Normalisasi item tanpa tahu asalnya (tikwm atau yt-dlp)."""
    if any(k in raw for k in ("video_id", "create_time", "play", "hdplay", "content_desc")):
        return normalize_tikwm_item(raw, unique_id, is_story)
    return normalize_ytdlp_entry(raw, unique_id, is_story)


class YtDlpProvider(BaseProvider):
    """
    Penyedia berbasis ekstraktor TikTok yt-dlp (tanpa login).

    Listing profil TikTok butuh **secUid** akun: bila tersimpan di database,
    input URL memakai `tiktokuser:<secUid>` yang stabil. Tanpa secUid, yt-dlp
    memakai URL profil `https://www.tiktok.com/@user` — dan TikTok sering
    menjawab halaman stub sehingga yt-dlp gagal dengan pesan "Unable to extract
    secondary user ID". Bila itu terjadi, penyedia ditandai tidak sehat dan
    penyedia lain (tikwm) yang dipakai.
    """

    name = "ytdlp"

    # Listing profil bisa lambat (TikTok membalas pelan dari IP datacenter).
    LIST_TIMEOUT_SECONDS = 180.0
    DETAIL_TIMEOUT_SECONDS = 120.0

    async def _run(self, args: list[str], timeout: float) -> tuple[int, str, str]:
        """Jalankan yt-dlp; return (returncode, stdout, stderr)."""
        cmd = [*ytdlp_command(), *args]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return -1, "", f"yt-dlp timeout setelah {timeout:.0f}s"
        return (
            process.returncode if process.returncode is not None else -1,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _target_url(account: dict) -> str:
        sec_uid = (account.get("sec_uid") or "").strip()
        if sec_uid:
            return f"tiktokuser:{sec_uid}"
        return f"{TIKTOK_WEB_BASE}/@{(account.get('unique_id') or '').strip()}"

    async def fetch_user_posts(self, account: dict, limit: int) -> list[TikTokItem]:
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id:
            return []
        wanted = max(1, int(limit))
        await self._limiter.wait()
        code, stdout, stderr = await self._run(
            [
                "--flat-playlist",
                "--playlist-end", str(wanted),
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                self._target_url(account),
            ],
            self.LIST_TIMEOUT_SECONDS,
        )
        if code != 0:
            message = (stderr or stdout or "").strip().replace("\n", " ")[:200]
            if "secondary user ID" in message or "Unable to extract" in message:
                raise ProviderBlocked(f"listing profil butuh secUid: {message}")
            raise ProviderError(f"yt-dlp listing gagal: {message}")
        try:
            payload = json.loads(stdout or "{}")
        except ValueError as exc:
            raise ProviderError(f"yt-dlp listing bukan JSON: {exc}") from exc

        items: list[TikTokItem] = []
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            item = normalize_ytdlp_entry(entry, unique_id)
            if item.id:
                items.append(item)
        return items

    async def fetch_item_detail(self, page_url: str, unique_id: str) -> Optional[TikTokItem]:
        """Detail satu postingan (dipakai saat unduhan butuh metadata lengkap)."""
        await self._limiter.wait()
        code, stdout, stderr = await self._run(
            ["--dump-single-json", "--skip-download", "--no-warnings", page_url],
            self.DETAIL_TIMEOUT_SECONDS,
        )
        if code != 0:
            logger.debug("yt-dlp detail gagal (%s): %s", page_url, (stderr or "")[:200])
            return None
        try:
            payload = json.loads(stdout or "{}")
        except ValueError:
            return None
        if not payload.get("id"):
            return None
        item = normalize_ytdlp_entry(payload, unique_id)
        return item if item.id else None

    async def resolve_sec_uid(self, account: dict, sample_page_url: str = "") -> str:
        """
        Cari `secUid` akun (identitas stabil untuk listing profil).

        Dua jalur, urut dari yang paling andal:
          1. Detail satu postingan contoh → field `channel_id` (= secUid).
          2. Listing profil (hanya berhasil bila TikTok tidak menjawab stub).
        Return '' bila keduanya gagal — bot lanjut memakai penyedia lain.
        """
        sec_uid = (account.get("sec_uid") or "").strip()
        if sec_uid:
            return sec_uid
        unique_id = (account.get("unique_id") or "").strip()
        if sample_page_url:
            detail = await self.fetch_item_detail(sample_page_url, unique_id)
            found = str(((detail.raw if detail else {}) or {}).get("channel_id") or "")
            if found:
                return found
        try:
            items = await self.fetch_user_posts(account, 1)
        except ProviderError as exc:
            logger.debug("resolve_sec_uid(%s) gagal: %s", unique_id, exc)
            return ""
        if not items:
            return ""
        detail = await self.fetch_item_detail(items[0].page_url, unique_id)
        if detail is None:
            return ""
        return str((detail.raw or {}).get("channel_id") or "")

    async def fetch_user_info(self, unique_id: str) -> dict:
        """
        Profil akun lewat yt-dlp (hanya secUid yang bisa dipastikan).

        yt-dlp tidak punya endpoint profil, jadi secUid diambil dari detail
        postingan terbaru (`channel_id`) — lihat `resolve_sec_uid()`. Nama
        tampilan dibiarkan kosong; nama member tetap datang dari `member_hls`.
        Setelah secUid tersimpan, listing profil memakai `tiktokuser:<secUid>`
        yang jauh lebih stabil.
        """
        sec_uid = await self.resolve_sec_uid({"unique_id": unique_id})
        if not sec_uid:
            return {}
        return {"unique_id": unique_id, "nickname": "", "sec_uid": sec_uid}


class FixtureProvider(BaseProvider):
    """
    Penyedia dari berkas JSON lokal (`Config.TIKTOK_FIXTURE_DIR/<akun>.json`).

    Dipakai unit test & verifikasi supaya seluruh alur (deteksi postingan →
    unduh/slide show → upload → halaman web) bisa diuji tanpa jaringan dan tanpa
    bergantung pada anti-bot TikTok.
    """

    name = "fixture"

    def __init__(self, rate_limiter: Optional[RateLimiter] = None, fixture_dir: str = "") -> None:
        super().__init__(rate_limiter or RateLimiter(0))
        self._dir = Path(fixture_dir or Config.TIKTOK_FIXTURE_DIR)

    def _load(self, unique_id: str) -> dict:
        path = self._dir / f"{unique_id}.json"
        if not path.exists():
            logger.debug("Fixture TikTok tidak ada: %s", path)
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            logger.warning("Fixture TikTok rusak (%s): %s", path, exc)
            return {}

    async def fetch_user_posts(self, account: dict, limit: int) -> list[TikTokItem]:
        unique_id = (account.get("unique_id") or "").strip()
        data = self._load(unique_id)
        items: list[TikTokItem] = []
        for raw in data.get("videos") or []:
            item = normalize_any(raw, unique_id)
            if item.id:
                items.append(item)
        return items[: max(1, int(limit))]

    async def fetch_user_stories(self, account: dict) -> list[TikTokItem]:
        unique_id = (account.get("unique_id") or "").strip()
        data = self._load(unique_id)
        items: list[TikTokItem] = []
        for raw in data.get("stories") or []:
            item = normalize_any(raw, unique_id, is_story=True)
            if item.id:
                items.append(item)
        return items

    async def fetch_user_info(self, unique_id: str) -> dict:
        account = (self._load(unique_id).get("account") or {})
        if not account:
            return {}
        return {
            "unique_id": account.get("unique_id") or unique_id,
            "nickname": account.get("nickname") or "",
            "sec_uid": account.get("sec_uid") or "",
        }


def build_providers(mode: Optional[str] = None) -> list[BaseProvider]:
    """
    Bangun daftar penyedia sesuai `Config.TIKTOK_PROVIDER`.

    Urutan = prioritas pemakaian. `auto` mencoba tikwm (JSON ringkas, mendukung
    foto) lebih dulu, lalu yt-dlp (tanpa batas pihak ketiga).
    """
    choice = (mode or Config.TIKTOK_PROVIDER or "auto").lower().strip()
    limiter = RateLimiter(Config.TIKTOK_REQUEST_INTERVAL_SECONDS)
    if choice == "fixture":
        return [FixtureProvider(limiter)]
    if choice == "tikwm":
        return [TikwmProvider(limiter)]
    if choice == "ytdlp":
        return [YtDlpProvider(limiter)]
    if choice != "auto":
        logger.warning("TIKTOK_PROVIDER=%r tidak dikenal; memakai 'auto'.", choice)
    return [TikwmProvider(limiter), YtDlpProvider(limiter)]
