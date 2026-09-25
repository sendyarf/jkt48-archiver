"""
hls_discovery.py - Discover permanent HLS URLs for new members via IDN GraphQL.

HLS URLs are stored in SQLite, but playback URLs can change for a
username.  This module discovers missing URLs and best-effort refreshes known
URLs from the active IDN feed; failures leave the previous URL untouched.
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
    """Discover missing HLS URLs and refresh known active-member URLs."""

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
            playback_url = (live.get("playback_url") or "").strip()
            display_name = creator.get("name") or username
            if not username or not playback_url:
                continue

            known = database.get_member_hls(username)
            if not known or not known.get("enabled", 1):
                continue
            # Refresh URL playback even for a member that was seeded before.
            # AWS IVS/IDN can rotate the playback host/channel while a member
            # keeps the same public username.  A stale 404 must not permanently
            # exclude that member from recording.
            old_url = (known.get("hls_url") or "").strip()
            if old_url != playback_url:
                database.update_member_hls_url(username, playback_url, display_name)
                logger.warning(
                    "HLS URL IDN untuk '%s' berubah: %s → %s",
                    username,
                    old_url or "(kosong)",
                    playback_url,
                )

            if username in missing_usernames:
                logger.info("Discovered HLS URL for '%s' (%s): %s", username, display_name, playback_url)
                discovered.append({
                    "username": username,
                    "display_name": display_name,
                    "hls_url": playback_url,
                })

        return discovered

    async def refresh_known_members(self) -> list[dict]:
        """Refresh stored IDN playback URLs for currently active members.

        HLS URLs seeded from an earlier session can become stale when IDN
        rotates the playback host/channel.  Refreshing them from the active
        IDN feed prevents a single 404 from permanently disabling recording.
        The operation is best-effort: an IDN outage simply returns an empty
        list and leaves the known URLs untouched.
        """
        known_members = {
            (m.get("username") or "").lower(): m
            for m in database.get_all_member_hls(include_disabled=False)
            if (m.get("username") or "").strip()
            and not m.get("showroom_only", 0)
        }
        if not known_members:
            return []

        active_lives = await self.fetch_active_lives()
        refreshed: list[dict] = []
        for live in active_lives:
            creator = live.get("creator") or {}
            username = (creator.get("username") or "").lower().strip()
            playback_url = (live.get("playback_url") or "").strip()
            if username not in known_members or not playback_url:
                continue
            old_url = (known_members[username].get("hls_url") or "").strip()
            if old_url == playback_url:
                continue
            display_name = creator.get("name") or known_members[username].get("display_name") or username
            database.update_member_hls_url(username, playback_url, display_name)
            logger.warning(
                "HLS URL IDN untuk '%s' berubah: %s → %s",
                username,
                old_url or "(kosong)",
                playback_url,
            )
            refreshed.append({
                "username": username,
                "display_name": display_name,
                "hls_url": playback_url,
            })
        return refreshed

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
