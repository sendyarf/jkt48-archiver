"""
downloader.py - HLS stream downloader using yt-dlp.

yt-dlp handles:
  - Auto-selecting best quality from HLS master playlist
  - Reconnect & segment retry automatically
  - Bearer token / custom headers passthrough
  - Proper muxing via ffmpeg (still required as a dependency)

ffmpeg is still needed on the system for muxing, but the
download logic, quality selection, and retry are managed by yt-dlp.
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


class DownloadError(Exception):
    """Raised when yt-dlp exits with a non-zero code."""


# Maps live_id -> asyncio.subprocess.Process for live cancel control
_active_processes: dict[str, asyncio.subprocess.Process] = {}


def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """
    Kirim SIGTERM ke proses yt-dlp (best-effort, tanpa await).

    SIGTERM membuat yt-dlp/ffmpeg menutup container file dengan rapi sehingga
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


def cancel_download(live_id: str) -> bool:
    """
    Gracefully terminate the yt-dlp download process for a specific live_id.
    Returns True if process was found and terminated, False otherwise.
    """
    proc = _active_processes.get(live_id)
    if proc and proc.returncode is None:
        logger.info("🛑 Manually terminating yt-dlp process for live_id=%s", live_id)
        try:
            proc.terminate()  # SIGTERM allows yt-dlp / ffmpeg to cleanly close the file container
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
        )
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=5) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.get(hls_url, headers=headers)
                if resp.status_code == 200:
                    logger.info("HLS URL is active (HTTP 200) on attempt %d", attempt)
                    return True
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

    Dipanggil sebelum mulai recording supaya yt-dlp/ffmpeg tidak gagal di
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
    Build a deterministic output file path (WITHOUT extension).
    yt-dlp will append the correct extension automatically.

    Example: /tmp/jkt48-lives/jkt48_fritzy_20250729_143022_live123
    """
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{member_username}_{now}_{live_id}"
    return _ensure_download_dir() / filename


def _find_output_file(base_path: Path) -> Optional[Path]:
    """
    Find the actual output file created by yt-dlp.
    yt-dlp appends .mp4, .mkv, etc. — we search for whichever was created.
    """
    for ext in (".mp4", ".mkv", ".ts", ".m4v"):
        candidate = base_path.with_suffix(ext)
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    # Fallback: search parent dir for files matching the stem
    parent = base_path.parent
    stem = base_path.name
    matches = sorted(parent.glob(f"{stem}.*"), key=lambda p: p.stat().st_size, reverse=True)
    if matches:
        return matches[0]
    return None


async def _read_output(
    stream: asyncio.StreamReader,
    live_id: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Read yt-dlp stdout/stderr in the background for logging and progress."""
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode(errors="replace").rstrip()
        if not decoded:
            continue

        # yt-dlp progress lines look like:
        # [download]  23.4% of ~  1.50GiB at   4.20MiB/s ETA 00:17
        if "[download]" in decoded and "%" in decoded:
            if on_progress:
                on_progress(decoded.strip())
            logger.debug("[yt-dlp][%s] %s", live_id, decoded)
        elif "ERROR" in decoded or "error" in decoded.lower():
            logger.warning("[yt-dlp][%s] %s", live_id, decoded)
        else:
            logger.debug("[yt-dlp][%s] %s", live_id, decoded)


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
    Download a live HLS stream to a local .mp4 file using yt-dlp.

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
        DownloadError: If yt-dlp exits with a non-zero return code.
        FileNotFoundError: If yt-dlp or ffmpeg is not installed.
    """
    if not shutil.which("yt-dlp"):
        raise FileNotFoundError(
            "yt-dlp not found. Install it with:\n"
            "  pip install yt-dlp\n"
            "  or: sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && sudo chmod +x /usr/local/bin/yt-dlp"
        )
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError(
            "ffmpeg not found (required by yt-dlp for muxing).\n"
            "Install: sudo apt install ffmpeg"
        )

    # A reconnect/restart segment often starts while the member's HLS stream is
    # momentarily not feeding (e.g. right after a lag). yt-dlp then exits with
    # code 1 and produces NO output file. Instead of failing immediately, retry
    # a few times with a short backoff so the segment is captured once the
    # stream resumes, and it can still be merged with the earlier part.
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
                "Starting yt-dlp anyway as fallback.",
                attempt, MAX_EMPTY_RETRIES,
            )

        # Fresh output path per attempt (timestamped) to avoid stale files.
        base_path = _output_path(member_username, live_id)
        # yt-dlp output template — %(ext)s will be replaced by yt-dlp
        output_template = str(base_path) + ".%(ext)s"

        logger.info(
            "Starting download (attempt %d/%d): %s → %s.*",
            attempt, MAX_EMPTY_RETRIES, member_username, base_path.name,
        )

        cmd = [
            "yt-dlp",
            # ── Quality ─────────────────────────────────────────────
            # Pick best video + best audio; merge into mp4 via ffmpeg
            "--format", "bestvideo+bestaudio/best",
            "--merge-output-format", "mp4",
            # ── Output ──────────────────────────────────────────────
            "--output", output_template,
            # No .part files (we want a clean path search afterwards)
            "--no-part",
            # ── Live stream handling ─────────────────────────────────
            # Give the HLS playlist more time to start producing segments
            "--wait-for-video", "15",
            # ── Network resilience ──────────────────────────────────
            # Retry individual fragments up to 50 times (helps survive stream lag/buffering)
            "--fragment-retries", "50",
            "--skip-unavailable-fragments",
            # Retry the whole download up to 10 times on fatal error
            "--retries", "10",
            # Keep retrying on connection error
            "--retry-sleep", "5",
            # ── Misc ────────────────────────────────────────────────
            "--no-playlist",
            "--no-warnings",
            "--newline",       # one progress line per line (easier to parse)
        ]

        # Pass Bearer token as HTTP header if provided
        if auth_token:
            cmd += ["--add-header", f"Authorization:Bearer {auth_token}"]

        cmd.append(hls_url)

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
        # Kill yt-dlp if no new data is written for INACTIVE_TIMEOUT seconds.
        # This recovers from stream lag/disconnect without waiting forever.
        INACTIVE_TIMEOUT = 120
        last_size = 0
        stalled = 0

        try:
            while True:
                try:
                    ret = await asyncio.wait_for(process.wait(), timeout=5)
                    return_code = ret
                    break
                except asyncio.TimeoutError:
                    # Check if yt-dlp is making progress
                    current_size = 0
                    if base_path.parent.exists():
                        matches = list(base_path.parent.glob(f"{base_path.name}.*"))
                        if matches:
                            current_size = max(p.stat().st_size for p in matches)

                    if current_size > last_size:
                        stalled = 0
                        last_size = current_size
                    else:
                        stalled += 5
                        if stalled >= INACTIVE_TIMEOUT:
                            logger.warning(
                                "[%s] No progress for %ds — killing yt-dlp",
                                live_id, stalled,
                            )
                            process.kill()
                            return_code = await process.wait()
                            break
        except asyncio.CancelledError:
            # Task dibatalkan (hard shutdown): pastikan yt-dlp benar-benar mati agar
            # tidak jadi proses orphan yang terus menulis file & memakai bandwidth.
            logger.info("[%s] Download task dibatalkan — menghentikan yt-dlp", live_id)
            _terminate_process(process)
            output_task.cancel()
            with contextlib.suppress(Exception):
                await output_task
            raise
        finally:
            _active_processes.pop(live_id, None)
            # Jaring pengaman: kalau proses masih hidup karena sebab lain, hentikan.
            if process.returncode is None:
                _terminate_process(process)

        with contextlib.suppress(Exception):
            await output_task

        # Note: exit code 1 or negative (SIGTERM -15) are valid when manually
        # cancelled or the stream ends.
        if return_code not in (0, 1, -15, -9) and return_code >= 0:
            raise DownloadError(
                f"yt-dlp exited with code {return_code} for live_id={live_id}"
            )

        # Find the actual output file
        out_path = _find_output_file(base_path)
        if out_path:
            size_mb = out_path.stat().st_size / (1024 * 1024)
            logger.info(
                "Download complete: %s (%.1f MB) → %s",
                member_username, size_mb, out_path.name,
            )
            return out_path

        # No output produced → stream probably not feeding yet.
        last_error = (
            f"Output file not found after yt-dlp completed. "
            f"Searched for: {base_path}.*"
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
