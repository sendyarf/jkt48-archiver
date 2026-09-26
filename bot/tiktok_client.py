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
  - ``embed``    : halaman embed TikTok (listing paling andal, ≤10 post).
  - ``scrape``   : halaman profil tiktok.com/@user langsung (regex + JSON
                   rehidrasi); fallback saat tikwm/embed dijawab Cloudflare.
  - ``fixture``  : membaca JSON lokal (folder `Config.TIKTOK_FIXTURE_DIR`),
                   dipakai unit test & verifikasi tanpa jaringan.
  - ``auto``     : coba ``tikwm`` → ``embed`` → ``scrape`` → ``ytdlp``.

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
import random
import re
import shlex
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


# ─── Lapisan HTTP (curl_cffi = kunci) ───────────────────────────────────────
#
# Temuan 21 Sep 2026 (dibandingkan dengan proyek JKT48_TIKTOK yang sudah jalan):
# request biasa (httpx/requests) ke tikwm.com dijawab **Cloudflare 403**, tetapi
# `curl_cffi` dengan `impersonate="chrome131"` (meniru TLS/HTTP2 fingerprint
# Chrome) TEMBUS — `/api/user/story` balas HTTP 200 dari mesin yang sama.
# Halaman embed TikTok juga hanya mau membalas 200 lewat jalur ini.
#
# curl_cffi bersifat sinyal sehingga dipanggil di thread terpisah
# (`asyncio.to_thread`) supaya tidak memblokir event loop bot.
try:  # pragma: no cover - tergantung lingkungan
    from curl_cffi import requests as _curl_requests  # type: ignore

    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    _curl_requests = None
    _HAS_CURL_CFFI = False

# User-Agent diputar supaya pola trafik tidak identik setiap request.
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

_API_HEADERS_EXTRA = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.tikwm.com",
    "Referer": "https://www.tikwm.com/",
}
_HTML_HEADERS_EXTRA = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def browser_headers(api: bool = False, form: bool = False) -> dict:
    """Header browser (UA acak) untuk tikwm (`api=True`) atau halaman TikTok."""
    headers = {"User-Agent": random.choice(_UAS)}
    headers.update(_API_HEADERS_EXTRA if api else _HTML_HEADERS_EXTRA)
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return headers


def impersonate_kwargs() -> dict:
    """Argumen `impersonate` untuk curl_cffi (kosong bila tidak terpasang)."""
    if _HAS_CURL_CFFI:
        return {"impersonate": Config.TIKTOK_IMPERSONATE or "chrome131"}
    return {}


def _sync_request(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """
    Satu request sinkron (curl_cffi bila ada, kalau tidak httpx).

    Returns:
        (status_code, body) — status_code 0 berarti kegagalan jaringan.
    """
    headers = headers or browser_headers()
    kwargs = impersonate_kwargs()
    if _HAS_CURL_CFFI:
        try:
            response = _curl_requests.request(
                method, url, params=params, data=data, headers=headers,
                timeout=timeout, **kwargs,
            )
            return response.status_code, response.text
        except Exception as exc:  # noqa: BLE001 - jaringan apa pun
            logger.debug("%s %s gagal: %s", method, url, exc)
            return 0, ""
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            response = client.request(method, url, params=params, data=data)
            return response.status_code, response.text
    except httpx.HTTPError as exc:
        logger.debug("%s %s gagal: %s", method, url, exc)
        return 0, ""


async def http_request(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Versi async dari `_sync_request` (curl_cffi dijalankan di thread)."""
    return await asyncio.to_thread(
        _sync_request, method, url,
        params=params, data=data, headers=headers, timeout=timeout,
    )


async def http_get_json(
    url: str, params: Optional[dict] = None, *, api: bool = False, timeout: float = 30.0
) -> tuple[int, Optional[dict]]:
    """GET + parse JSON. Return (status, payload|None)."""
    status, body = await http_request(
        "GET", url, params=params, headers=browser_headers(api=api), timeout=timeout
    )
    if status != 200 or not body:
        return status, None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


async def http_get_text(
    url: str, *, headers: Optional[dict] = None, timeout: float = 30.0
) -> tuple[int, str]:
    """GET halaman HTML (mis. halaman embed TikTok)."""
    return await http_request(
        "GET", url, headers=headers or browser_headers(), timeout=timeout
    )


# Status yang bersifat SEMENTARA pada halaman embed TikTok. Proyek JKT48_TIKTOK
# mencatat halaman embed sering membalas 503 saat overload; retry singkat
# biasanya berhasil sehingga tidak perlu jatuh ke penyedia lain.
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


async def http_get_text_retry(
    url: str,
    *,
    attempts: int = 3,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """`http_get_text` dengan retry+backoff untuk status transient (503 dsb.)."""
    status, body = 0, ""
    for attempt in range(1, max(1, attempts) + 1):
        status, body = await http_get_text(url, timeout=timeout)
        if status == 200 and body:
            return status, body
        if status not in _TRANSIENT_STATUS or attempt == attempts:
            return status, body
        # Jitter acak supaya 51 akun tidak retry serentak pada detik yang sama.
        wait = 1.5 * attempt * random.uniform(0.8, 1.4)
        logger.info("HTML %s: HTTP %s (sementara), coba lagi dalam %.1fs...", url, status, wait)
        await asyncio.sleep(wait)
    return status, body




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
                # Jitter ±25%: 51 akun dengan jeda identik terlihat seperti
                # pola metronom (1.1s, 1.1s, …) dan mudah ditandai anti-bot.
                delay *= random.uniform(0.75, 1.25)
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


def looks_like_photo(item: dict) -> bool:
    """
    Deteksi postingan FOTO dari bentuk item tikwm/item mentah.

    Tiga tanda (mengikuti proyek JKT48_TIKTOK): ada `images`, ada flag eksplisit
    `is_photo`, atau `duration == 0` DAN `size == 0` (foto tidak punya durasi
    maupun ukuran video). Dipakai HANYA sebagai pelengkap: keberadaan `images`
    tetap bukti terkuat.
    """
    if item.get("images"):
        return True
    if item.get("is_photo"):
        return True
    return item.get("duration", -1) == 0 and item.get("size", -1) == 0


def normalize_tikwm_item(item: dict, unique_id: str, is_story: bool = False) -> TikTokItem:
    """
    Ubah item respons tikwm menjadi TikTokItem.

    tikwm memakai `video_id`/`id` untuk ID, `create_time` (epoch) untuk waktu,
    `images` untuk postingan foto (tanpa `images` = video), dan
    `play`/`hdplay`/`download_url` untuk URL video.
    """
    video_id = str(item.get("video_id") or item.get("id") or item.get("aweme_id") or "").strip()
    images = [u for u in (item.get("images") or []) if u]
    title = item.get("title")
    if not title:
        desc = item.get("content_desc")
        title = " ".join(desc) if isinstance(desc, list) else (desc or "")
    author = item.get("author") or {}
    video_url = ""
    # `wmplay` sengaja DIKECUALIKAN: itu varian berwatermark TikTok.
    # Bila hanya `wmplay` yang tersedia, `video_url` dibiarkan kosong →
    # downloader naik ke rung embed (playAddr tanpa watermark).
    for key in ("hdplay", "play", "video_url", "download_url"):
        candidate = item.get(key)
        if isinstance(candidate, str) and candidate.startswith("http"):
            video_url = candidate
            break
    return TikTokItem(
        id=video_id,
        unique_id=str(author.get("unique_id") or unique_id or "").strip(),
        kind="photo" if (images or looks_like_photo(item)) else "video",
        title=str(title or "").strip(),
        created_at=epoch_to_utc(item.get("create_time")),
        duration_seconds=int(item.get("duration") or 0),
        images=images,
        video_url=video_url,
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
        # Kesehatan dicatat PER KAPABILITAS ("posts" / "stories"), karena
        # Cloudflare memblokir per-path: 21 Sep 2026 tikwm menjawab 403 untuk
        # `/user/posts` tetapi 200 untuk `/user/story`. Memakai satu flag global
        # membuat story ikut hilang hanya karena listing diblokir.
        self._unhealthy_until: dict[str, float] = {}
        self._permanent_failures: set[str] = set()

    # ── kesehatan penyedia ────────────────────────────────────────────────
    def is_healthy(self, capability: str = "") -> bool:
        """
        True bila penyedia boleh dipakai untuk `capability` ('' = semua).

        Kapabilitas dianggap tidak sehat bila flag global ('all') menyala, bila
        kapabilitas itu sendiri sedang dijeda, atau bila pernah gagal permanen.
        """
        now = time.monotonic()
        if capability and capability in self._permanent_failures:
            return False
        if now < self._unhealthy_until.get("all", 0.0):
            return False
        if capability and now < self._unhealthy_until.get(capability, 0.0):
            return False
        return True

    def mark_unhealthy(self, reason: str, capability: str = "") -> None:
        """Tandai penyedia (atau satu kapabilitasnya) tidak sehat sementara."""
        key = capability or "all"
        self._unhealthy_until[key] = time.monotonic() + _UNHEALTHY_SECONDS
        logger.warning(
            "Penyedia TikTok '%s' ditandai tidak sehat %d menit untuk %s: %s",
            self.name, _UNHEALTHY_SECONDS // 60,
            "semua kapabilitas" if key == "all" else key, reason,
        )

    def mark_capability_missing(self, capability: str, reason: str = "") -> None:
        """Kapabilitas tidak didukung penyedia ini (mis. embed tanpa story)."""
        self._permanent_failures.add(capability)
        logger.debug("Penyedia '%s' tanpa kapabilitas %s%s",
                     self.name, capability, f": {reason}" if reason else "")

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
    Penyedia berbasis API JSON tikwm.com (via curl_cffi → lolos Cloudflare).

    Endpoint yang dipakai:
      GET/POST /api/user/posts?unique_id=<u>&count=<n>&cursor=<c>
          → {code:0, data:{videos:[…], cursor:<int>, hasMore:<bool>}}
      GET/POST /api/user/story?unique_id=<u>&count=<n>&cursor=<c>
          → {code:0, data:{videos:[…], cursor:<str>, hasMore:<bool>}}
          (STORY memakai path TUNGGAL `/user/story`. `/user/stories` = 404 —
          kesalahan awal yang membuat story tidak pernah terambil.)
      GET /api/user/info?unique_id=<u>   → profil (secUid/nickname)
      POST /api/  (form) {url, hd}       → detail satu postingan

    Catatan lapangan 21 Sep 2026: Cloudflare memblokir `/api/user/posts` (403)
    untuk sebagian IP datacenter, sementara `/api/user/story` tetap 200 — jadi
    story TIDAK boleh bergantung pada keberhasilan listing `user/posts`.
    Batas gratis ±1 request/detik dan kuota harian (mis. "10000 request/ 1 day")
    ditangani `_note_limit()`: rate-limit per detik = jeda pendek, kuota harian =
    jeda panjang.
    """

    name = "tikwm"

    def __init__(self, rate_limiter: Optional[RateLimiter] = None) -> None:
        super().__init__(rate_limiter)
        self._blocked_until = 0.0
        self._quota_cooldown = Config.TIKWM_QUOTA_COOLDOWN_SECONDS
        self._rate_cooldown = Config.TIKWM_RATE_COOLDOWN_SECONDS
        self._rate_cooldown_max = max(
            self._rate_cooldown, Config.TIKWM_RATE_COOLDOWN_MAX_SECONDS
        )
        # Hitung hit rate-limit BERUNTUN: cooldown digandakan 2× per hit
        # (5s → 10s → 20s … maks TIKWM_RATE_COOLDOWN_MAX_SECONDS) karena jeda
        # datar 5s terbukti tetap ditolak tikwm ("Free Api Limit" berulang).
        self._rate_escalations = 0

    def is_blocked(self) -> bool:
        """True selama tikwm sedang dijeda (rate-limit / kuota harian)."""
        return time.monotonic() < self._blocked_until

    def _note_limit(self, message: str) -> None:
        """Catat batas tikwm: kuota harian → jeda panjang, rate/detik → eskalasi."""
        lowered = (message or "").lower()
        now = time.monotonic()
        if "day" in lowered or "10000" in lowered:
            if self._blocked_until - now > self._rate_cooldown:
                return  # sudah dalam jeda panjang
            self._blocked_until = now + self._quota_cooldown
            logger.warning(
                "tikwm kuota harian habis (%s) — tikwm dijeda %d menit.",
                (message or "").strip()[:80], self._quota_cooldown // 60,
            )
            return
        if now >= self._blocked_until:
            cooldown = min(
                self._rate_cooldown * (2 ** self._rate_escalations),
                self._rate_cooldown_max,
            )
            self._rate_escalations += 1
            self._blocked_until = now + cooldown
            logger.info(
                "tikwm rate limit (%s) — jeda %ds (eskalasi #%d).",
                (message or "").strip()[:80], cooldown, self._rate_escalations,
            )

    async def _call(
        self, path: str, params: dict, *, method: str = "GET"
    ) -> Optional[Any]:
        """
        Panggil satu endpoint tikwm. Return payload `data` (None bila kosong).

        Raises:
            ProviderBlocked: 403 / tantangan Cloudflare.
            ProviderError: respons 200 tapi badannya bukan JSON / code != 0.
        """
        if self.is_blocked():
            raise ProviderError(f"tikwm {path}: sedang dijeda (rate/quota limit)")
        await self._limiter.wait()
        url = f"{TIKWM_API_BASE}{path}"
        if method == "POST":
            status, body = await http_request(
                "POST", url, data={**params},
                headers=browser_headers(api=True, form=True), timeout=30.0,
            )
        else:
            status, body = await http_request(
                "GET", url, params=params,
                headers=browser_headers(api=True), timeout=30.0,
            )

        if status in (403, 429):
            raise ProviderBlocked(f"tikwm {path}: HTTP {status}")
        if _looks_like_cloudflare(body):
            raise ProviderBlocked(f"tikwm {path}: tantangan Cloudflare")
        if status != 200 or not body:
            raise ProviderError(f"tikwm {path}: HTTP {status}")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ProviderError(f"tikwm {path}: bukan JSON ({body[:120]!r})") from exc

        code = payload.get("code")
        if code is not None and str(code) != "0":
            message = str(payload.get("msg") or "")
            if "limit" in message.lower():
                self._note_limit(message)
                raise ProviderError(f"tikwm {path}: limit ({message[:80]})")
            raise ProviderError(f"tikwm {path}: code={code} msg={message}")
        self._note_success()
        return payload.get("data")

    def _note_success(self) -> None:
        """Panggilan berhasil → eskalasi rate-limit kembali ke cooldown awal."""
        if self._rate_escalations:
            logger.info("tikwm pulih — eskalasi rate-limit direset.")
        self._rate_escalations = 0

    async def fetch_user_posts(self, account: dict, limit: int) -> list[TikTokItem]:
        """Ambil `limit` postingan terbaru (mengikuti cursor bila perlu)."""
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id:
            return []
        wanted = max(1, int(limit))
        collected: list[TikTokItem] = []
        cursor: Any = ""
        for _ in range(5):   # batas aman: maksimum 5 halaman per akun
            page = await self._call(
                "/user/posts",
                {
                    "unique_id": unique_id,
                    "count": min(35, max(1, wanted - len(collected))),
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
            cursor = _next_cursor(page)
            if len(collected) >= wanted or not cursor:
                break
        return collected[:wanted]

    async def fetch_user_stories(self, account: dict) -> list[TikTokItem]:
        """
        Story aktif sebuah akun.

        Path benar = `/user/story` (TUNGGAL). Bila GET gagal total (mis. variasi
        respons), sekali lagi dicoba dengan POST form-urlencoded seperti yang
        dipakai proyek JKT48_TIKTOK; paginasi mengikuti `hasMore`/`has_more`.

        Story TIDAK bergantung pada hasil listing `/user/posts`: Cloudflare bisa
        memblokir salah satunya saja (terbukti 21 Sep 2026: posts 403 sementara
        story 200), jadi story diambil sebagai permintaan terpisah.
        """
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id:
            return []
        collected: list[TikTokItem] = []
        cursor: Any = ""
        for page_index in range(5):
            try:
                payload = await self._call(
                    "/user/story",
                    {"unique_id": unique_id, "count": 30, "cursor": cursor or ""},
                )
            except ProviderBlocked as exc:
                if page_index == 0:
                    # Coba POST sekali (beberapa endpoint tikwm hanya menerima POST).
                    logger.info("tikwm story GET diblokir (%s); coba POST.", exc)
                    payload = await self._call(
                        "/user/story",
                        {"unique_id": unique_id, "count": 30, "cursor": cursor or ""},
                        method="POST",
                    )
                else:
                    raise
            except ProviderError as exc:
                if page_index == 0:
                    logger.info("tikwm story GET gagal (%s); coba POST.", exc)
                    payload = await self._call(
                        "/user/story",
                        {"unique_id": unique_id, "count": 30, "cursor": cursor or ""},
                        method="POST",
                    )
                else:
                    raise

            raw_items = _extract_items(payload)
            if not raw_items:
                break
            for raw in raw_items:
                item = normalize_tikwm_item(raw, unique_id, is_story=True)
                if item.id:
                    collected.append(item)
            cursor = _next_cursor(payload)
            if not cursor:
                break
        if collected:
            logger.info("tikwm: %d story aktif @%s.", len(collected), unique_id)
        return collected

    async def fetch_item_detail(self, page_url: str, unique_id: str) -> Optional[TikTokItem]:
        """Detail satu postingan lewat POST `/api/` (dipakai untuk foto & tanggal)."""
        url = (page_url or "").strip()
        if not url:
            return None
        payload = await self._call("/", {"url": url, "hd": 1}, method="POST")
        for raw in _extract_items(payload):
            item = normalize_tikwm_item(raw, unique_id)
            if item.id:
                return item
        return None

    async def fetch_user_info(self, unique_id: str) -> dict:
        """Profil akun (nickname & secUid). Return {} bila tidak tersedia."""
        payload = await self._call("/user/info", {"unique_id": unique_id})
        if not isinstance(payload, dict):
            return {}
        user = payload.get("user") or payload
        return {
            "unique_id": user.get("unique_id") or user.get("uniqueId") or unique_id,
            "nickname": user.get("nickname") or "",
            "sec_uid": user.get("sec_uid") or user.get("secUid") or "",
        }


def _next_cursor(payload: Any) -> str:
    """
    Cursor halaman berikutnya dari respons tikwm, '' bila tidak ada lagi.

    tikwm memakai dua gaya penulisan: `hasMore` (camel, endpoint story) dan
    `has_more` (snake, proyek lama). Keduanya diterima; `has_more is False`
    berarti benar-benar habis, sedangkan cursor yang sama dengan sebelumnya
    dianggap tidak maju (stagnan) → berhenti agar tidak infinite loop.
    """
    if not isinstance(payload, dict):
        return ""
    has_more = payload.get("hasMore")
    if has_more is None:
        has_more = payload.get("has_more")
    if has_more is False:
        return ""
    cursor = payload.get("cursor")
    if cursor in (None, "", 0, "0"):
        return ""
    return str(cursor)




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

    @staticmethod
    def _config_args() -> list[str]:
        """
        Argumen yt-dlp dari konfigurasi (cookies dari file/browser, proxy, dan
        argumen bebas tambahan). Kosong bila tidak diatur — perilaku lama utuh.
        Dipakai listing maupun detail karena blokir IP TikTok berlaku per alamat.
        """
        args: list[str] = []
        if Config.TIKTOK_YTDLP_COOKIES_FILE:
            args += ["--cookies", Config.TIKTOK_YTDLP_COOKIES_FILE]
        if Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER:
            args += ["--cookies-from-browser", Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER]
        if Config.TIKTOK_YTDLP_PROXY:
            args += ["--proxy", Config.TIKTOK_YTDLP_PROXY]
        extra = Config.TIKTOK_YTDLP_EXTRA_ARGS
        if extra:
            args += shlex.split(extra)
        return args

    @staticmethod
    def _classify_error(message: str) -> ProviderError:
        """
        Klasifikasikan keluaran gagal yt-dlp. "IP address is blocked" berarti
        alamat IP diblokir TikTok untuk postingan ini — penyedia harus ditandai
        tidak sehat (ProviderBlocked) supaya monitor pindah penyedia, bukan
        memperingatkan berisik tiap siklus (ProviderError biasa).
        """
        lowered = message.lower()
        if "ip address is blocked" in lowered or "blocked from accessing" in lowered:
            return ProviderBlocked(f"IP diblokir TikTok: {message}")
        if "secondary user ID" in message or "Unable to extract" in message:
            return ProviderBlocked(f"listing profil butuh secUid: {message}")
        return ProviderError(f"yt-dlp listing gagal: {message}")

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
                *self._config_args(),
                self._target_url(account),
            ],
            self.LIST_TIMEOUT_SECONDS,
        )
        if code != 0:
            message = (stderr or stdout or "").strip().replace("\n", " ")[:200]
            raise self._classify_error(message)
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
            ["--dump-single-json", "--skip-download", "--no-warnings",
             *self._config_args(), page_url],
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


class EmbedProvider(BaseProvider):
    """
    Penyedia dari halaman **embed** TikTok (tanpa login, lolos WAF).

    Ini metode dari proyek `JKT48_TIKTOK` yang sudah berjalan, dan pada uji
    21 Sep 2026 terbukti paling andal untuk *listing*: halaman
    `https://www.tiktok.com/embed/@<user>` membalas HTTP 200 berisi
    `__FRONTITY_CONNECT_STATE__` dengan `videoList` (10 postingan terbaru +
    yang di-pin), lengkap dengan `id`, `desc`, `playAddr`, dan `coverUrl`.
    Halaman profil biasa `https://www.tiktok.com/@user` justru menjawab halaman
    stub sehingga yt-dlp gagal ("Unable to extract secondary user ID").

    Batasan: hanya ≤10 postingan terbaru, tanpa `createTime`/jumlah foto — itu
    diambil per postingan lewat `fetch_item_detail` (`/embed/v2/<id>` memuat
    `itemInfos.createTime` dan `imagePostInfo.displayImages`). Story tidak
    tersedia di embed; story tetap berasal dari penyedia lain.
    """

    name = "embed"

    # Polling tiap siklus hanya 1 akun, jadi 10 postingan terbaru sudah cukup.
    EMBED_LIST_LIMIT = 10

    async def fetch_user_posts(self, account: dict, limit: int) -> list[TikTokItem]:
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id:
            return []
        status, body = await http_get_text_retry(
            f"{TIKTOK_WEB_BASE}/embed/@{unique_id}", timeout=30.0
        )
        if status in (403, 429):
            raise ProviderBlocked(f"embed profil @{unique_id}: HTTP {status}")
        if status != 200 or not body:
            raise ProviderError(f"embed profil @{unique_id}: HTTP {status}")
        items = parse_embed_profile(body, unique_id)
        if not items:
            # HTTP 200 tapi tanpa data → hampir selalu halaman pembatas.
            raise ProviderBlocked(f"embed profil @{unique_id}: tanpa videoList")
        wanted = max(1, min(int(limit), self.EMBED_LIST_LIMIT))
        return items[:wanted]

    async def fetch_item_detail(self, page_url: str, unique_id: str) -> Optional[TikTokItem]:
        post_id = extract_post_id(page_url)
        if not post_id:
            return None
        status, body = await http_get_text_retry(
            f"{TIKTOK_WEB_BASE}/embed/v2/{post_id}", timeout=30.0
        )
        if status != 200 or not body:
            return None
        return parse_embed_post(body, post_id, unique_id)

    async def fetch_user_info(self, unique_id: str) -> dict:
        """Embed tidak memuat profil lengkap (tanpa secUid/nickname)."""
        return {}


class ScrapeProvider(BaseProvider):
    """
    Penyedia scraping halaman profil TikTok `https://www.tiktok.com/@<user>`
    (metode `_fetch_posts_scrape` dari proyek JKT48_TIKTOK).

    Daftar ID postingan diambil dari dua penanda di HTML profil:
      1. Regex ``/video/(\\d{18,20})`` pada tautan video, dan
      2. JSON ``__UNIVERSAL_DATA_FOR_REHYDRATION__`` →
         ``__DEFAULT_SCOPE__["webapp.user-detail"].itemList``.
    Judul/waktu diambil dari JSON itu bila tersedia; bila tidak, `created_at`
    di-aproksimasi dari ID snowflake TikTok (``int(id) >> 32`` = epoch detik).

    Halaman profil menjawab tantangan Cloudflare/halaman stub untuk banyak IP
    datacenter — tanpa kedua penanda dianggap DIBLOKIR (ProviderBlocked) supaya
    penyedia ditandai tidak sehat dan bot pindah penyedia. Story tidak tersedia
    di halaman profil (sama seperti embed/yt-dlp).
    """

    name = "scrape"

    # Daftar profil per akun hanya memuat ±30 postingan terbaru di HTML.
    SCRAPE_LIST_LIMIT = 30

    async def fetch_user_posts(self, account: dict, limit: int) -> list[TikTokItem]:
        unique_id = (account.get("unique_id") or "").strip()
        if not unique_id:
            return []
        status, body = await http_get_text_retry(
            f"{TIKTOK_WEB_BASE}/@{unique_id}", timeout=30.0
        )
        if status in (403, 429):
            raise ProviderBlocked(f"scrape profil @{unique_id}: HTTP {status}")
        if status != 200 or not body:
            raise ProviderError(f"scrape profil @{unique_id}: HTTP {status}")
        items = parse_scrape_profile(body, unique_id)
        if not items:
            # HTTP 200 tanpa penanda video = halaman stub/pembatas Cloudflare.
            raise ProviderBlocked(f"scrape profil @{unique_id}: tanpa penanda video")
        wanted = max(1, min(int(limit), self.SCRAPE_LIST_LIMIT))
        return items[:wanted]

    # fetch_user_stories: tidak dioverride — halaman profil tidak memuat story
    # (monitor menandai kapabilitas 'stories' hilang, sama seperti embed/ytdlp).


_SCRAPE_VIDEO_RE = re.compile(r"/video/(\d{18,20})")
_SCRAPE_REHYDRATION_RE = re.compile(
    r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _scrape_rehydration_items(html: str) -> dict:
    """
    Peta ``id → item`` dari JSON ``__UNIVERSAL_DATA_FOR_REHYDRATION__`` halaman
    profil. Struktur: ``__DEFAULT_SCOPE__["webapp.user-detail"]["itemList"]``
    (daftar ID) dengan metadata item pada ``itemInfoList``/``itemStruct``.
    """
    match = _SCRAPE_REHYDRATION_RE.search(html or "")
    if not match:
        return {}
    try:
        udata = json.loads(match.group(1))
    except ValueError:
        return {}
    user_detail = (udata.get("__DEFAULT_SCOPE__") or {}).get("webapp.user-detail") or {}
    candidates = user_detail.get("itemInfoList") or user_detail.get("itemStruct") or {}
    items: dict[str, dict] = {}
    if isinstance(candidates, dict):
        for key in ("itemList", "items"):
            for raw in (candidates.get(key) or {}).values() if isinstance(
                candidates.get(key), dict
            ) else []:
                if isinstance(raw, dict) and raw.get("id"):
                    items[str(raw["id"])] = raw
    if isinstance(user_detail.get("itemInfo"), dict):
        for key, raw in user_detail["itemInfo"].items():
            if isinstance(raw, dict):
                items[str(raw.get("id") or key)] = raw
    # Bentuk paling umum: itemInfoList.itemStruct {id1: {...}, id2: {...}}
    for key, raw in (user_detail.get("itemStruct") or {}).items():
        if isinstance(raw, dict):
            items.setdefault(str(raw.get("id") or key), raw)
    return items


def _scrape_item_list_ids(html: str) -> list[str]:
    """Urutan ID dari `webapp.user-detail.itemList` (bila JSON tersedia)."""
    match = _SCRAPE_REHYDRATION_RE.search(html or "")
    if not match:
        return []
    try:
        udata = json.loads(match.group(1))
    except ValueError:
        return []
    user_detail = (udata.get("__DEFAULT_SCOPE__") or {}).get("webapp.user-detail") or {}
    result: list[str] = []
    for raw in user_detail.get("itemList") or []:
        item_id = str(raw).strip()
        if item_id.isdigit() and len(item_id) >= 15 and item_id not in result:
            result.append(item_id)
    return result


def parse_scrape_profile(html: str, unique_id: str) -> list[TikTokItem]:
    """
    Parse halaman profil `/@<user>` menjadi daftar TikTokItem (terbaru dulu).

    ID video dari tautan `/video/<id>` dan `itemList` JSON rehidrasi; judul dan
    waktu diambil dari metadata JSON bila ada, kalau tidak `created_at` diisi
    dari timestamp snowflake ID (``int(id) >> 32``).
    """
    html = html or ""
    ids: list[str] = []
    for vid in _SCRAPE_VIDEO_RE.findall(html):
        if vid not in ids:
            ids.append(vid)
    for vid in _scrape_item_list_ids(html):
        if vid not in ids:
            ids.append(vid)
    if not ids:
        return []
    metadata = _scrape_rehydration_items(html)
    items: list[TikTokItem] = []
    for vid in ids:
        raw = metadata.get(vid) or {}
        created = epoch_to_utc(raw.get("createTime"))
        if not created:
            try:
                created = epoch_to_utc(int(vid) >> 32)  # epoch dari snowflake ID
            except ValueError:
                created = ""
        title = str(raw.get("desc") or raw.get("title") or "").strip()
        cover = raw.get("video") or {}
        items.append(
            TikTokItem(
                id=vid,
                unique_id=unique_id,
                kind="photo" if raw.get("imagePost") else "video",
                title=title,
                created_at=created,
                duration_seconds=int((cover.get("duration") or 0) if isinstance(cover, dict) else 0),
                cover_url=str((cover.get("cover") or "") if isinstance(cover, dict) else ""),
                source_url=f"{TIKTOK_WEB_BASE}/@{unique_id}/video/{vid}",
                raw=raw,
            )
        )
    return items


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


_EMBED_STATE_RE = re.compile(
    r'<script[^>]*id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', re.DOTALL
)


def _embed_state(html: str) -> Optional[dict]:
    """Ambil JSON `__FRONTITY_CONNECT_STATE__` dari halaman embed TikTok."""
    match = _EMBED_STATE_RE.search(html or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _embed_node(state: dict, key: str) -> Optional[dict]:
    """
    Cari node data embed: kunci persis (`/embed/@u` atau `/embed/v2/<id>`), atau
    node pertama yang memuat `videoList`/`videoData`.
    """
    data = ((state or {}).get("source") or {}).get("data") or {}
    node = data.get(key)
    if isinstance(node, dict):
        return node
    for value in data.values():
        if isinstance(value, dict) and ("videoList" in value or value.get("videoData")):
            return value
    return None


def parse_embed_profile(html: str, unique_id: str) -> list[TikTokItem]:
    """
    Parse halaman embed profil (`/embed/@<user>`) menjadi daftar TikTokItem.

    Item `videoList`: id, desc, playAddr, coverUrl, width/height, playCount,
    privateItem. Postingan FOTO tidak punya `playAddr` → ditandai `kind='photo'`
    (daftar gambarnya dilengkapi saat diunduh). Tanpa `createTime`, `created_at`
    dibiarkan kosong dan diisi pemanggil dari detail postingan (hanya untuk
    postingan BARU, agar tidak ada request tambahan untuk yang sudah dikenal).
    """
    state = _embed_state(html)
    if state is None:
        return []
    node = _embed_node(state, f"/embed/@{unique_id}")
    if node is None:
        return []
    items: list[TikTokItem] = []
    for raw in node.get("videoList") or []:
        if not isinstance(raw, dict):
            continue
        post_id = str(raw.get("id") or "").strip()
        if not post_id:
            continue
        play_addr = str(raw.get("playAddr") or "").strip()
        items.append(
            TikTokItem(
                id=post_id,
                unique_id=str(raw.get("authorUniqueId") or unique_id).strip() or unique_id,
                kind="video" if play_addr else "photo",
                title=str(raw.get("desc") or "").strip(),
                video_url=play_addr,
                cover_url=str(
                    raw.get("coverUrl") or raw.get("originCoverUrl") or ""
                ).strip(),
                source_url=f"{TIKTOK_WEB_BASE}/@{unique_id}/video/{post_id}",
                raw=raw,
            )
        )
    return items


def parse_embed_post(html: str, post_id: str, unique_id: str) -> Optional[TikTokItem]:
    """
    Parse halaman embed satu postingan (`/embed/v2/<id>`).

    Sumber: `videoData.itemInfos` (createTime, video.urls, covers, videoMeta) dan
    `videoData.imagePostInfo.displayImages` (foto carousel berurutan).
    """
    state = _embed_state(html)
    if state is None:
        return None
    node = _embed_node(state, f"/embed/v2/{post_id}")
    if node is None:
        return None
    video_data = node.get("videoData") or {}
    infos = video_data.get("itemInfos") or {}
    video_block = (infos.get("video") or {}) if infos else {}
    video_urls = video_block.get("urls") or []
    images: list[str] = []
    for image in ((video_data.get("imagePostInfo") or {}).get("displayImages") or []):
        if not isinstance(image, dict):
            continue
        urls = image.get("urlList") or []
        if urls and urls[0]:
            images.append(str(urls[0]))
    covers = (infos.get("covers") or []) if infos else []
    duration_ms = (video_block.get("videoMeta") or {}).get("duration") or 0
    author = (video_data.get("authorInfos") or {}).get("uniqueId")

    return TikTokItem(
        id=str(infos.get("id") or post_id).strip() or post_id,
        unique_id=str(author or unique_id).strip() or unique_id,
        kind="photo" if images else "video",
        title="",                      # deskripsi tetap dari listing embed
        created_at=epoch_to_utc(infos.get("createTime")),
        duration_seconds=int(float(duration_ms) // 1000) if duration_ms else 0,
        images=images,
        video_url=str(video_urls[0]).strip() if video_urls else "",
        cover_url=str(covers[0]).strip() if covers else "",
        source_url=f"{TIKTOK_WEB_BASE}/@{unique_id}/video/{post_id}",
        raw=video_data,
    )


def extract_post_id(page_url: str) -> str:
    """Ambil ID postingan dari URL halaman/`/video/`/`/photo/` TikTok."""
    match = re.search(r"/(?:video|photo)/(\d{15,25})", page_url or "")
    if match:
        return match.group(1)
    match = re.search(r"(\d{15,25})", page_url or "")
    return match.group(1) if match else ""


async def fetch_embed_post_media(unique_id: str, post_id: str) -> dict:
    """
    Media segar satu postingan dari halaman embed (tanpa login, tanpa kuota tikwm).

    Dipakai sebagai cadangan saat: URL CDN dari tikwm kedaluwarsa (403), yt-dlp
    gagal, atau postingan foto belum punya daftar `images`.

    Returns:
        dict berisi `video_url`, `images`, `created_at`, `duration_seconds`,
        `cover_url` — kosong bila gagal.
    """
    if not post_id:
        return {}
    status, body = await http_get_text_retry(
        f"{TIKTOK_WEB_BASE}/embed/v2/{post_id}", timeout=30.0
    )
    if status != 200 or not body:
        return {}
    item = parse_embed_post(body, post_id, unique_id)
    if item is None:
        return {}
    return {
        "video_url": item.video_url,
        "images": item.images,
        "created_at": item.created_at,
        "duration_seconds": item.duration_seconds,
        "cover_url": item.cover_url,
    }


def build_providers(mode: Optional[str] = None) -> list[BaseProvider]:
    """
    Bangun daftar penyedia sesuai `Config.TIKTOK_PROVIDER`.

    Urutan = prioritas pemakaian. `auto`:
      1. `tikwm`  — JSON paling lengkap (foto + story via `/user/story`), tetapi
         Cloudflare bisa memblokir sebagian path/IP.
      2. `embed`  — halaman embed TikTok, paling andal untuk listing (≤10 post
         terbaru) walau tanpa story.
      3. `scrape` — halaman profil `/@user` langsung (regex `/video/<id>` +
         JSON rehidrasi); sering masih lolos saat embed/tikwm dijawab stub.
      4. `ytdlp`  — tanpa pihak ketiga; butuh secUid untuk listing profil.
    """
    choice = (mode or Config.TIKTOK_PROVIDER or "auto").lower().strip()
    limiter = RateLimiter(Config.TIKTOK_REQUEST_INTERVAL_SECONDS)
    if choice == "fixture":
        return [FixtureProvider(limiter)]
    if choice == "tikwm":
        return [TikwmProvider(limiter)]
    if choice == "embed":
        return [EmbedProvider(limiter)]
    if choice == "scrape":
        return [ScrapeProvider(limiter)]
    if choice == "ytdlp":
        return [YtDlpProvider(limiter)]
    if choice != "auto":
        logger.warning("TIKTOK_PROVIDER=%r tidak dikenal; memakai 'auto'.", choice)
    return [
        TikwmProvider(limiter),
        EmbedProvider(limiter),
        ScrapeProvider(limiter),
        YtDlpProvider(limiter),
    ]
