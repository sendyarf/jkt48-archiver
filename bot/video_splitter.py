"""
video_splitter.py - Lossless video splitting and thumbnail generation using FFmpeg.

Splits videos larger than the Telegram 2GB limit (default: 1950 MB safe threshold)
into multiple sequential parts using FFmpeg stream copy (-c copy) without re-encoding.
"""
import asyncio
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from bot.config import Config

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    file_path: Path
    size_bytes: int
    duration_seconds: float
    width: int
    height: int


@dataclass
class VideoPart:
    part_number: int       # 1-indexed (e.g. 1 for Part 1)
    total_parts: int       # Total count (e.g. 2 for Part 1 of 2)
    file_path: Path
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    is_split: bool         # True if this is a generated temporary split chunk


def get_default_max_bytes() -> int:
    """Return max file size in bytes from config (default ~1950 MB)."""
    return Config.TELEGRAM_MAX_FILE_SIZE_MB * 1024 * 1024


async def get_video_metadata(file_path: Union[str, Path]) -> VideoMetadata:
    """
    Extract video metadata (size, duration, width, height) using ffprobe.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    size_bytes = path.stat().st_size
    duration = 0.0
    width = 0
    height = 0

    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration:format=duration,size",
        "-of", "json",
        str(path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0 and stdout:
            data = json.loads(stdout.decode("utf-8", errors="replace"))

            # Format duration
            fmt = data.get("format", {})
            if fmt.get("duration") and fmt["duration"] != "N/A":
                try:
                    duration = float(fmt["duration"])
                except (ValueError, TypeError):
                    pass

            # Stream metadata
            streams = data.get("streams", [])
            if streams:
                v_stream = streams[0]
                width = int(v_stream.get("width", 0))
                height = int(v_stream.get("height", 0))
                if duration <= 0 and v_stream.get("duration") and v_stream["duration"] != "N/A":
                    try:
                        duration = float(v_stream["duration"])
                    except (ValueError, TypeError):
                        pass

    except Exception as exc:
        logger.warning("Could not probe video metadata for %s: %s", path.name, exc)

    return VideoMetadata(
        file_path=path,
        size_bytes=size_bytes,
        duration_seconds=duration,
        width=width,
        height=height,
    )


async def generate_thumbnail(
    video_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    seek_seconds: float = 5.0,
) -> Optional[Path]:
    """
    Extract a single frame from the video using FFmpeg as a thumbnail JPEG.
    """
    path = Path(video_path).resolve()
    if not path.exists():
        return None

    if output_path is None:
        thumb_file = path.parent / f"{path.stem}_thumb.jpg"
    else:
        thumb_file = Path(output_path).resolve()

    seek_str = f"{seek_seconds:.2f}"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", seek_str,
        "-i", str(path),
        "-vframes", "1",
        "-q:v", "2",
        str(thumb_file),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        if thumb_file.exists() and thumb_file.stat().st_size > 0:
            return thumb_file

        # Fallback to 1 second if 5 seconds failed (e.g. video shorter than 5s)
        if seek_seconds > 1.0:
            cmd[2] = "1.00"
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if thumb_file.exists() and thumb_file.stat().st_size > 0:
                return thumb_file

    except Exception as exc:
        logger.warning("Failed to generate thumbnail for %s: %s", path.name, exc)

    return None


async def split_video_if_needed(
    file_path: Union[str, Path],
    max_bytes: Optional[int] = None,
) -> list[VideoPart]:
    """
    Checks video size. If larger than max_bytes, splits it into sequential parts
    using FFmpeg segment muxer with stream copy (-c copy).
    
    Returns a list of VideoPart objects.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    meta = await get_video_metadata(path)
    file_size = meta.size_bytes
    limit = max_bytes or get_default_max_bytes()

    # Case 1: No split needed
    if file_size <= limit:
        return [
            VideoPart(
                part_number=1,
                total_parts=1,
                file_path=path,
                size_bytes=file_size,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                is_split=False,
            )
        ]

    # Case 2: File exceeds limit → split into parts
    # Target chunk size is 8% below limit to account for container/keyframe jitter
    target_chunk = max(1024, int(limit * 0.92))
    num_parts = max(2, math.ceil(file_size / target_chunk))
    duration = meta.duration_seconds

    logger.info(
        "File %s is %.1f MB (exceeds limit %.1f MB). Splitting into %d parts...",
        path.name,
        file_size / (1024 * 1024),
        limit / (1024 * 1024),
        num_parts,
    )

    if duration > 0:
        segment_seconds = max(1.0, duration / num_parts)
    else:
        # Fallback estimation based on average bitrate assuming 2.5 Mbps
        estimated_sec = (file_size * 8) / (2500 * 1000)
        segment_seconds = max(10.0, estimated_sec / num_parts)

    output_dir = path.parent
    pattern = str(output_dir / f"{path.stem}_tgpart_%03d.mp4")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(path),
        "-c", "copy",
        "-map", "0",
        "-segment_time", f"{segment_seconds:.2f}",
        "-f", "segment",
        "-reset_timestamps", "1",
        pattern,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace")[:400]
        logger.error("FFmpeg split failed (code %d): %s", proc.returncode, err_msg)
        raise RuntimeError(f"FFmpeg split failed: {err_msg}")

    # Discover generated parts
    raw_parts = sorted(output_dir.glob(f"{path.stem}_tgpart_*.mp4"))
    if not raw_parts:
        raise RuntimeError(f"FFmpeg completed but no part files were generated for {path.name}")

    total = len(raw_parts)
    parts: list[VideoPart] = []

    for idx, p in enumerate(raw_parts, start=1):
        p_meta = await get_video_metadata(p)
        parts.append(
            VideoPart(
                part_number=idx,
                total_parts=total,
                file_path=p,
                size_bytes=p_meta.size_bytes,
                duration_seconds=p_meta.duration_seconds,
                width=p_meta.width or meta.width,
                height=p_meta.height or meta.height,
                is_split=True,
            )
        )
        logger.info(
            "Created part %d/%d for %s: %s (%.1f MB)",
            idx,
            total,
            path.name,
            p.name,
            p_meta.size_bytes / (1024 * 1024),
        )

    return parts


def cleanup_video_parts(parts: list[VideoPart]) -> None:
    """
    Remove temporary split part files from disk.
    Leaves original files (is_split=False) untouched.
    """
    for part in parts:
        if part.is_split and part.file_path.exists():
            try:
                part.file_path.unlink(missing_ok=True)
                logger.debug("Deleted temporary part file: %s", part.file_path.name)
            except Exception as exc:
                logger.warning("Failed to delete temporary part %s: %s", part.file_path, exc)
