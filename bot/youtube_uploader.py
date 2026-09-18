"""
youtube_uploader.py - Multi-Channel YouTube Data API v3 uploader for JKT48 Live Bot.

Distributes uploads across multiple YouTube channels to avoid the 10,000 unit/day quota limit.
If all channels hit quota limits, marks the upload as pending so it can be retried after quota reset.
"""
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from bot.config import Config, ChannelConfig
from bot import database
from bot import timeutil

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"


class YouTubeQuotaExceeded(Exception):
    """Raised when all available YouTube channels have hit their upload quota."""


class YouTubeChannelPool:
    """Manages YouTube client authentication and round-robin upload across channels."""

    def __init__(self) -> None:
        self._services: dict[str, any] = {}  # token_file -> service client
        self._sync_channels_with_db()

    def _sync_channels_with_db(self) -> None:
        """Sync channels defined in config/env to the database."""
        channels = Config.load_youtube_channels()
        db_entries = [
            {
                "channel_label": ch.label,
                "token_file": ch.token_file,
                "secret_file": ch.secret_file,
            }
            for ch in channels
        ]
        database.sync_youtube_channels(db_entries)

    def _get_service_for_channel(self, token_file: str, secret_file: str):
        """Build or retrieve cached YouTube service for a specific channel credentials file."""
        if token_file in self._services:
            return self._services[token_file]

        if not os.path.exists(token_file):
            raise FileNotFoundError(
                f"OAuth token file not found: {token_file}. "
                f"Run `python -m bot.auth_youtube` to authenticate."
            )

        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired YouTube OAuth token for %s", token_file)
                creds.refresh(Request())
                with open(token_file, "w") as tf:
                    tf.write(creds.to_json())
            else:
                raise RuntimeError(
                    f"Invalid YouTube token in {token_file}. Re-run auth_youtube.py"
                )

        service = build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=creds)
        self._services[token_file] = service
        return service

    def upload_video(
        self,
        file_path: str | Path,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Upload a video file to YouTube as unlisted using the best available channel.
        Tries channels with fewest uploads first, falling back to next if quota exceeded.

        Returns:
            (video_id, channel_label) if successful.
            (None, None) if failed permanently or all quotas exhausted.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("YouTube upload: file not found: %s", path)
            return None, None

        size_mb = path.stat().st_size / (1024 * 1024)
        logger.info("Starting YouTube upload (%.1f MB) for '%s'", size_mb, title)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags or ["JKT48", "IDN Live", "live"],
                "categoryId": "24",  # Entertainment
            },
            "status": {
                "privacyStatus": "unlisted",
                "selfDeclaredMadeForKids": False,
            },
        }

        channels = database.get_youtube_channels()
        if not channels:
            logger.error("No YouTube channels available in database.")
            return None, None

        all_quota_exceeded = True

        for ch in channels:
            ch_id = ch["id"]
            token_file = ch["token_file"]
            secret_file = ch["secret_file"]
            label = ch["channel_label"]

            logger.info("Trying YouTube channel '%s' (uploads today: %d)", label, ch["uploads_today"])

            try:
                service = self._get_service_for_channel(token_file, secret_file)
            except Exception as e:
                logger.warning("Could not authenticate channel '%s' (%s): %s. Trying next channel...",
                               label, token_file, e)
                all_quota_exceeded = False
                continue

            media = MediaFileUpload(
                str(path),
                mimetype="video/mp4",
                chunksize=50 * 1024 * 1024,
                resumable=True,
            )

            try:
                request = service.videos().insert(
                    part=",".join(body.keys()),
                    body=body,
                    media_body=media,
                )

                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        pct = int(status.progress() * 100)
                        if pct % 20 == 0:
                            logger.info("[%s] Upload progress: %d%%", label, pct)

                video_id = response.get("id")
                if video_id:
                    logger.info("Upload complete to [%s]. Video ID: %s | URL: https://youtu.be/%s",
                                label, video_id, video_id)
                    database.increment_channel_uploads(ch_id)
                    return video_id, label

            except HttpError as exc:
                reason = str(exc)
                if "quotaExceeded" in reason or "uploadLimitExceeded" in reason or exc.resp.status in (403, 429):
                    logger.warning("Quota exceeded for channel '%s'. Trying next channel...", label)
                    continue
                else:
                    logger.error("HTTP error uploading to channel '%s': %s", label, exc)
                    all_quota_exceeded = False
                    continue
            except Exception as exc:
                logger.error("Unexpected error on channel '%s': %s", label, exc)
                all_quota_exceeded = False
                continue

        if all_quota_exceeded:
            raise YouTubeQuotaExceeded("All configured YouTube channels have exceeded their quota!")

        return None, None

    @staticmethod
    def build_title(member_name: str, started_at: Optional[str] = None) -> str:
        """
        Build compact YouTube video title:
        Format: 'IDN LIVE [display_name] [DD-MM-YYYY HH:MM]'

        Jam ditampilkan di zona waktu penonton (DISPLAY_TIMEZONE_OFFSET_HOURS),
        bukan waktu dinding server — VPS bisa berjalan di UTC+9 (Seoul).
        """
        fmt = "%d-%m-%Y %H:%M"
        dt_str = ""
        if started_at:
            dt_str = timeutil.format_display(
                started_at,
                fmt,
                Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
                Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
            )
        if not dt_str:
            dt_str = timeutil.format_display(
                timeutil.utc_now_iso(),
                fmt,
                Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
                Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
            )

        name = member_name.strip() if member_name else "JKT48"
        title = f"IDN LIVE {name} {dt_str}"
        # YouTube title limit is 100 characters
        return title[:100]

    @staticmethod
    def build_description(member_name: str, member_username: str, started_at: str) -> str:
        return (
            f"JKT48 Live Recording\n"
            f"Member: {member_name} (@{member_username})\n"
            f"Live at: {started_at}\n\n"
            f"Recorded from IDN Live.\n"
            f"#JKT48 #IDNLive"
        )
