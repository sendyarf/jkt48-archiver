"""
showroom_scraper.py - Showroom Live detection and stream URL resolver for JKT48.

Interacts with Showroom Live public API to:
1. Check if a room is broadcasting live (`is_on_live`).
2. Retrieve the active HLS streaming URL (`/api/live/streaming_url`).
3. Extract room profile metadata (room name, description, avatar, started_at).
"""
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

SHOWROOM_API_BASE = "https://www.showroom-live.com/api"
SHOWROOM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class ShowroomScraper:
    """Handles interaction with Showroom Live API."""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers=SHOWROOM_HEADERS,
                follow_redirects=True,
            )
        return self._client

    async def get_room_profile(self, room_id: int | str) -> Optional[dict]:
        """Fetch room details including live status and room name."""
        client = self._get_client()
        url = f"{SHOWROOM_API_BASE}/room/profile?room_id={room_id}"
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Showroom profile returned HTTP %d for room %s", resp.status_code, room_id)
            return None
        except Exception as exc:
            logger.debug("Error fetching Showroom profile for %s: %s", room_id, exc)
            return None

    async def get_live_streaming_url(self, room_id: int | str) -> Optional[str]:
        """
        Fetch the active HLS stream URL for a live room.
        Returns the original/highest quality HLS m3u8 URL if live, else None.
        """
        client = self._get_client()
        url = f"{SHOWROOM_API_BASE}/live/streaming_url?room_id={room_id}"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            data = resp.json()
            streams = data.get("streaming_url_list", [])
            if not streams:
                return None

            # Look for default or original HLS
            for s in streams:
                if s.get("type") == "hls" and s.get("is_default"):
                    return s.get("url")

            # Fallback to first HLS stream
            for s in streams:
                if s.get("type") == "hls" and s.get("url"):
                    return s.get("url")

            return streams[0].get("url") if streams else None
        except Exception as exc:
            logger.debug("Error fetching Showroom stream URL for %s: %s", room_id, exc)
            return None

    async def is_room_live(self, room_id: int | str) -> bool:
        """Quick check if room currently has active streaming URLs."""
        url = await self.get_live_streaming_url(room_id)
        return bool(url)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
