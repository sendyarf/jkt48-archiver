"""
hls_monitor.py - Monitor permanent HLS stream status directly via HTTP requests.

Since member HLS URLs (AWS IVS) are permanent and static, checking whether
a member is live simply requires testing their HLS master playlist URL (.m3u8).
If the endpoint returns HTTP 200 with '#EXTM3U', the stream is broadcasting live.
If 404 or network timeout, the stream is offline.
"""
import asyncio
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

HLS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


class HLSMonitor:
    """Monitors live status of members by probing their permanent HLS URLs."""

    def __init__(self, concurrency_limit: int = 20, timeout_seconds: float = 6.0) -> None:
        self._concurrency_limit = concurrency_limit
        self._timeout_seconds = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(concurrency_limit)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                headers=HLS_HEADERS,
                follow_redirects=True,
            )
        return self._client

    async def is_stream_active(self, hls_url: str) -> bool:
        """
        Check if an HLS URL is currently broadcasting.
        Returns True if HTTP 200 and content starts with '#EXTM3U'.
        """
        if not hls_url:
            return False

        async with self._semaphore:
            client = self._get_client()
            try:
                resp = await client.get(hls_url)
                if resp.status_code == 200:
                    text = resp.text[:100]
                    if "#EXTM3U" in text:
                        return True
                return False
            except httpx.RequestError:
                return False
            except Exception as e:
                logger.debug("HLS probe error for %s: %s", hls_url, e)
                return False

    async def check_active_members(self, members: list[dict]) -> list[dict]:
        """
        Probe a list of members concurrently and return those currently live.
        Each member dict is expected to have 'username', 'display_name', 'hls_url'.
        """
        members_with_hls = [m for m in members if m.get("hls_url")]
        if not members_with_hls:
            return []

        tasks = [self.is_stream_active(m["hls_url"]) for m in members_with_hls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        active: list[dict] = []
        for m, is_active in zip(members_with_hls, results):
            if is_active is True:
                active.append(m)

        return active

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
