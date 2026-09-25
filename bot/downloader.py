"""
downloader.py - HLS stream recorder backed by ffmpeg.

The IDN/Showroom HLS endpoints are live playlists. ffmpeg writes a
fragmented-MP4 stream incrementally and handles reconnects at the HTTP/HLS
layer. The process remains cancellable through ``_active_processes``; a
non-empty partial file left after SIGTERM is used as a salvage segment.
"""
import asyncio
import contextlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx

from bot.config import Config

logger = logging.getLogger(__name__)

# ffmpeg writes an MP4 `ftyp` box before the media fragments.  A non-empty
# file without that header is usually an HTTP/CDN error body, not a recording.
_MIN_OUTPUT_BYTES = 16
_MP4_SUFFIXES = {".mp4", ".m4v"}


def _is_usable_recording(path: Optional[Path]) -> bool:
    """Return whether a recorder output is plausibly a usable media file.

    ffmpeg may create only the ``ftyp``/``moov`` header before the first media
    fragment arrives.  Treating that header-only file as a completed recording
    would lose a recording when a live task is stopped during startup, so an
    MP4 must also contain at least one media box (``moof`` or ``mdat``).
    """
    if path is None:
        return False
    try:
        if not path.is_file() or path.stat().st_size < _MIN_OUTPUT_BYTES:
            return False
        suffix = path.suffix.lower()
        is_mp4 = (
            suffix in _MP4_SUFFIXES
            or path.name.lower().endswith(tuple(ext + ".part" for ext in _MP4_SUFFIXES))
        )
        if is_mp4:
            with path.open("rb") as handle:
                header = handle.read(256 * 1024)
            return (
                b"ftyp" in header
                and (b"moof" in header or b"mdat" in header)
            )
        if suffix == ".ts":
            # MPEG-TS starts with the 0x47 sync byte; this rejects a large
            # HTML/JSON error body left with a media-looking suffix.
            with path.open("rb") as handle:
                return handle.read(1) == b"G"
        return suffix in {".mkv", ".webm"}
    except OSError:
        return False


class DownloadError(Exception):
    """Raised when ffmpeg exits without producing a usable recording."""


# Maps live_id -> asyncio.subprocess.Process for live cancel control
_active_processes: dict[str, asyncio.subprocess.Process] = {}


def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """
    Kirim SIGTERM ke proses ffmpeg (best-effort, tanpa await).

    SIGTERM membuat ffmpeg menutup container file dengan rapi sehingga
    segmen parsial tetap bisa dibaca (bukan file rusak).
    """
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    except Exception as exc:  # pragma: no cover - platform specific
        logger.warning("Gagal terminate proses download: %s", exc)


async def terminate_process_async(
    process: asyncio.subprocess.Process,
    grace_seconds: float = 5.0,
) -> None:
    """SIGTERM lalu tunggu sebentar; paksa kill kalau proses tidak berhenti."""
    _terminate_process(process)
    if process is None or process.returncode is not None:
        return
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    if process.returncode is None:
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()


def cancel_download(live_id: str) -> bool:
    """
    Gracefully terminate the ffmpeg process for a specific live_id.
    Returns True if process was found and terminated, False otherwise.
    """
    proc = _active_processes.get(live_id)
    if proc and proc.returncode is None:
        logger.info("🛑 Manually terminating ffmpeg process for live_id=%s", live_id)
        try:
            proc.terminate()  # SIGTERM allows ffmpeg to cleanly close the file container
            return True
        except Exception as exc:
            logger.warning("Error terminating process for %s: %s", live_id, exc)
    return False


async def _wait_for_hls_url(
    hls_url: str,
    auth_token: Optional[str] = None,
    max_attempts: int = 15,
    delay_seconds: float = 2.0,
) -> bool:
    """
    Poll the HLS URL until it returns a successful response (HTTP 200),
    waiting for the stream to initialize on the CDN.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=5) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.get(hls_url, headers=headers)
                if resp.status_code == 200:
                    text = resp.text[:200].lstrip()
                    if "#EXTM3U" in text:
                        logger.info(
                            "HLS URL is active (HTTP 200 with playlist) on attempt %d",
                            attempt,
                        )
                        return True
                    logger.warning(
                        "HLS URL returned HTTP 200 without #EXTM3U on attempt %d/%d",
                        attempt, max_attempts,
                    )
                    # A CDN error page is not a live stream; do not start a
                    # recorder against it.
                    return False
                logger.warning(
                    "HLS URL returned HTTP %d on attempt %d/%d. Waiting for stream to initialize...",
                    resp.status_code,
                    attempt,
                    max_attempts,
                )
            except Exception as exc:
                # %r, bukan %s: httpx.ReadTimeout dkk. punya pesan KOSONG, jadi
                # %s mencetak ": " tanpa keterangan apa pun (kasus URL Showroom
                # mati — CDN menggantung koneksi sampai timeout).
                logger.warning(
                    "Error checking HLS URL on attempt %d/%d: %r",
                    attempt,
                    max_attempts,
                    exc,
                )
            await asyncio.sleep(delay_seconds)
    return False


def _ensure_download_dir() -> Path:
    """Create the download directory if it doesn't exist."""
    path = Path(Config.DOWNLOAD_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def has_enough_disk_space(min_mb: Optional[int] = None) -> bool:
    """
    True bila space bebas di DOWNLOAD_DIR masih di atas ambang MIN_FREE_DISK_MB.

    Dipanggil sebelum mulai recording supaya ffmpeg tidak gagal di
    tengah jalan karena disk penuh (insiden yang tercatat di SHOWROOM-PLAN §6.1).
    """
    threshold_mb = Config.MIN_FREE_DISK_MB if min_mb is None else min_mb
    try:
        free_mb = shutil.disk_usage(str(_ensure_download_dir())).free // (1024 * 1024)
    except OSError:
        # Tidak bisa cek → jangan blokir recording (mis. FS aneh).
        return True
    if free_mb < threshold_mb:
        logger.warning(
            "Disk space rendah: %d MB bebas < ambang %d MB — recording dilewati.",
            free_mb, threshold_mb,
        )
        return False
    return True


def _output_path(member_username: str, live_id: str) -> Path:
    """
    Build a deterministic output base path (without extension).
    ffmpeg appends the configured ``.mp4`` extension.

    Example: /tmp/jkt48-lives/jkt48_fritzy_20250729_143022_live123
    """
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{member_username}_{now}_{live_id}"
    return _ensure_download_dir() / filename


def _find_output_file(base_path: Path) -> Optional[Path]:
    """
    Find the actual output file created by the recorder.
    ffmpeg writes the configured ``.mp4`` path directly; the fallback also
    accepts a non-empty partial file left by an interrupted process.
    """
    # `ftyp` check prevents a CDN error page from being accepted as media.
    for ext in (".mp4", ".mkv", ".ts", ".m4v"):
        candidate = Path(f"{base_path}{ext}")
        try:
            if _is_usable_recording(candidate):
                return candidate
        except OSError:
            continue
    # Fallback: search parent dir for files matching the stem.  A non-empty
    # partial file left after SIGTERM is still useful as a salvage segment.
    parent = base_path.parent
    stem = base_path.name
    matches = []
    for candidate in parent.glob(f"{stem}.*"):
        try:
            if _is_usable_recording(candidate):
                matches.append(candidate)
        except OSError:
            continue
    if matches:
        return max(matches, key=lambda p: p.stat().st_size)
    return None


async def _read_output(
    stream: asyncio.StreamReader,
    live_id: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Read ffmpeg stdout/stderr in the background for logging and progress."""
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode(errors="replace").rstrip()
        if not decoded:
            continue

        # ffmpeg progress lines look like:
        # frame= 1234 fps= ... time=00:00:41.00 ...
        if "frame=" in decoded and "time=" in decoded:
            if on_progress:
                on_progress(decoded.strip())
            logger.debug("[ffmpeg][%s] %s", live_id, decoded)
        elif "ERROR" in decoded or "error" in decoded.lower():
            logger.warning("[ffmpeg][%s] %s", live_id, decoded)
        else:
            logger.debug("[ffmpeg][%s] %s", live_id, decoded)


async def download_stream(
    hls_url: str,
    member_username: str,
    live_id: str,
    on_progress: Optional[Callable[[str], None]] = None,
    auth_token: Optional[str] = None,
    max_empty_retries: int = 5,
    empty_retry_delay: float = 10,
    url_refresher: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
) -> Path:
    """
    Download a live HLS stream to a local MP4 file using ffmpeg.

    Args:
        hls_url:          The .m3u8 URL from IDN API.
        member_username:  e.g. 'jkt48_fritzy'
        live_id:          Unique live session ID (used in filename + DB).
        on_progress:      Optional callback(line_str) for progress updates.
        auth_token:       Optional Bearer token for IDN authenticated streams.
        url_refresher:  Optional async callback yang diminta URL HLS segar di
                          awal SETIAP retry. Bila mengembalikan URL baru, retry
                          memakai URL itu. Penting untuk Showroom: URL lama mati
                          total (CDN menggantung koneksi) begitu sesi broadcast
                          berganti, jadi retry pada URL yang sama sia-sia.

    Returns:
        Path to the recorded video file (.mp4).

    Raises:
        DownloadError: If ffmpeg exits without producing a non-empty file.
        FileNotFoundError: If ffmpeg is not installed.
    """
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError(
            "ffmpeg not found (required for HLS recording).\n"
            "Install: sudo apt install ffmpeg"
        )

    # A reconnect/restart segment often starts while the member's HLS stream is
    # momentarily not feeding (e.g. right after a lag). ffmpeg then exits
    # without producing a usable file. Retry a few times so the segment can be
    # captured once the stream resumes and still be merged with the earlier part.
    MAX_EMPTY_RETRIES = max(1, int(max_empty_retries))
    RETRY_DELAY = max(0, float(empty_retry_delay))

    last_error: Optional[str] = None
    for attempt in range(1, MAX_EMPTY_RETRIES + 1):
        # Minta URL segar sebelum retry: sesi broadcast yang berganti mematikan
        # URL lama, sehingga menunggu URL lama aktif kembali tidak akan pernah
        # berhasil (insiden 22 Sep 2026: ~1 jam terbuang pada URL mati).
        if attempt > 1 and url_refresher is not None:
            try:
                fresh_url = await url_refresher()
            except Exception as exc:
                logger.debug("[%s] url_refresher gagal: %r", live_id, exc)
                fresh_url = None
            if fresh_url and fresh_url != hls_url:
                logger.info(
                    "[%s] URL HLS diganti dengan URL segar pada retry %d/%d",
                    live_id, attempt, MAX_EMPTY_RETRIES,
                )
                hls_url = fresh_url
        # Wait for the HLS stream to become available (avoid 404 during startup)
        hls_active = await _wait_for_hls_url(hls_url, auth_token)
        if not hls_active:
            logger.warning(
                "Stream URL did not become active after checking (attempt %d/%d). "
                "Starting ffmpeg anyway as fallback.",
                attempt, MAX_EMPTY_RETRIES,
            )

        # Fresh path per attempt prevents a retry from colliding with a
        # prior attempt's output.
        base_path = _output_path(member_username, live_id)
        # Never use with_suffix here: a live_id may itself contain a dot and
        # with_suffix would replace part of the identifier instead of appending
        # the recorder extension.
        output_path = Path(f"{base_path}.mp4")
        logger.info(
            "Starting ffmpeg HLS recording (attempt %d/%d): %s → %s",
            attempt, MAX_EMPTY_RETRIES, member_username, output_path.name,
        )

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "warning",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "10",
            "-i", hls_url,
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            # Fragmented MP4 remains playable while a live recording is still
            # being written and is also safe to concatenate after reconnect.
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4",
            "-y", str(output_path),
        ]

        # Pass Bearer token as an HTTP header if provided.
        if auth_token:
            cmd[1:1] = ["-headers", f"Authorization: Bearer {auth_token}\r\n"]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
        )

        _active_processes[live_id] = process

        # Read output in background while process runs
        output_task = asyncio.create_task(
            _read_output(process.stdout, live_id, on_progress)
        )

        # ── Inactivity timeout ─────────────────────────────────────────
        # Kill ffmpeg if no new data is written for INACTIVE_TIMEOUT seconds.
        # This recovers from stream lag/disconnect without waiting forever.
        # Some downloader backends create the destination only after ffmpeg has
        # opened the live input. Give the process a longer first-data grace,
        # then apply the inactivity timeout once media has started arriving.
        INACTIVE_TIMEOUT = 120
        FIRST_DATA_GRACE = max(INACTIVE_TIMEOUT, 180)
        last_size = 0
        stalled = 0

        try:
            while True:
                try:
                    ret = await asyncio.wait_for(process.wait(), timeout=5)
                    return_code = ret
                    break
                except asyncio.TimeoutError:
                    # Check if ffmpeg is making progress
                    current_size = 0
                    try:
                        if output_path.is_file():
                            current_size = output_path.stat().st_size
                    except OSError:
                        pass

                    if current_size > last_size:
                        stalled = 0
                        last_size = current_size
                    else:
                        stalled += 5
                        # Do not kill a live downloader during startup merely
                        # because ffmpeg has not created its destination yet.
                        limit = INACTIVE_TIMEOUT if last_size > 0 else FIRST_DATA_GRACE
                        if stalled >= limit:
                            logger.warning(
                                "[%s] No progress for %ds — killing ffmpeg",
                                live_id, stalled,
                            )
                            process.kill()
                            return_code = await process.wait()
                            break
        except asyncio.CancelledError:
            # Task dibatalkan (hard shutdown): pastikan ffmpeg benar-benar mati agar
            # tidak jadi proses orphan yang terus menulis file & memakai bandwidth.
            logger.info("[%s] Download task dibatalkan — menghentikan ffmpeg", live_id)
            await terminate_process_async(process)
            output_task.cancel()
            with contextlib.suppress(Exception):
                await output_task
            raise
        finally:
            _active_processes.pop(live_id, None)
            # Jaring pengaman: kalau proses masih hidup karena sebab lain, hentikan.
            if process.returncode is None:
                await terminate_process_async(process)

        with contextlib.suppress(Exception):
            await output_task

        # A usable partial recording is valuable even when ffmpeg exits with a
        # non-zero code after SIGTERM or an HLS disconnect.  Check the file
        # before classifying the exit as a hard failure.
        out_path = output_path if _is_usable_recording(output_path) else None
        if out_path is None:
            out_path = _find_output_file(base_path)
        if _is_usable_recording(out_path):
            assert out_path is not None
            size_mb = out_path.stat().st_size / (1024 * 1024)
            if return_code not in (0, 1, -15, -9) and return_code >= 0:
                logger.warning(
                    "[%s] ffmpeg keluar code %s tetapi file parsial %s "
                    "tetap digunakan (%.1f MB)",
                    live_id, return_code, out_path.name, size_mb,
                )
            else:
                logger.info(
                    "Download complete: %s (%.1f MB) → %s",
                    member_username, size_mb, out_path.name,
                )
            return out_path

        # Treat an unexpected exit like an empty output: a later retry can
        # recover from a transient 404/CDN disconnect instead of making one
        # failed ffmpeg invocation terminal for the whole recording.
        if return_code not in (0, 1, -15, -9) and return_code >= 0:
            # Preserve the process diagnosis; don't replace it with a generic
            # "file not found" message on the final attempt.
            last_error = (
                f"ffmpeg exited with code {return_code} for live_id={live_id}"
            )
        else:
            last_error = (
                f"Output file not found after ffmpeg completed. "
                f"Searched for: {output_path}"
            )
        if attempt < MAX_EMPTY_RETRIES:
            logger.warning(
                "[%s] No output produced on attempt %d/%d. "
                "Retrying in %ds …",
                live_id, attempt, MAX_EMPTY_RETRIES, RETRY_DELAY,
            )
            await asyncio.sleep(RETRY_DELAY)

    raise DownloadError(last_error or "Download failed: no output file")


async def get_duration_seconds(file_path: str | Path) -> Optional[float]:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(file_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        import json
        data = json.loads(stdout)
        return float(data["format"]["duration"])
    except Exception as exc:
        logger.warning("ffprobe failed for %s: %s", file_path, exc)
        return None



def delete_file(file_path: str | Path) -> None:
    """Remove a local file safely (ignore if already gone)."""
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            logger.info("Deleted local file: %s", path.name)
    except OSError as exc:
        logger.warning("Could not delete %s: %s", file_path, exc)


def get_file_size_bytes(file_path: str | Path) -> int:
    """Return file size in bytes, or 0 if file doesn't exist."""
    try:
        return Path(file_path).stat().st_size
    except OSError:
        return 0
