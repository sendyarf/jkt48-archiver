"""
hls_discovery.py - Discover permanent HLS URLs for new members via IDN GraphQL.

This module is ONLY invoked when there are members in the database whose
HLS URLs have not yet been recorded (hls_url is NULL).
Once an HLS URL is discovered for a member, it is stored permanently in SQLite,
and IDN GraphQL will no longer be queried for that member.
"""
import asyncio
import logging
from typing import Optional

import httpx

from bot import database

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.idn.app/graphql"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
}

QUERY_LIVESTREAMS = """
query GetLivestream($category: String, $page: Int){
  getLivestreams(category: $category, page: $page){
    slug
    title
    image_url
    playback_url
    status
    live_at
    creator { name username uuid }
  }
}
"""


class HLSDiscovery:
    """Discovers HLS playback URLs for members with missing HLS entries."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20, headers=HEADERS)
        return self._client

    async def fetch_active_lives(self) -> list[dict]:
        """Fetch all currently active live streams from IDN public GraphQL."""
        client = self._get_client()
        all_lives: list[dict] = []
        page = 1

        while True:
            payload = {
                "query": QUERY_LIVESTREAMS,
                "variables": {"category": "all", "page": page},
                "operationName": "GetLivestream",
            }
            try:
                resp = await client.post(GRAPHQL_URL, json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                lives = data.get("getLivestreams") or []
                if not lives:
                    break
                all_lives.extend(lives)
                page += 1
            except Exception as e:
                logger.warning("Error fetching IDN GraphQL page %d for HLS discovery: %s", page, e)
                break

        return all_lives

    async def discover_missing_members(self) -> list[dict]:
        """
        Check database for members without HLS. If any exist, fetch IDN GraphQL
        and update their HLS URL if they are currently live.

        Returns a list of dicts for members newly discovered:
        [{"username": ..., "display_name": ..., "hls_url": ...}, ...]
        """
        missing_members = database.get_members_without_hls()
        if not missing_members:
            return []

        missing_usernames = {m["username"].lower(): m for m in missing_members}
        logger.debug("Checking IDN GraphQL to discover HLS for %d members: %s",
                    len(missing_usernames), ", ".join(missing_usernames.keys()))

        active_lives = await self.fetch_active_lives()
        discovered: list[dict] = []

        for live in active_lives:
            creator = live.get("creator") or {}
            username = (creator.get("username") or "").lower()
            playback_url = live.get("playback_url") or ""
            display_name = creator.get("name") or username

            if username in missing_usernames and playback_url:
                database.update_member_hls_url(username, playback_url, display_name)
                logger.info("Discovered HLS URL for '%s' (%s): %s", username, display_name, playback_url)
                discovered.append({
                    "username": username,
                    "display_name": display_name,
                    "hls_url": playback_url,
                })

        return discovered

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
