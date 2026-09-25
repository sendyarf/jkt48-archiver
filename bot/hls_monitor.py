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
    # Do not let a regional CloudFront edge retain a stale 404 response.
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# A single probe can land on a CloudFront POP which briefly returns 404 while
# another POP is healthy.  Retry only transport/transient statuses; a normal
# offline response still fails quickly.
_RETRYABLE_HTTP_STATUSES = frozenset((403, 404, 408, 425, 429, 500, 502, 503, 504))


class HLSMonitor:
    """Monitors live status of members by probing their permanent HLS URLs."""

    def __init__(
        self,
        concurrency_limit: int = 20,
        timeout_seconds: float = 6.0,
        probe_attempts: int = 2,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        self._concurrency_limit = concurrency_limit
        self._timeout_seconds = timeout_seconds
        self._probe_attempts = max(1, int(probe_attempts))
        self._retry_delay_seconds = max(0.0, float(retry_delay_seconds))
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
            last_error = ""
            for attempt in range(1, self._probe_attempts + 1):
                try:
                    resp = await client.get(hls_url)
                    if resp.status_code == 200:
                        text = resp.text[:200].lstrip()
                        if "#EXTM3U" in text:
                            return True
                        last_error = f"HTTP 200 tanpa #EXTM3U: {text[:80]!r}"
                    else:
                        last_error = f"HTTP {resp.status_code}"
                    if (
                        attempt >= self._probe_attempts
                        or resp.status_code not in _RETRYABLE_HTTP_STATUSES
                    ):
                        return False
                except httpx.RequestError as exc:
                    last_error = repr(exc)
                    if attempt >= self._probe_attempts:
                        return False
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("HLS probe error for %s: %s", hls_url, exc)
                    return False

                await asyncio.sleep(self._retry_delay_seconds)

            logger.debug("HLS probe failed for %s (%s)", hls_url, last_error)
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
