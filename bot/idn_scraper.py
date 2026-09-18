"""
idn_scraper.py - IDN Live public GraphQL scraper (no login required).

Workaround for IDN login issues. Instead of authenticating via the private
API, we scrape the public GraphQL endpoint used by the homepage at
https://www.idn.app/.  No email/password needed — the endpoint returns
all active live streams including playback URLs.

Queries reverse-engineered from the IDN web frontend (2025-07):
  - getLivestreams(category, page)  → paginated list of active streams
  - getLivestreamHeadline           → featured/headline streams

NOTE: The IDN GraphQL API is frequently flaky (network errors / HTTP 500).
The whole scrape is retried with a short backoff so one transient failure does
not produce an empty poll and cause us to miss/stop recording lives.
"""
import asyncio
import logging
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

# Number of full-scrape attempts before giving up on a poll (the API is flaky).
MAX_SCRAPE_ATTEMPTS = 3

QUERY_LIVESTREAMS = """
query GetLivestream($category: String, $page: Int){
  getLivestreams(category: $category, page: $page){
    slug
    title
    image_url
    view_count
    playback_url
    room_identifier
    status
    scheduled_at
    live_at
    live_type
    category { name slug }
    creator { name username uuid }
  }
}
"""

QUERY_HEADLINE = """
{
  getLivestreamHeadline{
    title
    slug
    image_url
    playback_url
    status
    live_at
    scheduled_at
    category { name slug }
    creator { name username uuid }
  }
}
"""


class IDNScraper:
    """
    Scrape IDN live streams from the public GraphQL endpoint.

    No authentication required.  Behaves like the IDN homepage.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30, headers=HEADERS)

    async def get_all_lives(self) -> list[dict]:
        """
        Fetch ALL active live streams by paginating through getLivestreams.

        Retries the whole pagination a few times when the API errors, so a
        transient 500/network failure does not yield an empty (missed) poll.

        Returns a list of live dicts with the same keys as the GraphQL response:
          - slug, title, playback_url, status, live_at, ...
          - creator: { name, username, uuid }
        """
        all_lives: list[dict] = []
        for attempt in range(1, MAX_SCRAPE_ATTEMPTS + 1):
            page = 1
            attempt_lives: list[dict] = []
            had_error = False

            while True:
                lives, error = await self._fetch_page(page)
                if error:
                    had_error = True
                    break
                if not lives:
                    break  # end of pagination
                attempt_lives.extend(lives)
                page += 1

            if attempt_lives:
                all_lives = attempt_lives
                break
            if had_error:
                logger.warning(
                    "GraphQL scrape attempt %d/%d failed (transient error)",
                    attempt, MAX_SCRAPE_ATTEMPTS,
                )
                if attempt < MAX_SCRAPE_ATTEMPTS:
                    await asyncio.sleep(3 * attempt)  # backoff
            else:
                # Clean empty result — genuinely no active lives right now.
                break

        logger.info("Scraped %d active live streams from IDN homepage", len(all_lives))
        return all_lives

    async def _fetch_page(self, page: int) -> tuple[list, bool]:
        """Fetch one page of getLivestreams.

        Returns (lives, is_error). is_error=True on HTTP/network failure so the
        caller can retry; otherwise lives is the list (empty = no more pages).
        """
        payload = {
            "query": QUERY_LIVESTREAMS,
            "variables": {"category": "all", "page": page},
            "operationName": "GetLivestream",
        }
        try:
            resp = await self._client.post(GRAPHQL_URL, json=payload)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("GraphQL page %d HTTP error: %s", page, exc)
            return [], True
        except Exception as exc:
            logger.warning("GraphQL page %d error: %s", page, exc)
            return [], True

        data = body.get("data") or {}
        lives = data.get("getLivestreams") or []
        return lives, False

    async def get_jkt48_lives(self) -> list[dict]:
        """
        Filter get_all_lives() to return only JKT48 member streams.

        Behaviour:
        - If members.txt has entries  → only return those specific members
        - If members.txt is empty     → return ALL members whose username
                                        starts with 'jkt48'
        """
        jkt48 = self._collect_jkt48(await self.get_all_lives())

        if jkt48:
            names = [l["_member_name"] for l in jkt48]
            logger.info("Found %d JKT48 live(s): %s", len(jkt48), ", ".join(names))
        else:
            logger.debug("No monitored JKT48 members currently live.")
        return jkt48

    @staticmethod
    def _collect_jkt48(lives: list[dict]) -> list[dict]:
        """Normalise raw GraphQL lives into _-prefixed dicts, filter to
        whitelisted / JKT48 members, and dedup by live_id / slug."""
        whitelist = Config.load_members()
        out: list[dict] = []
        for live in lives:
            creator = live.get("creator") or {}
            username = (
                live.get("_member_username")
                or creator.get("username")
                or ""
            ).lower()
            if not username:
                continue

            if whitelist:
                if username not in whitelist:
                    continue
            else:
                if not username.startswith("jkt48"):
                    continue

            item = {
                "_member_username": username,
                "_member_name": live.get("_member_name")
                or creator.get("name")
                or username,
                "_stream_url": live.get("_stream_url")
                or live.get("playback_url")
                or "",
                "_live_id": live.get("_live_id") or live.get("slug") or "",
                "_started_at": live.get("_started_at")
                or live.get("live_at")
                or live.get("scheduled_at")
                or "",
                "_thumbnail_url": live.get("_thumbnail_url")
                or live.get("image_url")
                or "",
                "_title": live.get("_title") or live.get("title") or "",
            }
            if item["_stream_url"] and item["_live_id"]:
                out.append(item)

        seen: set[str] = set()
        unique: list[dict] = []
        for x in out:
            if x["_live_id"] in seen:
                continue
            seen.add(x["_live_id"])
            unique.append(x)
        return unique

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
