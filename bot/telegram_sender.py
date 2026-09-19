"""
telegram_sender.py - Telegram notifier and video uploader using Telethon (userbot).

Supports sending:
1. Direct video uploads to Telegram channels with streaming support,
   automatic splitting for files > 2GB (safe limit default: 1950 MB),
   and video thumbnail preview.
2. Text notifications with YouTube links (when UPLOAD_TARGET=youtube).
"""
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional, Union

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo

from bot.config import Config
from bot import timeutil
from bot.video_splitter import (
    VideoPart,
    cleanup_video_parts,
    generate_thumbnail,
    get_video_metadata,
    split_video_if_needed,
)

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    if not size_bytes:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


class TelegramSender:
    """
    Handles text notifications and video uploads to Telegram channels via Telethon userbot.
    """

    def __init__(self) -> None:
        if Config.TELEGRAM_SESSION_STRING:
            session = StringSession(Config.TELEGRAM_SESSION_STRING)
        else:
            session = "jkt48_live_bot"

        self._client = TelegramClient(
            session,
            Config.TELEGRAM_API_ID,
            Config.TELEGRAM_API_HASH,
        )
        self._connected = False

    async def connect(self) -> None:
        """Connect and authenticate with Telegram."""
        if self._connected:
            return
        if Config.TELEGRAM_SESSION_STRING:
            await self._client.start()  # type: ignore
        else:
            await self._client.start(phone=Config.TELEGRAM_PHONE)  # type: ignore
        self._connected = True
        me = await self._client.get_me()
        logger.info("Telegram connected as: %s (@%s)", me.first_name, me.username)

    async def send_message(
        self,
        text: str,
        channel_id: Optional[int] = None,
        link_preview: bool = True,
    ) -> Optional[int]:
        """Send an HTML-formatted message to the Telegram channel."""
        await self.connect()
        target = channel_id or Config.TELEGRAM_CHANNEL_ID
        try:
            msg = await self._client.send_message(
                target,
                text,
                parse_mode="html",
                link_preview=link_preview,
            )
            logger.info("Telegram notification sent to %d. Message ID: %d", target, msg.id)
            return msg.id
        except FloodWaitError as exc:
            logger.warning("Telegram FloodWait %ds. Retrying...", exc.seconds)
            await asyncio.sleep(exc.seconds + 5)
            msg = await self._client.send_message(
                target,
                text,
                parse_mode="html",
                link_preview=link_preview,
            )
            return msg.id
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return None

    async def send_video_file(
        self,
        file_path: Union[str, Path],
        caption: str = "",
        duration: float = 0.0,
        width: int = 0,
        height: int = 0,
        thumb_path: Optional[Union[str, Path]] = None,
        channel_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[int]:
        """
        Upload a single video file to Telegram with video streaming attributes.
        """
        await self.connect()
        target = channel_id or Config.TELEGRAM_CHANNEL_ID
        path = Path(file_path).resolve()

        if not path.exists():
            logger.error("send_video_file: file does not exist: %s", path)
            return None

        # Build video streaming attributes so Telegram client renders it as a playable video
        attributes = [
            DocumentAttributeVideo(
                duration=int(duration or 0),
                w=int(width or 0),
                h=int(height or 0),
                supports_streaming=True,
            )
        ]

        thumb = str(thumb_path) if thumb_path and Path(thumb_path).exists() else None

        last_log_time = 0.0

        def _default_progress(current: int, total: int) -> None:
            nonlocal last_log_time
            now = time.time()
            if progress_callback:
                progress_callback(current, total)
            if now - last_log_time >= 15 or current >= total:
                percent = (current / total * 100) if total > 0 else 0
                logger.info(
                    "Uploading %s: %.1f%% (%s / %s)",
                    path.name,
                    percent,
                    _format_size(current),
                    _format_size(total),
                )
                last_log_time = now

        for attempt in range(3):
            try:
                msg = await self._client.send_file(
                    target,
                    file=str(path),
                    caption=caption,
                    parse_mode="html",
                    attributes=attributes,
                    thumb=thumb,
                    supports_streaming=True,
                    progress_callback=_default_progress,
                )
                logger.info("Successfully uploaded video %s to %d (Message ID: %d)", path.name, target, msg.id)
                return msg.id

            except FloodWaitError as exc:
                logger.warning("Telegram FloodWait %ds during upload. Waiting...", exc.seconds)
                await asyncio.sleep(exc.seconds + 5)
            except Exception as exc:
                logger.exception("Upload error for %s (attempt %d/3): %s", path.name, attempt + 1, exc)
                if attempt == 2:
                    return None
                await asyncio.sleep(5)

        return None

    async def upload_video_with_splitting(
        self,
        file_path: Union[str, Path],
        member_name: str,
        member_username: str,
        started_at: str,
        live_title: str = "",
        channel_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        platform: str = "",
    ) -> list[int]:
        """
        Splits video if > 2GB (TELEGRAM_MAX_FILE_SIZE_MB) and uploads all parts
        sequentially to the Telegram channel.

        `platform` ('idn'/'showroom') dipakai untuk header caption agar rekaman
        Showroom tidak lagi dilabeli "IDN LIVE REPLAY".

        Returns a list of sent message IDs.
        """
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Video file not found on disk: {path}")

        logger.info("Preparing video for Telegram upload: %s", path.name)

        # 1. Split video into parts if needed
        parts = await split_video_if_needed(path)
        total_parts = len(parts)

        # 2. Generate a thumbnail frame for the video
        thumb_path = await generate_thumbnail(path)

        sent_message_ids: list[int] = []

        try:
            for part in parts:
                caption = build_telegram_video_caption(
                    member_name=member_name,
                    member_username=member_username,
                    started_at=started_at,
                    live_title=live_title,
                    part_number=part.part_number,
                    total_parts=total_parts,
                    file_size_bytes=part.size_bytes,
                    platform=platform,
                )

                logger.info(
                    "Uploading to Telegram [Part %d/%d]: %s (%.1f MB)...",
                    part.part_number,
                    total_parts,
                    part.file_path.name,
                    part.size_bytes / (1024 * 1024),
                )

                msg_id = await self.send_video_file(
                    file_path=part.file_path,
                    caption=caption,
                    duration=part.duration_seconds,
                    width=part.width,
                    height=part.height,
                    thumb_path=thumb_path,
                    channel_id=channel_id,
                    progress_callback=progress_callback,
                )

                if msg_id:
                    sent_message_ids.append(msg_id)
                else:
                    raise RuntimeError(
                        f"Failed to upload part {part.part_number}/{total_parts} ({part.file_path.name}) to Telegram"
                    )

            logger.info(
                "All %d part(s) of %s successfully uploaded to Telegram. Message IDs: %s",
                total_parts,
                path.name,
                sent_message_ids,
            )
            return sent_message_ids

        finally:
            # Clean up generated split part files
            cleanup_video_parts(parts)
            # Clean up temporary thumbnail
            if thumb_path and thumb_path.exists():
                try:
                    thumb_path.unlink(missing_ok=True)
                except Exception:
                    pass

    async def disconnect(self) -> None:
        if self._connected:
            await self._client.disconnect()
            self._connected = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()


DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS_ID = [
    "", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
    "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
]


def _format_live_time(iso_str: str) -> str:
    """
    Ubah waktu tersimpan (UTC) menjadi format Indonesia di zona tampilan.

    Nilai lama tanpa penanda zona waktu dibaca sesuai
    LEGACY_NAIVE_TIME_OFFSET_HOURS; lihat bot/timeutil.py.
    """
    if not iso_str:
        return ""
    converted = timeutil.to_display(
        iso_str,
        Config.DISPLAY_TIMEZONE_OFFSET_HOURS,
        Config.LEGACY_NAIVE_TIME_OFFSET_HOURS,
    )
    if converted is None:
        return iso_str
    day = DAYS_ID[converted.weekday()]
    date = converted.day
    mon = MONTHS_ID[converted.month]
    year = converted.year
    time_str = converted.strftime("%H:%M")
    label = Config.DISPLAY_TIMEZONE_LABEL
    return f"{day}, {date} {mon} {year} | {time_str} {label}".rstrip()


def _dot_at(username: str) -> str:
    """@jkt48_olla -> @.jkt48_olla (prevents Telegram username tagging)"""
    u = username.strip()
    if u.startswith("@"):
        return "@." + u[1:]
    return "@." + u


def _platform_label(platform: str) -> str:
    """
    Label platform untuk header pesan Telegram: 'showroom' -> 'SHOWROOM',
    lainnya -> 'IDN'. Sama dengan pembentukan judul web (`buildDisplayTitle`) dan
    judul YouTube (`YouTubeChannelPool.build_title`) agar sebutannya konsisten.
    """
    return "SHOWROOM" if (platform or "").strip().lower() == "showroom" else "IDN"


def build_telegram_video_caption(
    member_name: str,
    member_username: str,
    started_at: str,
    live_title: str = "",
    part_number: int = 1,
    total_parts: int = 1,
    file_size_bytes: int = 0,
    platform: str = "",
) -> str:
    """
    Build HTML caption for video uploaded directly to Telegram.

    Header mengikuti platform rekaman ('IDN LIVE REPLAY' / 'SHOWROOM LIVE REPLAY')
    supaya tidak ada lagi label IDN pada video Showroom.
    """
    live_time = _format_live_time(started_at)
    size_str = _format_size(file_size_bytes)

    header = f"🔴 <b>{_platform_label(platform)} LIVE REPLAY</b>"
    if total_parts > 1:
        header += f" <b>[Part {part_number}/{total_parts}]</b>"

    lines = [
        header,
        "",
        f"👤 <b>{member_name}</b> ({_dot_at(member_username)})",
    ]
    if live_title:
        lines.append(f"📌 {live_title}")
    if live_time:
        lines.append(f"📅 {live_time}")

    if total_parts > 1:
        lines.append(f"📁 Part {part_number} dari {total_parts} ({size_str})")
    elif file_size_bytes > 0:
        lines.append(f"📁 Ukuran: {size_str}")

    return "\n".join(lines)


def build_youtube_notification(
    member_name: str,
    member_username: str,
    started_at: str,
    video_id: str,
    live_title: str = "",
    platform: str = "",
) -> str:
    """
    Build the Telegram notification message for a newly uploaded YouTube video.

    Header mengikuti platform rekaman ('IDN LIVE REPLAY' / 'SHOWROOM LIVE REPLAY').
    """
    live_time = _format_live_time(started_at)
    yt_url = f"https://youtu.be/{video_id}"

    lines = [
        f"🔴 <b>{_platform_label(platform)} LIVE REPLAY</b>",
        "",
        f"👤 <b>{member_name}</b> ({_dot_at(member_username)})",
    ]
    if live_title:
        lines.append(f"📌 {live_title}")
    if live_time:
        lines.append(f"📅 {live_time}")

    lines.append("")
    lines.append(f"▶️ <b>Watch:</b> <a href=\"{yt_url}\">YouTube Unlisted</a>")

    return "\n".join(lines)
