"""
telegram_sender.py - Telegram notifier and video uploader using Telethon (userbot).

Supports sending:
1. Direct video uploads to Telegram channels with streaming support,
   automatic splitting for files > 2GB (safe limit default: 1950 MB),
   video thumbnail preview, dan RESUME intra-file untuk file >= 64 MB
   (progres part disimpan di sidecar ``<path>.tgup.json`` sehingga flood
   atau restart proses tidak mengulang upload dari byte 0).
2. Text notifications with YouTube links (when UPLOAD_TARGET=youtube).
"""
import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Callable, Optional, Union

from telethon import TelegramClient, functions, helpers, types, utils
from telethon.errors import (
    FilePartMissingError,
    FloodError,
    FloodWaitError,
    MediaEmptyError,
)
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo

from bot.config import Config
from bot import timeutil
from bot.telegram_limits import (
    MAX_FILE_PARTS_FREE,
    MAX_FILE_PARTS_PREMIUM,
    max_file_bytes,
    safe_split_bytes,
)
from bot.video_splitter import (
    VideoPart,
    cleanup_video_parts,
    generate_thumbnail,
    get_video_metadata,
    split_video_if_needed,
)

logger = logging.getLogger(__name__)


class MediaRejectedError(Exception):
    """Raised when Telegram permanently refuses a media (not retryable)."""


class TelegramFloodExhausted(RuntimeError):
    """Upload besar kehabisan jatah retry flood; akun sedang kena rate limit.

    Turunan dari RuntimeError agar jalur penanganan yang sudah ada tetap aman.
    Caller (retry worker) memakai exception ini untuk memberi COOLDOWN pada
    file tersebut. Progres upload file besar (>= 64 MB) TERSIMPAN di sidecar
    ``<path>.tgup.json``, jadi percobaan berikutnya melanjutkan dari part
    terakhir dan tidak lagi membuang seluruh bandwidth file dari nol.
    """


class TelegramFileTooLarge(RuntimeError):
    """File butuh lebih banyak part daripada batas jumlah part akun ini.

    Batas ini datang dari server Telegram (bukan dari Telethon): akun biasa
    dibatasi ``MAX_FILE_PARTS_FREE`` part, akun Premium dua kali lipatnya.
    Kondisi ini PERMANEN selama jenis akun dan ukuran file tidak berubah,
    jadi tidak ada gunanya diulang — solusinya naik ke Telegram Premium atau
    memotong file lebih kecil lewat ``TELEGRAM_MAX_FILE_SIZE_MB``.
    """


class TelegramUploadStalled(RuntimeError):
    """Server Telegram tidak merespons dalam batas waktu (koneksi macet).

    Insiden 30 Sep 2026: satu ``await`` RPC menggantung selamanya sehingga
    upload tidak pernah selesai maupun melempar error, baris DB terkunci di
    ``uploading_telegram``, dan lock upload memblokir notifikasi admin
    (gejala: notifikasi "UPLOAD TELEGRAM" tanpa lanjutan selama 28 jam).
    Semua request kini dibungkus ``asyncio.wait_for``; timeout ini yang
    datang dari sana. Progres upload tetap aman di sidecar, jadi percobaan
    berikutnya melanjutkan dari part terakhir.
    """


_PREMIUM_WAIT_RE = re.compile(r"FLOOD_PREMIUM_WAIT_(\d+)")

# File di atas ambang ini di-upload lewat SaveBigFilePartRequest yang bisa
# di-resume (sidecar JSON), bukan satu panggilan client.send_file() monolitik.
_RESUME_MIN_SIZE_BYTES = 64 * 1024 * 1024

# Batas JUMLAH part upload per jenis akun (MAX_FILE_PARTS_FREE = 4000 part
# ≈ 2 GB, MAX_FILE_PARTS_PREMIUM = 8000 part ≈ 4 GB) diimpor dari
# bot.telegram_limits supaya dipakai bersama dengan ambang split di
# bot.video_splitter. Nilainya diverifikasi live pada akun bot ini, 26 Sep
# 2026: SaveBigFilePartRequest dengan file_total_parts=4000 diterima,
# sedangkan 4001 dan 8000 ditolak server dengan FILE_PARTS_INVALID.


def _flood_wait_seconds(exc: BaseException, default: int = 30) -> int:
    """Durasi tunggu (detik) yang diminta Telegram pada error flood.

    `FloodWaitError` (RPC 429) menyediakan `.seconds`. Varian RPC 420 lain —
    misalnya `FLOOD_PREMIUM_WAIT_3` pada upload file besar — hanya menyertakan
    angka di pesan, jadi angka itu yang diambil. Untuk varian PREMIUM, durasi
    di pesan (mis. 3 detik) bukan penalti sebenarnya melainkan sinyal kuota
    lelah, sehingga digenjot ke minimal TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS.
    """
    text = str(exc)
    premium = _PREMIUM_WAIT_RE.search(text)
    if premium is None and "premium" in text.lower():
        if getattr(exc, "code", None) == 420 or _is_code_420(exc):
            premium = re.search(r"(\d+)", text)
    seconds = getattr(exc, "seconds", None)
    if premium is not None:
        return max(int(premium.group(1)), Config.TELEGRAM_FLOOD_PREMIUM_WAIT_SECONDS)
    if isinstance(seconds, (int, float)) and seconds > 0:
        return int(seconds) + 5
    match = re.search(r"FLOOD_\w*WAIT_(\d+)", text)
    if match:
        return int(match.group(1)) + 5
    match = re.search(r"wait of (\d+) seconds", text, re.IGNORECASE)
    if match:
        return int(match.group(1)) + 5
    return max(5, int(default))


def _is_code_420(exc: BaseException) -> bool:
    """RPC 420 dikenali juga dari prefix pesan ('A wait of ...' milik 420)."""
    return int(getattr(exc, "code", 0) or 0) == 420


def _flood_wait_with_jitter(seconds: int) -> int:
    """Jeda flood + jitter acak 0–30% agar retry tidak serempak (thundering herd)."""
    return int(seconds * (1 + random.uniform(0.0, 0.3)))


async def _rpc(awaitable, what: str, timeout: float) -> "object":
    """Jalankan satu request Telethon dengan batas waktu keras.

    Tanpa ``wait_for``, satu koneksi TCP yang tidak maju membuat ``await``
    menggantung selamanya: upload tidak pernah selesai, tidak pernah melempar
    error, lock upload tidak pernah dilepas, dan notifikasi admin ikut
    membeku karena memakai lock yang sama (insiden 30 Sep 2026). Timeout
    diubah jadi ``TelegramUploadStalled`` supaya pemanggil memperlakukannya
    sebagai kondisi transien (retry dari sidecar), bukan error tak terduga.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TelegramUploadStalled(
            f"Telegram tidak merespons dalam {int(timeout)} detik saat {what}"
        ) from exc


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

    def _upload_guard(self) -> asyncio.Lock:
        """Lock yang dibuat lazy untuk MENYERIALISKAN upload ke Telegram.

        Rate limit Telegram dihitung per AKUN, bukan per file. Rekaman live
        (1 GB ≈ 1.638 request 512 KB) dan arsip TikTok berjalan pada task
        berbeda; bila keduanya upload bersamaan, keduanya memakai kuota
        request yang sama dan file besar kena flood jauh lebih awal.

        Bukti dari log VPS 26 Sep 2026: upload 819 MB mulai 02:29:33,
        tiga story TikTok terkirim 02:29:34–02:29:57, lalu file live
        kena flood pada 37,5 MB (4,6%) — bukan pada 800 MB. Setelah
        serialisasi, tiap upload mendapat kuota penuh.

        Dibuat lazy (bukan di __init__) agar instance yang dibangun lewat
        `TelegramSender.__new__` pada test tetap punya lock.
        """
        lock = getattr(self, "_upload_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._upload_lock = lock
        return lock

    def _notify_guard(self) -> asyncio.Lock:
        """Lock terpisah untuk notifikasi TEKS.

        Notifikasi tidak memakai ``_upload_guard`` dengan sengaja. Notifikasi
        beberapa ratus byte tidak berebut kuota request dengan upload file
        besar, sedangkan memakai lock yang sama berarti satu upload yang
        menggantung ikut membekukan seluruh notifikasi admin — persis gejala
        insiden 30 Sep 2026 (notifikasi "UPLOAD TELEGRAM" masuk, lalu tidak
        ada lagi notifikasi apa pun, termasuk penanda kegagalan). Lock ini
        hanya mencegah notifikasi-notifikasi saling berebut di antara
        themselves.
        """
        lock = getattr(self, "_notify_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._notify_lock = lock
        return lock

    async def _max_file_parts(self) -> int:
        """Batas jumlah part upload yang diizinkan server untuk akun ini.

        Akun biasa 4000 part (~2 GB/file), Premium 8000 part (~4 GB/file).
        Status Premium dibaca sekali lalu di-cache; bila akun tidak bisa
        dibaca (mis. client tiruan pada unit test), dipakai batas akun biasa
        karena itu asumsi paling aman.
        """
        cached = getattr(self, "_max_file_parts_cached", None)
        if cached is not None:
            return cached
        try:
            me = await self._client.get_me()
            premium = bool(getattr(me, "premium", False))
        except Exception:  # noqa: BLE001 - client tiruan / sesi belum siap
            premium = False
        limit = MAX_FILE_PARTS_PREMIUM if premium else MAX_FILE_PARTS_FREE
        self._max_file_parts_cached = limit
        logger.info(
            "Batas upload akun Telegram: %d part (~%.1f GB/file), akun %sPremium.",
            limit, limit * 512 * 1024 / 1024 ** 3, "" if premium else "bukan ",
        )
        return limit

    async def _split_limit_bytes(self) -> int:
        """Ambang split yang aman untuk kapasitas part akun ini.

        Menggabungkan kehendak config dengan batas nyata akun: akun biasa
        maksimal 1800 MB (4000 part × 512 KiB dikurangi margin 10%), akun
        Premium boleh memakai angka config sampai ~3.6 GB. Angka inilah yang
        dipakai ``split_video_if_needed`` sehingga file besar selalu dipotong
        di bawah jumlah part yang diterima server.
        """
        capacity = safe_split_bytes(await self._max_file_parts())
        return min(Config.TELEGRAM_MAX_FILE_SIZE_MB * 1024 * 1024, capacity)

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
        # Flood dibatasi 3 jeda berturut; sesudah itu menyerah (return None)
        # agar satu pesan notifikasi tidak menahan antrean upload video.
        flood_waits = 0
        while True:
            try:
                async with self._notify_guard():
                    msg = await _rpc(
                        self._client.send_message(
                            target,
                            text,
                            parse_mode="html",
                            link_preview=link_preview,
                        ),
                        f"mengirim pesan notifikasi ke {target}",
                        Config.TELEGRAM_NOTIFY_TIMEOUT_SECONDS,
                    )
                logger.info("Telegram notification sent to %d. Message ID: %d", target, msg.id)
                return msg.id
            except FloodError as exc:
                # RPC 420 (mis. FLOOD_PREMIUM_WAIT_*) bukan FloodWaitError; bila
                # tidak tertangani ia jatuh ke except Exception dari percobaan kedua.
                flood_waits += 1
                if flood_waits > 3:
                    logger.error(
                        "Gagal mengirim pesan Telegram: flood tidak berhenti "
                        "setelah %d jeda (%s)", flood_waits - 1, exc,
                    )
                    return None
                wait = _flood_wait_with_jitter(_flood_wait_seconds(exc))
                logger.warning("Telegram flood %ds saat kirim pesan. Menunggu...", wait)
                await asyncio.sleep(wait)
            except TelegramUploadStalled as exc:
                # Timeout = koneksi macet, bukan flood. Notifikasi yang gagal
                # tidak boleh menahan antrean upload, jadi langsung menyerah
                # (retry upload berikutnya akan mengirim notifikasi ulang).
                logger.error("Notifikasi Telegram gagal: %s", exc)
                return None
            except Exception as exc:
                logger.error("Failed to send Telegram message: %s", exc)
                return None

    async def _upload_with_resume(
        self,
        path: Path,
        size: int,
        mtime: float,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> "types.TypeInputFile":
        """Upload file besar per-part via Save(Big)FilePartRequest, bisa di-resume.

        Progres disimpan di sidecar JSON ``<path>.tgup.json`` agar flood atau
        proses yang mati tidak mengulangi upload dari byte 0 (insiden 26 Sep:
        6 restart × ~1 GB). FloodError sengaja TIDAK ditangkap di sini agar
        loop retry di pemanggil melanjutkan dari ``parts_sent`` terakhir.
        """
        sidecar = Path(str(path) + ".tgup.json")
        part_size = utils.get_appropriated_part_size(size) * 1024
        total_parts = (size + part_size - 1) // part_size
        is_big = size > 10 * 1024 * 1024

        # Cek batas server SEBELUM byte pertama dikirim. Telethon sendiri boleh
        # memecah file jadi 6000 part, tapi server menolak request part pertama
        # dengan FILE_PARTS_INVALID begitu total part melewati batas akun —
        # errornya kriptik dan baru muncul setelah file siap dibaca dari disk.
        max_parts = await self._max_file_parts()
        if total_parts > max_parts:
            raise TelegramFileTooLarge(
                f"{path.name} ({size / 1024 ** 3:.2f} GB) butuh {total_parts} part "
                f"({part_size // 1024} KB/part) padahal server hanya mengizinkan "
                f"{max_parts} part (~{max_parts * part_size / 1024 ** 3:.2f} GB) "
                f"untuk akun ini. Naikkan akun ke Telegram Premium (~4 GB/file) "
                f"atau turunkan TELEGRAM_MAX_FILE_SIZE_MB agar video di-split "
                f"di bawah batas."
            )

        state: dict = {}
        if sidecar.exists():
            try:
                state = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        # Sesi upload besar di server TIDAK bertahan selamanya: Telegram
        # membuang part yang belum dirakit setelah jendela tertentu. Melanjutkan
        # sidecar yang sudah terlalu basi berarti mengupload seluruh sisa file
        # hanya untuk ditolak `FILE_PART_MISSING` (terukur 1 Okt 2026: 528 MB
        # naik ke 100%, ditolak "Part 680 missing", lalu diulang dari nol).
        # lebih baik: buang sekalian, lalu mulai dari nol tanpa satu pun byte
        # terbuang. Resume tetap berguna untuk jeda singkat (flood, restart bot)
        # yang justru kasus resume yang sesungguhnya.
        max_age_hours = int(Config.TELEGRAM_UPLOAD_RESUME_MAX_AGE_HOURS)
        if state and max_age_hours > 0:
            try:
                age_hours = (time.time() - sidecar.stat().st_mtime) / 3600
            except OSError:
                age_hours = 0.0
            if age_hours > max_age_hours:
                logger.warning(
                    "Sidecar upload %s berumur %.0f jam (batas %d jam); sesi "
                    "server kemungkinan sudah dibuang, jadi progress %.1f%% "
                    "dibuang dan upload diulang dari nol.",
                    path.name, age_hours, max_age_hours,
                    state.get("parts_sent", 0) * 100 / max(1, total_parts),
                )
                state = {}
        if not (
            state.get("path") == str(path)
            and state.get("size") == size
            and state.get("mtime") == mtime
            and state.get("part_size") == part_size
            and isinstance(state.get("file_id"), int)
            and isinstance(state.get("parts_sent"), int)
            and 0 <= state["parts_sent"] <= total_parts
        ):
            state = {
                "path": str(path),
                "size": size,
                "mtime": mtime,
                "file_id": helpers.generate_random_long(),
                "part_size": part_size,
                "total_parts": total_parts,
                "parts_sent": 0,
            }

        file_id = state["file_id"]
        parts_sent = state["parts_sent"]

        def _persist() -> None:
            sidecar.write_text(
                json.dumps(state), encoding="utf-8"
            )

        if parts_sent >= total_parts:
            logger.info("Upload %s sudah lengkap di sidecar; langsung kirim.", path.name)
        else:
            if parts_sent:
                logger.info(
                    "Resume upload %s dari part %d/%d (%.1f%%).",
                    path.name, parts_sent, total_parts,
                    parts_sent / total_parts * 100,
                )
            with open(path, "rb") as handle:
                handle.seek(parts_sent * part_size)
                for part_index in range(parts_sent, total_parts):
                    part = handle.read(part_size)
                    if is_big:
                        request = functions.upload.SaveBigFilePartRequest(
                            file_id, part_index, total_parts, part
                        )
                    else:
                        request = functions.upload.SaveFilePartRequest(
                            file_id, part_index, part
                        )
                    result = await _rpc(
                        self._client(request),
                        f"mengirim part {part_index + 1}/{total_parts} {path.name}",
                        Config.TELEGRAM_PART_UPLOAD_TIMEOUT_SECONDS,
                    )
                    if not result:
                        raise RuntimeError(
                            f"Failed to upload file part {part_index}."
                        )
                    parts_sent = part_index + 1
                    state["parts_sent"] = parts_sent
                    _persist()
                    if progress_callback:
                        sent = min(parts_sent * part_size, size)
                        await helpers._maybe_await(progress_callback(sent, size))
                    # Pacing: jeda di antara part supaya laju upload tetap di
                    # bawah batas aman akun. Tanpa jeda, bot mendorong ~1,8 MB/s
                    # dan kena FLOOD_PREMIUM_WAIT 930-1042 detik SETELAH ~20 MB.
                    # Perhitungannya: 15 menit penalti untuk 20 MB jauh lebih
                    # buruk daripada transfer 2x lebih lambat yang jalan terus -
                    # laju 1 MB/s membuat file 332 MB selesai dalam ~5 menit,
                    # bukan ~4 jam. Part terakhir tidak perlu jeda.
                    delay = int(Config.TELEGRAM_UPLOAD_PART_DELAY_MS) / 1000
                    if delay > 0 and part_index + 1 < total_parts:
                        await asyncio.sleep(delay)

        # Pemanggil hanya memakai jalur ini untuk file >= 64 MB, jadi hasilnya
        # selalu InputFileBig (>10 MB).
        return types.InputFileBig(file_id, total_parts, path.name)

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

        # File besar di-upload lewat jalur resume (SaveBigFilePartRequest per
        # part + sidecar JSON); file kecil tetap memakai send_file() biasa.
        size = path.stat().st_size
        mtime = path.stat().st_mtime
        resumable = size >= _RESUME_MIN_SIZE_BYTES
        sidecar = Path(str(path) + ".tgup.json")

        # Flood adalah kondisi sementara, jadi diberi jatah retry sendiri
        # (TELEGRAM_FLOOD_MAX_RETRIES); error biasa tetap 3 percobaan.
        max_attempts = max(3, Config.TELEGRAM_FLOOD_MAX_RETRIES + 1)
        flood_attempts = 0
        for attempt in range(max_attempts):
            try:
                # Lock menahan upload lain (mis. arsip TikTok) sampai file ini
                # selesai, supaya file besar mendapat kuota request penuh.
                async with self._upload_guard():
                    if resumable:
                        input_file = await self._upload_with_resume(
                            path, size, mtime, _default_progress
                        )
                        msg = await _rpc(
                            self._client.send_file(
                                target,
                                file=input_file,
                                caption=caption,
                                parse_mode="html",
                                attributes=attributes,
                                thumb=thumb,
                                supports_streaming=True,
                            ),
                            f"membuat pesan untuk {path.name}",
                            Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
                        )
                    else:
                        msg = await _rpc(
                            self._client.send_file(
                                target,
                                file=str(path),
                                caption=caption,
                                parse_mode="html",
                                attributes=attributes,
                                thumb=thumb,
                                supports_streaming=True,
                                progress_callback=_default_progress,
                            ),
                            f"mengunggah {path.name}",
                            Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
                        )
                sidecar.unlink(missing_ok=True)
                logger.info("Successfully uploaded video %s to %d (Message ID: %d)", path.name, target, msg.id)
                return msg.id

            except FloodError as exc:
                # Menangkap FloodWaitError (RPC 429) sekaligus varian RPC 420
                # seperti FLOOD_PREMIUM_WAIT_*. Progres upload part-file besar
                # sudah tersimpan di sidecar sehingga retry melanjutkan dari
                # part terakhir, bukan mengulang upload dari 0%.
                flood_attempts += 1
                # Jatah habis → berhenti sebelum mencoba upload lagi. File &
                # sidecar tetap di disk; siklus retry berikutnya me-resume.
                if flood_attempts >= Config.TELEGRAM_FLOOD_MAX_RETRIES:
                    size_mb = path.stat().st_size / (1024 * 1024)
                    raise TelegramFloodExhausted(
                        f"Telegram tetap flood setelah {flood_attempts} percobaan "
                        f"untuk {path.name} ({size_mb:.0f} MB)"
                    )
                wait = _flood_wait_with_jitter(_flood_wait_seconds(exc))
                logger.warning(
                    "Telegram flood %ds saat upload %s (flood %d/%d). "
                    "Menunggu sesuai durasi Telegram + jitter…",
                    wait, path.name, flood_attempts,
                    Config.TELEGRAM_FLOOD_MAX_RETRIES,
                )
                await asyncio.sleep(wait)
            except FilePartMissingError as exc:
                # Sesi upload besar di sisi server sudah TIDAK ADA lagi, jadi
                # me-resume ke file_id lama tidak akan pernah berhasil (terukur
                # 1 Okt 2026: sidecar dari ~30 jam sebelumnya masih mencatat
                # 1007 dari 1403 part "terkirim", padahal server sudah membuang
                # sebagian - part 532 hilang dan SendMediaRequest menolak dengan
                # FILE_PART_MISSING). Satu-satunya jalan keluar: buang sidecar
                # dan unggah ulang dari nol pada percobaan berikutnya. Tanpa ini
                # error yang sama diulang 7 kali, lalu file menyerah dan arsipnya
                # hilang padahal upload ulang hanya butuh beberapa menit.
                had_sidecar = sidecar.exists()
                sidecar.unlink(missing_ok=True)
                if had_sidecar and attempt < max_attempts - 1:
                    logger.warning(
                        "Sesi upload %s kedaluwarsa di server (%s); mengulang "
                        "upload dari awal (percobaan %d/%d).",
                        path.name, exc, attempt + 1, max_attempts,
                    )
                    continue
                logger.error(
                    "Sesi upload %s kedaluwarsa di server dan tidak bisa "
                    "dipulihkan: %s", path.name, exc,
                )
                return None
            except MediaEmptyError:
                # Media ditolak permanen (mis. story TikTok yang formatnya
                # tidak didukung sebagai album) — mengulang tidak menolong.
                sidecar.unlink(missing_ok=True)
                logger.exception(
                    "Upload ditolak Telegram untuk %s (media tidak valid); "
                    "tidak diulang.", path.name,
                )
                return None
            except TelegramUploadStalled as exc:
                # Server tidak merespons. Lock upload sudah dilepas (async with
                # keluar) supaya file lain dan notifikasi admin tidak ikut
                # tersumbat. Sidecar dibuang dengan sengaja: file_id MTProto
                # hanya valid selama sesi upload masih hidup di sisi server,
                # dan setelah timeout reference-nya bisa saja sudah kedaluwarsa
                # — me-resume ke file_id yang sudah dibuang akan gagal dengan
                # error yang jauh lebih sulit dibaca daripada upload ulang.
                sidecar.unlink(missing_ok=True)
                logger.error(
                    "Upload %s macet: %s (sidecar dibuang, upload ulang dari awal)",
                    path.name, exc,
                )
                return None
            except TelegramFileTooLarge as exc:
                # Batas jumlah part adalah sifat akun, bukan kondisi sementara:
                # diulang 10 kali pun hasilnya sama, jadi langsung menyerah
                # (file tetap di disk, bisa di-split ulang manual atau dikirim
                # setelah akun naik Premium).
                sidecar.unlink(missing_ok=True)
                logger.error("Upload %s dibatalkan sebelum mulai: %s", path.name, exc)
                return None
            except Exception as exc:
                logger.exception(
                    "Upload error for %s (percobaan %d): %s", path.name, attempt + 1, exc
                )
                if attempt >= max_attempts - 1:
                    return None
                await asyncio.sleep(5)

        return None

    async def _send_parts_album(
        self,
        parts: list["VideoPart"],
        caption: str,
        thumb_path: Optional[Path],
        target: int,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[list[int]]:
        """Kirim semua part hasil split sebagai SATU pesan album (media group).

        Telegram menganggapnya beberapa media dalam satu pesan (album), bukan
        satu file raksasa — persis perilaku yang diharapkan untuk video >2 GB.
        Batas album Telegram adalah 10 media per pesan.

        Setiap part >= 64 MB di-upload lewat jalur resume (sidecar JSON), jadi
        flood/restart tidak mengulang byte yang sudah terkirim. Message ID
        urut sesuai urutan part.

        Return ``None`` bila album ditolak (mis. media tidak valid) sehingga
        pemanggil bisa jatuh ke pengiriman per-part; FloodError naik ke
        pemanggil supaya retry/cooldown worker yang mengatur jeda.
        """
        await self.connect()
        entity = await self._client.get_input_entity(target)

        caption_result = await self._client._parse_message_text(caption or "", "html")

        thumb_handle = None
        if thumb_path and Path(thumb_path).exists():
            thumb_handle = await _rpc(
                self._client.upload_file(str(thumb_path)),
                "mengunggah thumbnail",
                Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
            )

        # Flood dihormati seperti jalur lain: jeda sesuai durasi Telegram,
        # habis jatah → TelegramFloodExhausted agar retry worker memberi
        # cooldown. Progres part besar aman di sidecar sehingga retry album
        # melanjutkan dari byte terakhir, bukan mengulang file dari nol.
        flood_attempts = 0
        while True:
            try:
                ids = await self._send_parts_album_once(
                    entity, parts, caption_result, thumb_handle, progress_callback
                )
            except FloodError as exc:
                flood_attempts += 1
                if flood_attempts >= Config.TELEGRAM_FLOOD_MAX_RETRIES:
                    raise TelegramFloodExhausted(
                        f"Telegram tetap flood setelah {flood_attempts} "
                        f"percobaan album ({len(parts)} part)"
                    )
                wait = _flood_wait_with_jitter(_flood_wait_seconds(exc))
                logger.warning(
                    "Telegram flood %ds saat mengirim album part. Menunggu...",
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            break

        # Album sukses → sidecar resume tiap part sudah tidak diperlukan;
        # hapus agar tidak menumpuk di disk (per-part path menghapusnya di
        # send_video_file, jalur album harus membersihkannya sendiri).
        for part in parts:
            try:
                Path(str(part.file_path) + ".tgup.json").unlink(missing_ok=True)
            except OSError:
                pass
        logger.info(
            "Album %d part terkirim ke %d dalam 1 pesan. Message IDs: %s",
            len(parts), target, ids,
        )
        return ids

    async def _send_parts_album_once(
        self,
        entity,
        parts: list["VideoPart"],
        caption_result,
        thumb_handle,
        progress_callback: Optional[Callable[[int, int], None]],
    ) -> list[int]:
        """Satu percobaan pengiriman album; FloodError dibiarkan naik."""
        async with self._upload_guard():
            media = []
            for index, part in enumerate(parts):
                size = part.size_bytes
                mtime = part.file_path.stat().st_mtime
                if size >= _RESUME_MIN_SIZE_BYTES:
                    file_handle = await self._upload_with_resume(
                        part.file_path, size, mtime, progress_callback
                    )
                else:
                    file_handle = await _rpc(
                        self._client.upload_file(
                            str(part.file_path), progress_callback=progress_callback
                        ),
                        f"mengunggah {part.file_path.name}",
                        Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
                    )
                uploaded = types.InputMediaUploadedDocument(
                    file=file_handle,
                    mime_type="video/mp4",
                    attributes=[
                        DocumentAttributeVideo(
                            duration=int(part.duration_seconds or 0),
                            w=int(part.width or 0),
                            h=int(part.height or 0),
                            supports_streaming=True,
                        )
                    ],
                    thumb=thumb_handle,
                    ttl_seconds=None,
                    nosound_video=True,
                )
                response = await _rpc(
                    self._client(
                        functions.messages.UploadMediaRequest(entity, media=uploaded)
                    ),
                    f"membuat media album untuk {part.file_path.name}",
                    Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
                )
                media.append(
                    types.InputSingleMedia(
                        utils.get_input_media(
                            response.document, supports_streaming=True
                        ),
                        message=caption_result[0] if index == 0 else "",
                        entities=caption_result[1] if index == 0 else None,
                    )
                )
            request = functions.messages.SendMultiMediaRequest(
                entity, multi_media=media
            )
            result = await _rpc(
                self._client(request),
                f"mengirim album {len(media)} part",
                Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
            )
            random_ids = [m.random_id for m in media]
            messages = self._client._get_response_message(random_ids, result, entity)

        if not isinstance(messages, list):
            messages = [messages]
        return [m.id for m in messages if m is not None]

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
        existing_message_ids: Optional[list[int]] = None,
        on_part_sent: Optional[Callable[[list[int]], None]] = None,
    ) -> list[int]:
        """
        Splits video if > 2GB (TELEGRAM_MAX_FILE_SIZE_MB) and uploads all parts
        to the Telegram channel.

        Video hasil split (2..10 part) dikirim sebagai SATU pesan album
        (media group); part lebih dari 10 atau resume parsial tetap dikirim
        per-part seperti semula.

        `platform` ('idn'/'showroom') dipakai untuk header caption agar rekaman
        Showroom tidak lagi dilabeli "IDN LIVE REPLAY".

        Returns a list of sent message IDs.
        """
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Video file not found on disk: {path}")

        logger.info("Preparing video for Telegram upload: %s", path.name)

        # Keep all post-split preparation inside the cleanup scope.  A thumbnail
        # error or an invalid durable prefix must not leave generated parts on
        # disk for a later retry to rediscover.
        parts: list[VideoPart] = []
        thumb_path: Optional[Path] = None
        try:
            # 1. Split video into parts if needed — ambangnya mengikuti jumlah
            #    part yang benar-benar diterima server untuk akun ini.
            max_part_count = await self._max_file_parts()
            parts = await split_video_if_needed(
                path, max_bytes=await self._split_limit_bytes()
            )
            total_parts = len(parts)

            # Guard pasca-split: ffmpeg memotong berbasis DURASI pada stream
            # VBR, jadi satu part bisa membengkak melewati batas part server
            # (FILE_PARTS_INVALID — ditolak mentah, tanpa resume). Gagalkan
            # SEKARANG sebelum byte pertama naik, bukan di tengah upload yang
            # akan meninggalkan arsip parsial.
            hard_limit = max_file_bytes(max_part_count)
            oversized = [p for p in parts if p.size_bytes > hard_limit]
            if oversized:
                names = ", ".join(
                    f"{p.file_path.name} ({p.size_bytes / 1024 ** 3:.2f} GB)"
                    for p in oversized
                )
                raise TelegramFileTooLarge(
                    f"Hasil split {path.name} memuat part di atas batas akun "
                    f"(~{hard_limit / 1024 ** 3:.2f} GB): {names}. Turunkan "
                    f"TELEGRAM_MAX_FILE_SIZE_MB atau naikkan akun ke Premium."
                )

            # 2. Generate a thumbnail frame for the video
            thumb_path = await generate_thumbnail(path)

            sent_message_ids: list[int] = list(existing_message_ids or [])
            if len(sent_message_ids) > total_parts:
                raise ValueError(
                    f"Telegram partial marker has {len(sent_message_ids)} IDs, "
                    f"but current split has only {total_parts} parts"
                )
            if sent_message_ids:
                logger.info(
                    "Resuming Telegram archive for %s at part %d/%d",
                    path.name,
                    len(sent_message_ids) + 1,
                    total_parts,
                )
            start_index = len(sent_message_ids)

            # Video hasil split (2..10 part, belum ada part terkirim) dikirim
            # sebagai SATU pesan album: Telegram menampilkannya sebagai media
            # group, bukan N pesan terpisah. Bila album ditolak (return None)
            # atau jumlah part > 10, jatuh ke pengiriman per-part di bawah.
            if 10 >= total_parts > 1 and start_index == 0:
                album_caption = build_telegram_video_caption(
                    member_name=member_name,
                    member_username=member_username,
                    started_at=started_at,
                    live_title=live_title,
                    part_number=1,
                    total_parts=1,
                    file_size_bytes=sum(p.size_bytes for p in parts),
                    platform=platform,
                ) + f"\n📁 Terbagi {total_parts} part dalam 1 album"
                try:
                    album_ids = await self._send_parts_album(
                        parts,
                        album_caption,
                        thumb_path,
                        channel_id or Config.TELEGRAM_CHANNEL_ID,
                        progress_callback=progress_callback,
                    )
                except TelegramFloodExhausted:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Pengiriman album %s gagal (%s); jatuh ke pengiriman "
                        "per-part. Byte yang sudah ter-upload aman di sidecar.",
                        path.name, exc,
                    )
                    album_ids = None
                if album_ids is not None:
                    if len(album_ids) != total_parts:
                        logger.warning(
                            "Album %s mengembalikan %d message ID untuk %d "
                            "part; ID yang tersedia tetap dipakai.",
                            path.name, len(album_ids), total_parts,
                        )
                    sent_message_ids.extend(album_ids)
                    if on_part_sent is not None:
                        on_part_sent(list(sent_message_ids))
                    return sent_message_ids

            for part in parts[start_index:]:
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
                    if on_part_sent is not None:
                        on_part_sent(list(sent_message_ids))
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
            cleanup_video_parts(parts)
            if thumb_path is not None:
                try:
                    Path(thumb_path).unlink(missing_ok=True)
                except Exception:
                    pass

    async def send_tiktok_archive(
        self,
        media,
        *,
        post: dict,
        account: dict,
        channel_id: Optional[int] = None,
    ) -> list[int]:
        """
        Kirim media TikTok (video / foto / story) ke channel arsip.

        - VIDEO  : dikirim sebagai video; bila melebihi batas ukuran Telegram,
                   dipecah memakai `split_video_if_needed` seperti replay.
        - FOTO   : dikirim sebagai album (maksimum 10 foto/album) sehingga
                   postingan dengan > 10 foto menjadi beberapa part.
        - STORY  : selalu video (TikTok hanya menyediakan video untuk story).

        Returns:
            Daftar message_id URUT PART (kosong bila tidak ada yang terkirim).
        """
        target = channel_id or Config.TELEGRAM_ARCHIVE_CHANNEL_ID or Config.TELEGRAM_CHANNEL_ID
        kind = (post.get("kind") or "video").strip() or "video"
        is_story = bool(post.get("is_story"))
        unique_id = (post.get("unique_id") or account.get("unique_id") or "").strip()
        member_name = (account.get("display_name") or account.get("member_username")
                       or unique_id)
        title = post.get("title") or ""
        created_at = post.get("created_at") or ""
        source_url = post.get("source_url") or ""

        sent_ids: list[int] = []

        # ── FOTO: album per part ────────────────────────────────────────────
        parts = list(getattr(media, "image_parts", []) or [])
        if parts:
            total = len(parts)
            for index, files in enumerate(parts, start=1):
                size_bytes = sum(Path(f).stat().st_size for f in files if Path(f).exists())
                caption = build_tiktok_caption(
                    member_name=member_name,
                    unique_id=unique_id,
                    created_at=created_at,
                    title=title,
                    kind=kind,
                    is_story=is_story,
                    part_number=index,
                    total_parts=total,
                    file_size_bytes=size_bytes,
                    source_url=source_url,
                )
                ids = await self._send_media_album(files, caption, target)
                if not ids:
                    # Album sebagian = arsip tidak lengkap. Melewati part ini
                    # seperti sebelumnya membuat `telegram_message_ids` terisi
                    # parsial: website lalu menampilkan jako yang siap padahal
                    # fotonya kurang, dan `get_tiktok_youtube_backlog`
                    # mengira arsipnya "sudah aman". Lebih baik gagal total dan
                    # dicoba ulang utuh.
                    raise RuntimeError(
                        f"Album foto part {index}/{total} gagal terkirim ke "
                        f"Telegram untuk {unique_id}"
                    )
                sent_ids.extend(ids)
            return sent_ids

        # ── VIDEO: satu file (dipecah bila terlalu besar) ────────────────────
        video_path = getattr(media, "archive_video_path", None)
        if not video_path or not Path(video_path).exists():
            logger.warning("Tidak ada media untuk dikirim ke Telegram (post %s)", post.get("id"))
            return []

        video_parts = await split_video_if_needed(
            Path(video_path), max_bytes=await self._split_limit_bytes()
        )
        total = len(video_parts)
        try:
            for part in video_parts:
                caption = build_tiktok_caption(
                    member_name=member_name,
                    unique_id=unique_id,
                    created_at=created_at,
                    title=title,
                    kind=kind,
                    is_story=is_story,
                    part_number=part.part_number,
                    total_parts=total,
                    file_size_bytes=part.size_bytes,
                    source_url=source_url,
                )
                # TelegramFloodExhausted sengaja dibiarkan naik: kegagalan
                # flood adalah TRANSien — melanjutkan part berikutnya hanya
                # membuang bandwidth, dan mengembalikan id parsial akan
                # menandai arsip sukses sebagian.
                message_id = await self.send_video_file(
                    file_path=part.file_path,
                    caption=caption,
                    duration=part.duration_seconds,
                    width=part.width,
                    height=part.height,
                    channel_id=target,
                )
                if message_id:
                    sent_ids.append(message_id)
                else:
                    # JANGAN lanjut ke part berikutnya lalu mengembalikan id
                    # parsial: marker parsial itu membuat website menampilkan
                    # konten setengah jadi, dan `get_tiktok_youtube_backlog`
                    # menganggap arsipnya sudah aman lalu menerbitkannya ke
                    # YouTube. Kegagalan satu part berarti arsip ini tidak pernah
                    # masuk catatan, dan akan dicoba ulang utuh.
                    logger.error(
                        "Part %d/%d (%s) gagal terkirim; arsip %s dibatalkan "
                        "agar tidak tercatat sebagai sukses (sudah terkirim: %s)",
                        part.part_number, total, part.file_path.name,
                        unique_id, sent_ids or "tidak ada",
                    )
                    raise RuntimeError(
                        f"Part {part.part_number}/{total} gagal terkirim ke "
                        f"Telegram untuk {unique_id}"
                    )
        finally:
            # Bersih-bersih part WAJIB jalan juga saat flood exhaustion.
            cleanup_video_parts(video_parts)
        return sent_ids

    async def _send_media_album(
        self,
        files: list,
        caption: str,
        target: int,
    ) -> list[int]:
        """
        Kirim satu album Telegram (maksimum 10 media) + caption.

        Telethon mengembalikan LIST pesan untuk album (satu pesan per media);
        fungsi ini menormalkannya menjadi daftar message_id. Satu kegagalan
        album tidak melempar exception ke pemanggil (log + []).
        """
        paths = [str(Path(f)) for f in files if Path(f).exists()]
        if not paths:
            return []
        await self.connect()

        # Force-document WAJIB untuk foto TikTok. Tanpa ini Telethon mengirim
        # gambar sebagai foto inline dan Telegram menampilkannya sebagai
        # STIKER (WebP adalah format stiker resmi Telegram) - user tidak bisa
        # mengunduhnya sebagai berkas. Sebagai document, foto tiba sebagai
        # file JPEG/PNG biasa yang selalu bisa diunduh apa adanya, dan album
        # tetap dikelompokkan rapi per 10 media.
        as_document = Config.TIKTOK_PHOTOS_AS_DOCUMENT

        # Satu berkas TIDAK boleh dikirim sebagai album. Telethon meneruskan
        # list apa pun ke `_send_album` -> `SendMultiMediaRequest`, dan Telegram
        # menolak album satu media dengan `MediaEmptyError` (terjadi pada
        # story TikTok berupa 1 foto, 26 Sep 2026). Album 1 berkas = kirim
        # sebagai media biasa agar tidak pernah ditolak.
        if len(paths) == 1:
            flood_attempts = 0
            for attempt in range(3):
                try:
                    async with self._upload_guard():
                        msg = await _rpc(
                            self._client.send_file(
                                target,
                                paths[0],
                                caption=caption,
                                parse_mode="html",
                                force_document=as_document,
                            ),
                            f"mengirim media tunggal {Path(paths[0]).name}",
                            Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
                        )
                    logger.info(
                        "Media tunggal TikTok terkirim ke %d (Message ID: %d): %s",
                        target, msg.id, Path(paths[0]).name,
                    )
                    return [msg.id]
                except FloodError as exc:
                    # Flood pada media tunggal juga harus dihormati; habis
                    # jatah → raise agar pemanggil memberi cooldown, bukan
                    # diam-diam kehilangan arsip (return []).
                    flood_attempts += 1
                    if flood_attempts >= Config.TELEGRAM_FLOOD_MAX_RETRIES:
                        raise TelegramFloodExhausted(
                            f"Telegram tetap flood setelah {flood_attempts} "
                            f"percobaan media tunggal {Path(paths[0]).name}"
                        )
                    wait = _flood_wait_with_jitter(_flood_wait_seconds(exc))
                    logger.warning(
                        "Telegram flood %ds saat mengirim media tunggal. "
                        "Menunggu...", wait,
                    )
                    await asyncio.sleep(wait)
                except MediaEmptyError:
                    logger.exception(
                        "Media tunggal ditolak Telegram (%s); tidak diulang.",
                        Path(paths[0]).name,
                    )
                    return []
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Gagal mengirim media TikTok: %s", exc)
                    return []
            return []

        flood_attempts = 0
        for attempt in range(3):
            try:
                async with self._upload_guard():
                    result = await _rpc(
                        self._client.send_file(
                            target,
                            paths,
                            caption=caption,
                            parse_mode="html",
                            force_document=as_document,
                        ),
                        f"mengirim album {len(paths)} media TikTok",
                        Config.TELEGRAM_SEND_TIMEOUT_SECONDS,
                    )
                messages = result if isinstance(result, list) else [result]
                ids = [m.id for m in messages if m is not None]
                logger.info(
                    "Album TikTok (%d media) terkirim ke %d. Message IDs: %s",
                    len(paths), target, ids,
                )
                return ids
            except FloodError as exc:
                # Sama seperti send_video_file: variant RPC 420 (mis.
                # FLOOD_PREMIUM_WAIT_*) harus dihormati durasinya. Kehabisan
                # jatah flood melempar TelegramFloodExhausted (transien),
                # bukan [] yang akan dianggap arsip permanen gagal.
                flood_attempts += 1
                if flood_attempts >= Config.TELEGRAM_FLOOD_MAX_RETRIES:
                    raise TelegramFloodExhausted(
                        f"Telegram tetap flood setelah {flood_attempts} "
                        f"percobaan album ({len(paths)} berkas)"
                    )
                wait = _flood_wait_with_jitter(_flood_wait_seconds(exc))
                logger.warning(
                    "Telegram flood %ds saat mengirim album. Menunggu...", wait
                )
                await asyncio.sleep(wait)
            except MediaEmptyError:
                # Album ditolak permanen (mis. satu-satunya media adalah video
                # story). Tiga percobaan sia-sia dan hanya memperlambat antrean.
                logger.exception(
                    "Album ditolak Telegram (media tidak valid, %d berkas); "
                    "tidak diulang.", len(paths),
                )
                return []
            except Exception as exc:  # noqa: BLE001
                logger.exception("Gagal mengirim album TikTok (percobaan %d/3): %s", attempt + 1, exc)
                if attempt == 2:
                    return []
                await asyncio.sleep(5)
        return []


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


def build_tiktok_notification(
    member_name: str,
    unique_id: str,
    posted_at: str = "",
    video_id: str = "",
    title: str = "",
    kind: str = "video",
    is_story: bool = False,
) -> str:
    """
    Notifikasi publik ke channel Telegram saat arsip TikTok naik ke YouTube.

    Sama seperti jalur replay: Telegram hanya memuat tautan YouTube Unlisted,
    bukan berkas videonya.
    """
    yt_url = f"https://youtu.be/{video_id}" if video_id else ""
    header = _TIKTOK_HEADERS.get((kind, bool(is_story)), "🎵 <b>TIKTOK</b>")
    posted = _format_live_time(posted_at)

    lines = [
        header,
        "",
        f"👤 <b>{member_name or unique_id}</b> ({_dot_at(unique_id)})",
    ]
    if title:
        lines.append(f"📌 {title[:600]}")
    if posted:
        lines.append(f"📅 {posted}")
    if yt_url:
        lines.append("")
        lines.append(f"▶️ <b>Watch:</b> <a href=\"{yt_url}\">YouTube Unlisted</a>")
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


# ─── Arsip TikTok ───────────────────────────────────────────────────────────
#
# Caption & pengiriman media TikTok (video, foto/slide, story) ke channel arsip.
# Postingan FOTO dikirim sebagai album Telegram (maksimum 10 foto per album),
# sehingga postingan dengan > 10 foto otomatis menjadi beberapa part.

_TIKTOK_HEADERS = {
    ("photo", False): "🖼️ <b>TIKTOK FOTO</b>",
    ("photo", True): "🖼️ <b>TIKTOK STORY (FOTO)</b>",
    ("video", False): "🎵 <b>TIKTOK VIDEO</b>",
    ("video", True): "📱 <b>TIKTOK STORY</b>",
}


def build_tiktok_caption(
    member_name: str,
    unique_id: str,
    created_at: str = "",
    title: str = "",
    kind: str = "video",
    is_story: bool = False,
    part_number: int = 1,
    total_parts: int = 1,
    file_size_bytes: int = 0,
    source_url: str = "",
) -> str:
    """
    Caption media TikTok untuk channel arsip.

    Header mengikuti jenis media: 'TIKTOK VIDEO' / 'TIKTOK FOTO' /
    'TIKTOK STORY'. Foto dengan > 10 gambar dipecah sehingga headernya memuat
    penanda part seperti caption replay multi-part.
    """
    header = _TIKTOK_HEADERS.get((kind, bool(is_story)), "🎵 <b>TIKTOK</b>")
    if total_parts > 1:
        header += f" <b>[Part {part_number}/{total_parts}]</b>"

    created = _format_live_time(created_at)
    lines = [
        header,
        "",
        f"👤 <b>{member_name or unique_id}</b> ({_dot_at(unique_id)})",
    ]
    if title:
        # Deskripsi TikTok bisa sangat panjang → potong agar caption tetap rapi.
        lines.append(f"📌 {title[:600]}")
    if created:
        lines.append(f"📅 {created}")
    if total_parts > 1:
        lines.append(
            f"📁 Part {part_number} dari {total_parts}"
            + (f" ({_format_size(file_size_bytes)})" if file_size_bytes else "")
        )
    elif file_size_bytes > 0:
        lines.append(f"📁 Ukuran: {_format_size(file_size_bytes)}")
    if source_url:
        lines.append("")
        lines.append(f"🔗 <a href=\"{source_url}\">Lihat di TikTok</a>")

    return "\n".join(lines)

