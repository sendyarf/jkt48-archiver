"""
showroom_monitor.py - Pemantauan status live room Showroom.

Tanggung jawab modul ini HANYA menjawab "apakah room ini sedang live?" dan
menyediakan URL HLS-nya. Ia tidak menyentuh database maupun merge manager,
sehingga dapat diuji tanpa jaringan dan tanpa efek samping.

Debounce sinyal offline
-----------------------
Panggilan API bisa gagal sesaat (timeout/5xx). Karena itu "room offline" hanya
dianggap SAH setelah OFFLINE_CONFIRMATIONS pembacaan offline BERTURUT-TURUT.

Prinsip penting: debounce hanya MEMPERLAMBAT pengakuan offline, tidak pernah
mempercepat. Selama belum terkonfirmasi, fungsi mengembalikan `None`
("tidak diketahui") — bukan `False`. Pemanggil (MergeManager) memperlakukan
`None` sebagai "jangan finalize dulu", sehingga satu hiccup API tidak pernah
memecah satu live menjadi beberapa video.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bot.showroom_scraper import ShowroomScraper
from bot.timeutil import utc_now

logger = logging.getLogger(__name__)

LIVE_ID_PREFIX = "sr_"


@dataclass
class RoomState:
    """Keadaan satu room pada satu pembacaan."""
    room_id: str
    is_onlive: bool
    live_id: int = 0
    room_url_key: str = ""
    room_name: str = ""
    cover_image: str = ""
    started_at: Optional[str] = None

    @property
    def namespace(self) -> str:
        """
        Penanda identitas room yang stabil untuk dipakai di `live_id`.

        `room_url_key` (mis. 'JKT48_Feni') adalah identitas stabil dari API.
        Bila kosong, jatuh ke `room_id` agar `live_id` tetap unik.
        """
        return self.room_url_key.strip() or self.room_id


def build_live_id(state: RoomState, epoch: Optional[int] = None) -> str:
    """
    Bangun live_id Showroom: `sr_<namespace>_<epoch>`.

    Prefix `sr_` wajib: kolom `live_id` UNIQUE dan format IDN adalah
    `<username>_<epoch>`, jadi tanpa prefix keduanya bisa bertabrakan.
    """
    moment = epoch if epoch is not None else int(utc_now().timestamp())
    return f"{LIVE_ID_PREFIX}{state.namespace}_{moment}"


def is_showroom_live_id(live_id: str) -> bool:
    """Apakah sebuah live_id berasal dari Showroom?"""
    return bool(live_id) and live_id.startswith(LIVE_ID_PREFIX)


@dataclass
class OfflineCounter:
    """Penghitung pembacaan offline berturut-turut untuk satu room."""
    count: int = 0
    last_error: str = ""


class ShowroomMonitor:
    """Probe status live room Showroom dengan debounce offline."""

    def __init__(
        self,
        scraper: Optional[ShowroomScraper] = None,
        offline_confirmations: int = 3,
        concurrency: int = 10,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.scraper = scraper or ShowroomScraper(timeout_seconds=timeout_seconds)
        # Minimal 1 pembacaan agar room yang memang offline tidak tertahan selamanya.
        self.offline_confirmations = max(1, int(offline_confirmations))
        self.concurrency = max(1, int(concurrency))
        self._offline: dict[str, OfflineCounter] = {}
        self._closed = False

    # ── Pembacaan mentah ────────────────────────────────────────────────────

    async def fetch_state(self, room_id: str) -> Optional[RoomState]:
        """
        Baca keadaan room dari API.

        Return None bila pembacaan GAGAL (timeout/HTTP error) — beda artinya
        dengan "room offline". Kegagalan tidak boleh dihitung sebagai bukti
        offline.
        """
        profile = await self.scraper.get_room_profile(room_id)
        if not profile:
            return None

        raw_name = profile.get("room_name") or profile.get("name") or ""
        cover = profile.get("image") or profile.get("room_image") or ""
        started_raw = profile.get("current_live_started_at")
        started: Optional[str] = None
        if started_raw:
            try:
                # API mengirim epoch detik.
                started = datetime.fromtimestamp(int(started_raw), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                started = None

        return RoomState(
            room_id=str(profile.get("room_id") or room_id),
            is_onlive=bool(profile.get("is_onlive")),
            live_id=int(profile.get("live_id") or 0),
            room_url_key=str(profile.get("room_url_key") or ""),
            room_name=str(raw_name),
            cover_image=str(cover),
            started_at=started,
        )

    # ── Pembacaan ber-debounce ──────────────────────────────────────────────

    async def is_room_live(self, room_id: str) -> Optional[bool]:
        """
        True  → terbukti live.
        False → terbukti offline (sudah OFFLINE_CONFIRMATIONS kali berturut-turut).
        None  → belum diketahui (pembacaan gagal, atau offline belum terkonfirmasi).

        `None` sengaja dipakai agar pemanggil tidak memfinalisasi lebih awal.
        """
        key = str(room_id)
        state = await self.fetch_state(key)

        if state is None:
            # Kegagalan pembacaan BUKAN bukti offline.
            counter = self._offline.setdefault(key, OfflineCounter())
            counter.last_error = "pembacaan gagal"
            return None

        if state.is_onlive:
            self._offline.pop(key, None)
            return True

        counter = self._offline.setdefault(key, OfflineCounter())
        counter.count += 1
        if counter.count >= self.offline_confirmations:
            return False
        logger.debug(
            "Room %s terbaca offline (%d/%d) — belum dikonfirmasi, diperlakukan 'tidak diketahui'",
            key, counter.count, self.offline_confirmations,
        )
        return None

    def offline_readings(self, room_id: str) -> int:
        """Jumlah pembacaan offline berturut-turut saat ini (diagnostik/tes)."""
        counter = self._offline.get(str(room_id))
        return counter.count if counter else 0

    def forget(self, room_id: str) -> None:
        """Lupakan riwayat debounce satu room (mis. setelah rekaman dimulai)."""
        self._offline.pop(str(room_id), None)

    # ── Pemindaian banyak room ──────────────────────────────────────────────

    async def find_live_rooms(self, rooms: list[dict]) -> list[dict]:
        """
        Pindai banyak room secara bersamaan dan kembalikan yang sedang live,
        lengkap dengan URL HLS.

        `rooms` = daftar dict berisi minimal `room_id` dan `username`.
        URL HLS hanya diminta untuk room yang benar-benar live, sehingga jumlah
        request tetap hemat.
        """
        if not rooms:
            return []

        semaphore = asyncio.Semaphore(self.concurrency)

        async def probe(room: dict) -> Optional[dict]:
            async with semaphore:
                room_id = str(room.get("room_id") or "")
                if not room_id:
                    return None
                state = await self.fetch_state(room_id)
                if state is None or not state.is_onlive:
                    return None
                hls_url = await self.scraper.get_live_streaming_url(room_id)
                if not hls_url:
                    logger.warning(
                        "Room %s (%s) melaporkan live tetapi URL HLS tidak tersedia",
                        room_id, room.get("name") or room.get("username"),
                    )
                    return None
                self._offline.pop(room_id, None)
                return {
                    "username": room.get("username") or "",
                    "display_name": (
                        room.get("display_name")
                        or room.get("name")
                        or room.get("username")
                        or ""
                    ),
                    "room_id": room_id,
                    "hls_url": hls_url,
                    "room_url_key": state.room_url_key,
                    "room_name": state.room_name,
                    "cover_image": state.cover_image,
                    "started_at": state.started_at,
                    "showroom_live_id": state.live_id,
                }

        results = await asyncio.gather(*(probe(r) for r in rooms), return_exceptions=True)
        live: list[dict] = []
        for item in results:
            if isinstance(item, BaseException):
                logger.debug("Probe Showroom gagal (diabaikan): %s", item)
                continue
            if item:
                live.append(item)
        return live

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.scraper.close()
        except Exception as exc:  # pragma: no cover - penutupan best-effort
            logger.debug("Gagal menutup klien Showroom: %s", exc)
