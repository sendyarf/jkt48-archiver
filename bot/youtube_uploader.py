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

from bot.config import Config
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

    def set_thumbnail(
        self,
        video_id: str,
        thumbnail_path: str | Path,
        channel_label: Optional[str] = None,
    ) -> bool:
        """
        Pasang thumbnail custom ke video YouTube (~50 unit kuota).

        Bila `channel_label` diisi, hanya channel itu yang dicoba (channel
        tempat video diupload). Bila None, semua channel dicoba berurutan.
        Gagal di semua channel -> False (upload TETAP dianggap sukses;
        video memakai thumbnail otomatis YouTube).
        """
        path = Path(thumbnail_path)
        if not video_id or not path.exists() or path.stat().st_size == 0:
            logger.warning("set_thumbnail dilewati: video_id/thumbnail tidak valid.")
            return False

        channels = Config.load_youtube_channels()
        if channel_label:
            channels = [c for c in channels if c.label == channel_label] or channels
        if not channels:
            logger.warning("set_thumbnail dilewati: tidak ada channel YouTube untuk %s.", video_id)
            return False

        # MediaFileUpload dibuat PER percobaan: stream bisa ter-consume saat
        # request pertama gagal di tengah jalan, sehingga channel berikutnya
        # menerima body kosong dan thumbnail tidak pernah terpasang.
        last_err = ""
        for ch in channels:
            try:
                media = MediaFileUpload(str(path), mimetype="image/jpeg", resumable=False)
                service = self._get_service_for_channel(ch.token_file, ch.secret_file)
                service.thumbnails().set(
                    videoId=video_id, media_body=media
                ).execute()
                logger.info(
                    "Thumbnail terpasang ke video %s via channel '%s'.",
                    video_id, ch.label,
                )
                return True
            except HttpError as exc:
                last_err = str(exc)
                logger.warning(
                    "set_thumbnail gagal via channel '%s' (%s); coba channel lain.",
                    ch.label, exc,
                )
                continue
            except Exception as exc:
                last_err = str(exc)
                logger.warning(
                    "set_thumbnail error via channel '%s' (%s); coba channel lain.",
                    ch.label, exc,
                )
                continue
        logger.warning(
            "set_thumbnail gagal di semua channel untuk video %s%s",
            video_id, f" (terakhir: {last_err})" if last_err else "",
        )
        return False

    # Bulan Indonesia — judul YouTube dibuat identik dengan format judul website
    # (web/lib/wib.ts buildDisplayTitle): "LIVE IDN NAMA - 15 September 2026 | 16:37 WIB".
    _ID_MONTHS = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
        7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
    }

    @staticmethod
    def build_title(
        member_name: str,
        started_at: Optional[str] = None,
        platform: str = "idn",
    ) -> str:
        """
        Build YouTube video title, identik dengan judul di website:
        Format: 'LIVE IDN NAMA - 18 September 2026 | 20:18 WIB'
                'LIVE SHOWROOM NAMA - 18 September 2026 | 20:18 WIB'

        Jam ditampilkan di zona waktu penonton (DISPLAY_TIMEZONE_OFFSET_HOURS)
        dengan label (DISPLAY_TIMEZONE_LABEL), bukan waktu dinding server.
        Nama member di-HURUF KAPITAL seperti versi web agar keduanya serasi.
        """
        plat_label = "SHOWROOM" if platform == "showroom" else "IDN"
        name = (member_name or "").strip().upper() or "JKT48"

        converted = timeutil.to_display(
            started_at,
            Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
            Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
        )
        if converted is not None:
            date_part = (
                f"{converted.day} "
                f"{YouTubeChannelPool._ID_MONTHS[converted.month]} {converted.year}"
            )
            time_part = converted.strftime("%H:%M")
            title = (
                f"LIVE {plat_label} {name} - {date_part} | "
                f"{time_part} {Config.DISPLAY_TIMEZONE_LABEL}"
            )
        else:
            # Nilai waktu tidak terbaca → sama seperti fallback website:
            # judul tanpa tanggal.
            title = f"LIVE {plat_label} {name}"

        # YouTube title limit is 100 characters
        return title[:100]

    @staticmethod
    def build_description(
        member_name: str,
        member_username: str,
        started_at: str,
        platform: str = "idn",
    ) -> str:
        source = "Showroom Live" if platform == "showroom" else "IDN Live"
        tags = "#JKT48 #ShowroomLive" if platform == "showroom" else "#JKT48 #IDNLive"
        return (
            f"JKT48 Live Recording\n"
            f"Member: {member_name} (@{member_username})\n"
            f"Live at: {started_at}\n\n"
            f"Recorded from {source}.\n"
            f"{tags}"
        )

    @staticmethod
    def build_tiktok_title(
        member_name: str,
        posted_at: Optional[str] = None,
        kind: str = "video",
        is_story: bool = False,
    ) -> str:
        """
        Judul YouTube untuk arsip TikTok.

        Format: 'TIKTOK VIDEO NAMA - 21 September 2026 | 17:44 WIB'
                'TIKTOK FOTO NAMA - 21 September 2026 | 17:44 WIB'
                'TIKTOK STORY NAMA - 21 September 2026 | 17:44 WIB'

        Waktu memakai zona tampilan (DISPLAY_TIMEZONE_*) seperti judul replay,
        nama member di-HURUF KAPITAL, dan hasilnya dipotong ke batas 100 karakter
        YouTube.
        """
        if is_story:
            label = "TIKTOK STORY"
        elif (kind or "video").lower() == "photo":
            label = "TIKTOK FOTO"
        else:
            label = "TIKTOK VIDEO"
        name = (member_name or "").strip().upper() or "JKT48"

        converted = timeutil.to_display(
            posted_at,
            Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
            Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
        )
        if converted is not None:
            date_part = (
                f"{converted.day} "
                f"{YouTubeChannelPool._ID_MONTHS[converted.month]} {converted.year}"
            )
            title = (
                f"{label} {name} - {date_part} | "
                f"{converted.strftime('%H:%M')} {Config.DISPLAY_TIMEZONE_LABEL}"
            )
        else:
            title = f"{label} {name}"
        return title[:100]

    @staticmethod
    def build_tiktok_description(
        member_name: str,
        unique_id: str,
        posted_at: str = "",
        kind: str = "video",
        is_story: bool = False,
        image_count: int = 0,
        source_url: str = "",
        caption_text: str = "",
    ) -> str:
        """Deskripsi YouTube untuk arsip TikTok (video, foto→slide show, story)."""
        if is_story:
            media_note = "TikTok Story (arsip video)"
        elif (kind or "video").lower() == "photo":
            media_note = f"Postingan foto TikTok ({image_count} foto, dibuat slide show)"
        else:
            media_note = "Video TikTok"

        tags = "#JKT48 #TikTokStory" if is_story else "#JKT48 #TikTok"
        lines = [
            "JKT48 TikTok Archive",
            f"Member: {member_name} (@{unique_id})",
        ]
        if posted_at:
            lines.append(f"Posted at: {posted_at}")
        lines.append(f"Source: {media_note}")
        if caption_text:
            lines.append("")
            lines.append(caption_text)
        if source_url:
            lines.append("")
            lines.append(source_url)
        lines.append("")
        lines.append(tags)
        return "\n".join(lines)

