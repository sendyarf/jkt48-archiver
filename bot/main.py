"""
main.py - JKT48 Live Bot main orchestrator.

A streamlined, robust bot that:
  1. Records directly from permanent member HLS URLs (AWS IVS).
  2. Discovers HLS URLs for new members automatically via IDN GraphQL.
  3. Supports concurrent recordings across multiple members.
  4. Merges reconnect segments if a member rejoins within 1 hour.
  5. Uploads all completed videos to YouTube as Unlisted (Multi-channel pool to avoid quotas).
  6. If YouTube daily quota is exceeded, keeps video safely on disk and queues for retry.
  7. Sends notification text with YouTube watch link to Telegram.
"""
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

import colorlog

from bot.config import Config, warn_env_overrides
from bot import database
from bot.showroom_monitor import (
    RoomState,
    ShowroomMonitor,
    build_live_id,
    detection_gap_seconds,
    should_resume_showroom,
)
from bot.timeutil import utc_now_iso, utc_now
from bot.admin_bot import AdminBot
from bot.downloader import (
    download_stream,
    delete_file,
    get_file_size_bytes,
    cancel_download,
    DownloadError,
)
from bot.hls_discovery import HLSDiscovery
from bot.hls_monitor import HLSMonitor
from bot.idn_lookup import IDNLookup
from bot.merger import MergeManager
from bot.telegram_sender import TelegramSender, build_youtube_notification
from bot.replay_bot import ReplayBot
from bot.youtube_uploader import YouTubeChannelPool, YouTubeQuotaExceeded

# Ukuran minimal file parsial agar layak didaftarkan sebagai segmen saat shutdown
# (lihat JKT48LiveBot._salvage_partial_segments). ~5 MB ≈ puluhan detik video:
# file lebih kecil dari ini kemungkinan container-nya belum final sehingga bisa
# merusak hasil penggabungan.
_MIN_PARTIAL_BYTES = 5 * 1024 * 1024

# ─── Logging Setup ────────────────────────────────────────────────────────────
def _setup_logging() -> None:
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s [%(levelname)s] %(name)s%(reset)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers
    if not root.handlers:
        root.addHandler(handler)

    # Silence noisy HTTP client & external libraries (prevents 404 polling spam)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


logger = logging.getLogger("jkt48_bot")


class JKT48LiveBot:
    """Main orchestrator for monitoring, recording, merging, and uploading JKT48 lives."""

    def __init__(self) -> None:
        database.init_db()  # ensure all tables exist before any component accesses the DB
        self.tg = TelegramSender()
        self.yt_pool = YouTubeChannelPool()
        self.hls_discovery = HLSDiscovery()
        self.hls_monitor = HLSMonitor()
        self.idn_lookup = IDNLookup()
        self.showroom = ShowroomMonitor(
            offline_confirmations=Config.SHOWROOM_OFFLINE_CONFIRMATIONS,
            concurrency=Config.SHOWROOM_CONCURRENCY,
            timeout_seconds=Config.SHOWROOM_TIMEOUT_SECONDS,
        )
        self.merge_mgr = MergeManager(
            on_upload_ready=self.handle_upload_ready,
            probe_active=self._probe_member_active,
            fetch_live=self._fetch_member_live,
        )

        self.active_recordings: set[str] = set()  # username IDN yang sedang direkam
        # Showroom dipisah dari IDN supaya member yang live di dua platform
        # sekaligus tetap bisa direkam keduanya tanpa saling memblokir.
        self.active_showroom: set[str] = set()  # username Showroom yang sedang direkam
        self.active_live_ids: dict[str, str] = {}  # kunci -> live_id sedang direkam
        self.recording_tasks: set[asyncio.Task] = set()
        self.admin_bot: Optional[AdminBot] = None
        self.replay_bot: Optional[ReplayBot] = None
        self._stopped_members_logged: set[str] = set()
        self.running = False

    async def initialize(self) -> None:
        """Initialize database, connections, and sync members whitelist."""
        logger.info("Initializing database and tables...")
        database.init_db()
        database.clean_interrupted_downloads()

        # Connect to Telegram
        logger.info("Connecting to Telegram...")
        await self.tg.connect()

        # Recover any stuck merge groups from previous run
        await self.merge_mgr.recover_stuck_groups()

        # Initial members sync
        self.sync_members_whitelist()

        # Check and process pending uploads in background
        asyncio.create_task(self.retry_pending_uploads())

        # Telegram admin bot (long polling) — control channels from Telegram
        self.admin_bot = AdminBot(on_stop_recording=self._cancel_active_recording)
        if self.admin_bot.start():
            logger.info("Telegram admin bot listener started.")

        # Telegram replay bot (publik, long polling) — download via deep-link
        self.replay_bot = ReplayBot()
        if self.replay_bot.start():
            logger.info("Telegram replay bot listener started.")

        logger.info("Bot initialization completed successfully.")

    def sync_members_whitelist(self) -> None:
        """
        Read members.txt and ensure they exist in member_hls table.

        Sinkronisasi ini hanya MENAMBAH member (INSERT ... DO NOTHING), jadi member
        yang di-stop (enabled = 0) atau dihapus tidak akan hidup kembali hanya
        karena masih tertulis di members.txt.
        """
        members = Config.load_members()
        if members:
            database.register_members_if_not_exists(list(members))
            logger.debug("Synced %d members from %s to database", len(members), Config.MEMBERS_FILE)

            # Warning kalau ada member di members.txt yang statusnya STOP
            # (mis. di-stop lewat CLI/admin bot) — hanya dilaporkan sekali saat berubah.
            stopped = {
                m["username"]
                for m in database.get_all_member_hls(include_disabled=True)
                if not m.get("enabled", 1) and m["username"] in members
            }
            if stopped != self._stopped_members_logged:
                new_ones = stopped - self._stopped_members_logged
                cleared = self._stopped_members_logged - stopped
                if new_ones:
                    logger.warning(
                        "Member ada di members.txt tapi berstatus STOP (tidak direkam): %s "
                        "— jalankan `resume` via Telegram/CLI untuk mengaktifkan lagi.",
                        ", ".join(sorted(new_ones)),
                    )
                if cleared:
                    logger.info("Member kembali AKTIF: %s", ", ".join(sorted(cleared)))
                self._stopped_members_logged = stopped

    def _cancel_active_recording(self, username: str) -> str:
        """
        Callback dari Telegram admin bot (/stop): hentikan rekaman yang sedang
        berjalan untuk member ini secara graceful (SIGTERM ke yt-dlp).

        Segmen yang sudah terekam tetap disimpan, masuk merge group, lalu
        diupload seperti biasa — jadi tidak ada data yang hilang.
        """
        username = username.lower()
        # Kunci Showroom memakai prefix agar tidak bertabrakan dengan IDN saat
        # member yang sama sedang direkam di dua platform.
        candidates = [username, f"sr:{username}"]
        was_recording = False
        stopped = False
        for key in candidates:
            live_id = self.active_live_ids.get(key)
            if not live_id:
                continue
            was_recording = True
            if cancel_download(live_id):
                stopped = True
                logger.info("Admin menghentikan rekaman aktif %s (kunci=%s)", username, key)

        # Member ini memang tidak sedang direkam → tidak perlu mengirim pesan apa pun.
        if not was_recording:
            return ""
        if stopped:
            return (
                "⏹ Rekaman yang sedang berjalan dihentikan (graceful). "
                "Segmen yang sudah terekam tetap akan diupload setelah merge window."
            )
        return "⚠️ Proses rekaman tidak ditemukan (mungkin sudah selesai)."

    async def _probe_member_active(self, username: str, platform: str = "idn") -> Optional[bool]:
        """
        Cek langsung apakah stream member masih mengalir.

        IDN     : 1 HTTP request ke URL HLS tetap (AWS IVS).
        Showroom: panggilan API room, TAPI dengan debounce — "offline" hanya
                  dilaporkan setelah beberapa pembacaan offline berturut-turut,
                  sehingga gangguan API sesaat tidak memecah live menjadi
                  beberapa video.

        Dipakai MergeManager untuk memastikan live benar-benar selesai sebelum
        merge+upload — sama sekali tanpa bergantung pada IDN.

        Return: True (masih aktif) / False (offline terkonfirmasi) / None (tidak diketahui).
        """
        u = username.lower()

        if platform == "showroom":
            room_id = database.get_member_showroom_room_id(u)
            if not room_id:
                return None
            return await self.showroom.is_room_live(room_id)

        row = database.get_member_hls(u)
        hls_url = (row or {}).get("hls_url") or ""
        if not hls_url:
            return None
        return await self.hls_monitor.is_stream_active(hls_url)

    async def _fetch_member_live(self, username: str, platform: str = "idn") -> Optional[dict]:
        """
        Info live IDN member (OPSIONAL):
          None                 → tidak diketahui (IDN down / dimatikan)
          {"slug": ""}         → IDN menjawab: member tidak live
          {"slug": "..."}      → member live (slug unik per sesi, mis. haii-260915223455)

        Untuk sesi Showroom, IDN tidak dipanggil sama sekali: judul/slug IDN tidak
        relevan dan hasilnya justru bisa menahan finalisasi karena grup Showroom
        dianggap "masih live di IDN".

        Kalau IDN bermasalah, hasilnya None dan MergeManager otomatis kembali ke
        logika murni HLS (perilaku lama) tanpa error.
        """
        if platform == "showroom":
            return None
        return await self.idn_lookup.fetch_member_live(username)

    async def handle_upload_ready(
        self,
        live_id: str,
        member_username: str,
        member_name: str,
        started_at: str,
        file_path: str,
        thumbnail_url: str = "",
        live_title: str = "",
        platform: str = "",
    ) -> None:
        """
        Callback executed by MergeManager when a video (single or merged)
        is ready to be uploaded to YouTube after the merge window expires.
        `platform` diteruskan oleh merger; bila kosong (mis. jalur pending
        upload lama), platform ditebak dari live_id / database.
        """
        path = Path(file_path)
        group_id: Optional[int] = None
        if live_id.startswith("merged_"):
            try:
                group_id = int(live_id.split("_")[1])
            except (IndexError, ValueError):
                pass

        # Platform untuk judul/deskripsi: dari merger bila ada, lalu prefix
        # live_id (`sr_`), lalu database (live_sessions → merge_groups).
        plat = platform or database.get_platform_for_live(live_id, group_id)

        def set_status(status: str, **kwargs) -> None:
            if group_id is not None:
                database.update_sessions_by_merge_group(group_id, status, **kwargs)
            else:
                database.update_status(live_id, status, **kwargs)

        if not path.exists():
            logger.error("Upload failed: file not found on disk: %s", file_path)
            set_status("failed", error_message="File not found")
            return

        file_size = get_file_size_bytes(path)

        if Config.UPLOAD_TARGET == "telegram":
            set_status(
                "uploading_telegram",
                file_path=str(path),
                file_size_bytes=file_size,
            )
            logger.info("Uploading video to Telegram for %s (%s): %s", member_name, live_id, path.name)

            try:
                msg_ids = await self.tg.upload_video_with_splitting(
                    file_path=path,
                    member_name=member_name or member_username,
                    member_username=member_username,
                    started_at=started_at,
                    live_title=live_title,
                )

                if msg_ids:
                    set_status(
                        "done_telegram",
                        telegram_message_id=msg_ids[0],
                    )
                    logger.info("Successfully uploaded to Telegram (%s). Message IDs: %s", live_id, msg_ids)

                    # Auto delete local file if configured
                    if Config.AUTO_DELETE_AFTER_UPLOAD:
                        delete_file(path)
                else:
                    logger.error("Telegram upload returned no message ID for %s", live_id)
                    set_status("pending_upload", error_message="Telegram upload returned no message ID")

            except Exception as exc:
                logger.exception("Error uploading %s to Telegram: %s", live_id, exc)
                set_status("pending_upload", error_message=str(exc))

        else:
            # ─── YouTube Upload Flow (preserved) ─────────────────────────
            set_status(
                "uploading_youtube",
                file_path=str(path),
                file_size_bytes=file_size,
            )

            title = self.yt_pool.build_title(
                member_name or member_username, started_at, plat
            )
            desc = self.yt_pool.build_description(
                member_name or member_username, member_username, started_at, plat
            )

            logger.info("Uploading video to YouTube for %s (%s): %s", member_name, live_id, title)

            try:
                video_id, channel_label = self.yt_pool.upload_video(path, title=title, description=desc)

                if video_id:
                    set_status(
                        "done_youtube",
                        youtube_video_id=video_id,
                    )
                    logger.info("Successfully uploaded to YouTube (%s). Video ID: %s", channel_label, video_id)

                    # Arsipkan juga ke channel Telegram privat (database replay)
                    archive_ok = await self._archive_to_telegram(
                        path=path,
                        member_name=member_name or member_username,
                        member_username=member_username,
                        started_at=started_at,
                        live_title=live_title,
                        live_id=live_id,
                        set_status=set_status,
                    )

                    # Send Telegram notification
                    msg_text = build_youtube_notification(
                        member_name=member_name or member_username,
                        member_username=member_username,
                        started_at=started_at,
                        video_id=video_id,
                        live_title=live_title,
                    )
                    msg_id = await self.tg.send_message(msg_text)
                    if msg_id:
                        set_status("done_youtube", telegram_message_id=msg_id)

                    # Auto delete local file only if EVERYTHING succeeded
                    if Config.AUTO_DELETE_AFTER_UPLOAD and archive_ok:
                        delete_file(path)
                else:
                    logger.error("YouTube upload returned no video ID for %s", live_id)
                    set_status("failed", error_message="YouTube upload failed")

            except YouTubeQuotaExceeded as q_exc:
                logger.warning("YouTube quota limit exceeded across all channels for %s: %s", live_id, q_exc)
                set_status("pending_upload", error_message=str(q_exc))
                # File stays on disk; notify admin if configured
                if Config.ADMIN_CHAT_ID:
                    await self.tg.send_message(
                        f"⚠️ <b>YouTube Quota Alert</b>\n"
                        f"Semua channel YouTube mencapai limit upload harian.\n"
                        f"Video untuk <b>{member_name}</b> disimpan di VPS dan masuk antrian upload.",
                        channel_id=Config.ADMIN_CHAT_ID,
                    )
            except Exception as exc:
                logger.exception("Unexpected error uploading %s to YouTube: %s", live_id, exc)
                set_status("failed", error_message=str(exc))

    async def _archive_to_telegram(
        self,
        *,
        path,
        member_name: str,
        member_username: str,
        started_at: str,
        live_title: str,
        live_id: str,
        set_status,
    ) -> bool:
        """
        Upload video hasil merge ke channel Telegram privat sebagai "database"
        replay, lalu simpan SEMUA message_id ke kolom telegram_message_ids.

        Mengembalikan True bila arsip sukses (atau fitur dimatikan); False bila
        gagal — dalam hal ini sesi ditandai pending_upload agar file lokal TIDAK
        dihapus dan bisa di-retry. Dipanggil hanya pada alur YouTube.
        """
        if not Config.TELEGRAM_ARCHIVE_UPLOAD_ENABLED:
            return True
        archive_channel = Config.TELEGRAM_ARCHIVE_CHANNEL_ID
        if not archive_channel:
            logger.warning(
                "TELEGRAM_ARCHIVE_UPLOAD_ENABLED=true tapi TELEGRAM_ARCHIVE_CHANNEL_ID "
                "kosong — arsip Telegram dilewati untuk %s.", live_id,
            )
            return True

        try:
            logger.info(
                "Archiving video to Telegram channel %s for %s (%s)",
                archive_channel, member_name, live_id,
            )
            msg_ids = await self.tg.upload_video_with_splitting(
                file_path=path,
                member_name=member_name,
                member_username=member_username,
                started_at=started_at,
                live_title=live_title,
                channel_id=archive_channel,
            )
            if msg_ids:
                joined = ",".join(str(m) for m in msg_ids)
                set_status("done_youtube", telegram_message_ids=joined)
                logger.info(
                    "Archived to Telegram (%s). Message IDs: %s", live_id, msg_ids,
                )
                return True
            logger.error("Telegram archive returned no message ID for %s", live_id)
            set_status(
                "pending_upload",
                error_message="Telegram archive upload returned no message ID",
            )
            return False
        except Exception as exc:
            logger.exception("Error archiving %s to Telegram: %s", live_id, exc)
            set_status("pending_upload", error_message=f"telegram archive: {exc}")
            return False

    async def _record_member_task(self, member: dict, live_id: str) -> None:
        """Background task that records a member's live stream via yt-dlp."""
        username = member["username"].lower()
        display_name = member.get("display_name") or username
        hls_url = member["hls_url"]
        started_at = utc_now_iso()

        logger.info("🔴 Starting recording task for %s (%s) [live_id=%s]",
                    display_name, username, live_id)

        # Daftarkan live_id aktif agar Telegram /stop bisa membatalkan rekaman ini
        self.active_live_ids[username] = live_id

        database.insert_live(
            live_id=live_id,
            member_username=username,
            member_name=display_name,
            started_at=started_at,
            hls_url=hls_url,
        )
        database.update_status(live_id, "downloading", download_started_at=started_at)
        self.merge_mgr.download_started(username, "idn")

        # Best-effort: ambil slug + judul live dari IDN secara paralel (tidak menambah
        # keterlambatan mulai merekam). Slug unik per sesi live → dipakai untuk
        # membedakan reconnect vs live baru. Kalau IDN down → kosong, bot tetap jalan.
        slug_task: Optional[asyncio.Task] = None
        if Config.IDN_LOOKUP_ENABLED:
            slug_task = asyncio.create_task(self._fetch_member_live(username))

        try:
            output_file = await download_stream(
                hls_url=hls_url,
                member_username=username,
                live_id=live_id,
            )

            file_size = get_file_size_bytes(output_file)
            database.update_status(
                live_id,
                "segment_done",
                download_ended_at=utc_now_iso(),
                file_path=str(output_file),
                file_size_bytes=file_size,
            )
            database.update_member_last_live(username)

            live_slug = ""
            live_title = ""
            if slug_task is not None:
                with contextlib.suppress(Exception):
                    live_info = await slug_task or {}
                if live_info.get("slug"):
                    live_slug = live_info["slug"]
                    live_title = live_info.get("title") or ""
                    database.set_session_live_slug(live_id, live_slug)

            logger.info(
                "Recording segment finished for %s. Adding to merge manager...%s",
                username,
                f" (slug: {live_slug})" if live_slug else "",
            )
            await self.merge_mgr.add_segment(
                live_id=live_id,
                member_username=username,
                member_name=display_name,
                started_at=started_at,
                file_path=str(output_file),
                live_title=live_title,
                live_slug=live_slug,
                platform="idn",
            )

        except DownloadError as exc:
            logger.warning("Download error for %s: %s", username, exc)
            database.update_status(live_id, "failed", error_message=str(exc))
        except Exception as exc:
            logger.exception("Unexpected recording error for %s: %s", username, exc)
            database.update_status(live_id, "failed", error_message=str(exc))
        finally:
            if slug_task is not None and not slug_task.done():
                slug_task.cancel()
            self.merge_mgr.download_ended(username, "idn")
            self.active_recordings.discard(username)
            self.active_live_ids.pop(username, None)

    async def _record_showroom_task(self, room: dict, live_id: str) -> None:
        """
        Task rekaman satu sesi Showroom.

        Alur sama dengan IDN (rekam → segmen → merge group → upload), dengan
        perbedaan yang disengaja:
          * `platform="showroom"` disimpan di live_sessions dan merge_groups.
          * Tidak ada slug/judul dari IDN — identitas sesi Showroom hanya
            `live_id` yang dibentuk dari `room_url_key`.
          * Cover room dipakai sebagai thumbnail, mengisi celah thumbnail web
            yang sebelumnya hanya berasal dari YouTube.
          * Judul grup memakai nama room Showroom, supaya arsip web punya
            konteks meski tanpa judul dari IDN.
          * Task TETAP TINGGAL sampai live benar-benar berakhir. Kalau yt-dlp
            berhenti lebih awal (HLS Showroom belum feeding, token kadaluarsa,
            hiccup CDN), task resume dengan URL HLS segar; setiap potongan
            resume jadi live_id `_r<N>` tersendiri dan tetap masuk satu merge
            group. Latar: insiden 18 Sep 2026 (Sona) — bot kehilangan ±8-10
            menit awal live karena task gagal cepat saat HLS belum siap.
        """
        username = (room.get("username") or "").lower()
        display_name = room.get("display_name") or username
        hls_url = room.get("hls_url") or ""
        room_id = str(room.get("room_id") or "")
        started_at = room.get("started_at") or utc_now_iso()
        room_name = room.get("room_name") or ""
        thumb = room.get("cover_image") or ""

        # Observability: seberapa telat kita mulai merekam sejak live resmi
        # dimulai (current_live_started_at). Telat = potongan awal hilang.
        gap = detection_gap_seconds(started_at)
        if gap is not None and gap >= Config.SHOWROOM_LATE_START_WARN_SECONDS:
            logger.warning(
                "⚠️ Showroom %s: mulai merekam %ds setelah live dimulai — "
                "potongan awal live tidak terekam (cek uptime bot / "
                "rate-limit API Showroom)",
                display_name, int(gap),
            )

        logger.info(
            "Showroom recording started for %s (room %s) [live_id=%s]",
            display_name, room_id, live_id,
        )

        self.active_live_ids[f"sr:{username}"] = live_id

        part_id = live_id
        database.insert_live(
            live_id=part_id,
            member_username=username,
            member_name=display_name,
            started_at=started_at,
            hls_url=hls_url,
            platform="showroom",
        )
        self.merge_mgr.download_started(username, "showroom")

        resumes_done = 0
        try:
            while True:
                database.update_status(
                    part_id, "downloading", download_started_at=utc_now_iso()
                )
                # Bagian resume memakai live_id `_r<N>` sendiri; pemetaan ini harus
                # ikut diperbarui supaya shutdown/admin bisa SIGTERM proses yang
                # benar (lihat _stop_recordings_gracefully).
                self.active_live_ids[f"sr:{username}"] = part_id
                try:
                    output_file = await download_stream(
                        hls_url=hls_url,
                        member_username=username,
                        live_id=part_id,
                        max_empty_retries=Config.SHOWROOM_EMPTY_RETRIES,
                    )
                except DownloadError as exc:
                    database.update_status(part_id, "failed", error_message=str(exc))
                    if not should_resume_showroom(
                        resumes_done,
                        Config.SHOWROOM_MAX_RESUMES,
                        await self._showroom_room_live(room_id),
                    ):
                        raise
                    resumes_done += 1
                    logger.info(
                        "Showroom %s: yt-dlp berhenti padahal live belum berakhir "
                        "— resume bagian %d dengan URL segar",
                        username, resumes_done,
                    )
                    await asyncio.sleep(Config.SHOWROOM_RESUME_DELAY_SECONDS)
                    part_id = f"{live_id}_r{resumes_done}"
                    hls_url = (
                        await self.showroom.scraper.get_live_streaming_url(room_id)
                        or hls_url
                    )
                    database.insert_live(
                        live_id=part_id,
                        member_username=username,
                        member_name=display_name,
                        started_at=started_at,
                        hls_url=hls_url,
                        platform="showroom",
                    )
                    continue

                file_size = get_file_size_bytes(output_file)
                database.update_status(
                    part_id,
                    "segment_done",
                    download_ended_at=utc_now_iso(),
                    file_path=str(output_file),
                    file_size_bytes=file_size,
                )
                database.update_member_last_live(username)

                logger.info(
                    "Showroom segment finished for %s [bagian %d]. "
                    "Adding to merge manager...",
                    username, resumes_done,
                )
                await self.merge_mgr.add_segment(
                    live_id=part_id,
                    member_username=username,
                    member_name=display_name,
                    started_at=started_at,
                    file_path=str(output_file),
                    thumbnail_url=thumb,
                    live_title=room_name,
                    live_slug="",          # Showroom tidak punya slug IDN
                    platform="showroom",
                )

                # yt-dlp keluar "bersih" padahal live masih jalan (mis. playlist
                # berhenti diperbarui sesaat) → lanjut bagian berikutnya.
                if not should_resume_showroom(
                    resumes_done,
                    Config.SHOWROOM_MAX_RESUMES,
                    await self._showroom_room_live(room_id),
                ):
                    break
                resumes_done += 1
                logger.info(
                    "Showroom %s: masih live setelah bagian selesai — "
                    "lanjut bagian %d",
                    username, resumes_done,
                )
                await asyncio.sleep(Config.SHOWROOM_RESUME_DELAY_SECONDS)
                part_id = f"{live_id}_r{resumes_done}"
                hls_url = (
                    await self.showroom.scraper.get_live_streaming_url(room_id)
                    or hls_url
                )
                database.insert_live(
                    live_id=part_id,
                    member_username=username,
                    member_name=display_name,
                    started_at=started_at,
                    hls_url=hls_url,
                    platform="showroom",
                )

        except DownloadError as exc:
            logger.warning("Showroom download error for %s: %s", username, exc)
        except Exception as exc:
            logger.exception("Unexpected Showroom recording error for %s: %s", username, exc)
            database.update_status(part_id, "failed", error_message=str(exc))
        finally:
            self.merge_mgr.download_ended(username, "showroom")
            self.active_showroom.discard(username)
            self.active_live_ids.pop(f"sr:{username}", None)

    async def _showroom_room_live(self, room_id: str) -> Optional[bool]:
        """
        Status live room untuk keputusan resume rekaman Showroom.

        Mengembalikan True (terbukti live) / False (offline terkonfirmasi
        lewat debounce) / None (tidak diketahui) dari ShowroomMonitor.
        """
        if not str(room_id or "").strip():
            # Tanpa room_id tidak ada yang bisa diprobe — perlakukan sebagai
            # "sudah berakhir" agar task tidak memanggil API dengan parameter
            # kosong dan perilaku lama (gagal → selesai) tetap terjaga.
            return False
        try:
            return await self.showroom.is_room_live(room_id)
        except Exception as exc:  # pragma: no cover - jalur jaringan
            logger.debug("Probe liveness Showroom %s gagal: %s", room_id, exc)
            return None

    async def _check_showroom(self) -> None:
        """
        Satu siklus pemantauan Showroom: cari room yang live lalu mulai merekam.

        Dipanggil dengan interval terpisah (SHOWROOM_CHECK_INTERVAL_SECONDS) yang
        lebih longgar daripada IDN, karena 58 room pada interval 5 detik berarti
        ~11,6 request/detik ke API Showroom — berisiko rate-limit.
        """
        rooms = database.get_members_with_showroom()
        candidates = [
            {
                "username": m["username"],
                "display_name": m.get("display_name") or m["username"],
                "name": m.get("showroom_name") or "",
                "room_id": str(m.get("showroom_room_id") or ""),
            }
            for m in rooms
            if m["username"].lower() not in self.active_showroom
        ]
        if not candidates:
            return

        live_rooms = await self.showroom.find_live_rooms(candidates)
        if not live_rooms:
            return

        logger.info("Showroom: %d room sedang live", len(live_rooms))
        for room in live_rooms:
            username = (room.get("username") or "").lower()
            if not username or username in self.active_showroom:
                continue
            if username in self.active_recordings:
                # Sedang direkam di IDN. Rekam Showroom juga (platform berbeda),
                # tapi beri tahu agar perilakunya jelas di log.
                logger.info(
                    "Showroom: %s juga sedang direkam di IDN — keduanya direkam "
                    "sebagai grup terpisah.", username,
                )

            state = RoomState(
                room_id=str(room.get("room_id") or ""),
                is_onlive=True,
                live_id=int(room.get("showroom_live_id") or 0),
                room_url_key=room.get("room_url_key") or "",
                room_name=room.get("room_name") or "",
                cover_image=room.get("cover_image") or "",
            )
            live_id = build_live_id(state)

            # Penghitung debounce direset karena sesi baru sudah dimulai.
            self.showroom.forget(state.room_id)

            self.active_showroom.add(username)
            task = asyncio.create_task(self._record_showroom_task(room, live_id))
            self.recording_tasks.add(task)
            task.add_done_callback(self.recording_tasks.discard)

    async def retry_pending_uploads(self) -> None:
        """Retry sessions that were previously paused or pending upload."""
        if Config.UPLOAD_TARGET == "telegram":
            pending = database.get_all_pending_videos()
        else:
            pending = database.get_pending_uploads_youtube()

        if not pending:
            return

        logger.info(
            "Found %d pending upload(s) in queue for target '%s'.",
            len(pending),
            Config.UPLOAD_TARGET,
        )
        for sess in pending:
            live_id = sess["live_id"]
            file_path = sess["file_path"]
            if not file_path or not Path(file_path).exists():
                logger.warning("Pending upload file missing on disk: %s. Marking failed.", file_path)
                database.update_status(live_id, "failed", error_message="File missing on disk")
                continue

            username = sess["member_username"]
            name = sess["member_name"] or username
            started_at = sess["started_at"] or sess["created_at"]

            await self.handle_upload_ready(
                live_id=live_id,
                member_username=username,
                member_name=name,
                started_at=started_at,
                file_path=file_path,
            )

    async def run(self) -> None:
        """Main polling loop."""
        self.running = True
        # Diagnostik .env SEKALI di awal: laporkan baris yang tidak terbaca dan
        # nilai .env yang dikalahkan environment proses (mis. cache pm2), supaya
        # salah-setel tidak berjalan diam-diam selama berhari-hari.
        warn_env_overrides()
        logger.info("Bot started. Monitoring HLS streams every %ds (Merge window: %ds)", Config.HLS_CHECK_INTERVAL_SECONDS, Config.MERGE_WINDOW_SECONDS)
        logger.info(
            "Merge policy → window %ds jeda liputan antar segmen | idle finalize %ds | "
            "hard cap %.1f jam | split-on-title: %s | IDN lookup: %s | Showroom: %s",
            Config.MERGE_WINDOW_SECONDS,
            Config.MERGE_IDLE_FINALIZE_SECONDS,
            Config.MERGE_MAX_GROUP_HOURS,
            "AKTIF" if Config.MERGE_SPLIT_ON_TITLE_CHANGE else "nonaktif",
            "aktif" if Config.IDN_LOOKUP_ENABLED else "NONAKTIF (HLS-only)",
            (
                f"AKTIF (polling %ds, debounce offline %dx)"
                % (Config.SHOWROOM_CHECK_INTERVAL_SECONDS, Config.SHOWROOM_OFFLINE_CONFIRMATIONS)
            ) if Config.SHOWROOM_ENABLED else "NONAKTIF",
        )

        # Peringatan setelan yang bisa MEMECAH satu live menjadi beberapa video.
        # Ditempatkan di startup agar salah-setel langsung terlihat di log.
        if Config.MERGE_SPLIT_ON_TITLE_CHANGE:
            logger.warning(
                "MERGE_SPLIT_ON_TITLE_CHANGE=AKTIF: member yang mengedit judul di "
                "tengah live akan terpecah menjadi beberapa video (bukti: Carissa "
                "15 Sep 2026, 3 judul dalam 11 menit = satu live)."
            )
        if Config.MERGE_IDLE_FINALIZE_SECONDS > 0:
            logger.warning(
                "MERGE_IDLE_FINALIZE_SECONDS=%ds: grup difinalisasi lebih cepat dari "
                "window saat stream terbaca offline; pastikan nilainya > jeda "
                "reconnect terpanjang, kalau tidak satu live bisa terpecah.",
                Config.MERGE_IDLE_FINALIZE_SECONDS,
            )

        # Showroom dipantau pada interval terpisah: 58 room tiap 5 detik akan
        # menghasilkan ~11,6 request/detik ke API Showroom (risiko rate-limit).
        showroom_every = max(
            1,
            round(Config.SHOWROOM_CHECK_INTERVAL_SECONDS / max(1, Config.HLS_CHECK_INTERVAL_SECONDS)),
        )
        loop_counter = 0

        while self.running:
            try:
                loop_counter += 1

                # 1. Hot-reload members.txt
                self.sync_members_whitelist()

                # 2. HLS Discovery: periodic check every ~2 minutes (8 loops) for unseeded members
                if loop_counter % 8 == 0:
                    missing = database.get_members_without_hls()
                    if missing:
                        discovered = await self.hls_discovery.discover_missing_members()
                        if discovered:
                            logger.info("Discovered %d new HLS URLs!", len(discovered))

                # 3. Check HLS status of all members with stored HLS
                #    Hanya member ENABLED (enabled = 1) yang di-probe — member
                #    yang di-stop lewat CLI/Telegram admin bot dilewati.
                all_members = database.get_all_member_hls(include_disabled=False)
                candidate_members = [
                    m for m in all_members
                    if m.get("hls_url") and m["username"].lower() not in self.active_recordings
                ]

                if candidate_members:
                    active_now = await self.hls_monitor.check_active_members(candidate_members)

                    for member in active_now:
                        u = member["username"].lower()
                        if u in self.active_recordings:
                            continue

                        self.active_recordings.add(u)
                        live_id = f"{u}_{int(utc_now().timestamp())}"

                        task = asyncio.create_task(self._record_member_task(member, live_id))
                        self.recording_tasks.add(task)
                        task.add_done_callback(self.recording_tasks.discard)

                # 4. Showroom (OPSIONAL): cari room yang sedang live.
                #    Interval terpisah + dibungkus try/except tersendiri supaya
                #    kegagalan API Showroom tidak pernah mengganggu jalur IDN.
                #    loop_counter == 1 → pindai langsung saat bot start agar
                #    live yang sudah berjalan sebelum restart tetap dikejar
                #    sejak detik pertama (potongan awal tidak hilang).
                if Config.SHOWROOM_ENABLED and (
                    loop_counter == 1 or loop_counter % showroom_every == 0
                ):
                    try:
                        await self._check_showroom()
                    except Exception as exc:
                        logger.warning("Pemeriksaan Showroom gagal (diabaikan): %s", exc)

                # 5. Periodic retry of pending uploads (every ~30 minutes or 120 ticks)
                if loop_counter % 120 == 0:
                    await self.retry_pending_uploads()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Unhandled error in main loop: %s", exc)

            await asyncio.sleep(Config.HLS_CHECK_INTERVAL_SECONDS)

    async def shutdown(self) -> None:
        """
        Graceful shutdown of all components and tasks.

        Rekaman aktif TIDAK langsung dibatalkan. yt-dlp di-SIGTERM lebih dulu
        supaya container file ditutup rapi, lalu task diberi waktu
        `GRACEFUL_SHUTDOWN_SECONDS` untuk menyelesaikan jalur normalnya
        (`segment_done` + masuk merge group). Tanpa ini, setiap `pm2 restart` di
        tengah live membuang potongan rekaman: sesinya masih berstatus
        'downloading' sehingga `clean_interrupted_downloads()` menghapusnya saat
        boot, dan file parsial yang sudah ditutup rapi tidak pernah diupload.
        """
        logger.info("Shutting down bot...")
        self.running = False

        # Stop Telegram admin bot listener
        if self.admin_bot:
            await self.admin_bot.stop()

        # Stop Telegram replay bot listener
        if self.replay_bot:
            await self.replay_bot.stop()

        await self._stop_recordings_gracefully()

        await self.merge_mgr.shutdown()
        await self.hls_discovery.close()
        await self.hls_monitor.close()
        await self.showroom.close()
        await self.tg.disconnect()
        logger.info("Bot shutdown complete.")

    async def _stop_recordings_gracefully(self) -> None:
        """
        Hentikan rekaman aktif tanpa membuang segmen parsial.

        Urutan: SIGTERM semua yt-dlp aktif → tunggu task selesai maksimal
        `GRACEFUL_SHUTDOWN_SECONDS` → batalkan paksa sisanya → selamatkan file
        parsial yang tertinggal. `active_live_ids` disalin SEBELUM apa pun
        dibatalkan karena task yang berhenti menghapus entri itu di blok
        `finally` — snapshot inilah yang dipakai untuk penyelamatan.
        """
        tasks = [t for t in list(self.recording_tasks) if not t.done()]
        if not tasks:
            return

        snapshot = dict(self.active_live_ids)
        for key, live_id in snapshot.items():
            if cancel_download(live_id):
                logger.info(
                    "Shutdown: SIGTERM yt-dlp %s [live_id=%s] — file parsial "
                    "ditutup rapi lalu didaftarkan sebagai segmen",
                    key, live_id,
                )

        grace = max(0, int(Config.GRACEFUL_SHUTDOWN_SECONDS))
        if grace:
            _, pending = await asyncio.wait(tasks, timeout=grace)
        else:
            pending = set(tasks)

        for task in pending:
            task.cancel()
        if not pending:
            logger.info("Shutdown: %d task rekaman selesai dengan rapi", len(tasks))
            return

        logger.warning(
            "Shutdown: %d task rekaman tidak selesai dalam %ds — dibatalkan paksa",
            len(pending), grace,
        )
        await asyncio.gather(*pending, return_exceptions=True)
        await self._salvage_partial_segments(snapshot)

    async def _salvage_partial_segments(self, snapshot: dict) -> None:
        """
        Daftarkan file parsial yang tertinggal sebagai segmen sah (best-effort).

        Jaring pengaman terakhir, hanya untuk kasus task yang sudah dibatalkan
        paksa: file yang cukup besar dianggap masih bisa diputar. Sesi seperti
        ini masuk merge group yang sama seperti segmen normal, sehingga grup
        tetap difinalisasi (atau dipulihkan saat boot oleh
        `MergeManager.recover_stuck_groups`).
        """
        directory = Path(Config.DOWNLOAD_DIR)
        for key, live_id in snapshot.items():
            try:
                username = key.split(":", 1)[-1].lower()
                session = database.get_session(live_id)
                if not session or (session["status"] or "") != "downloading":
                    continue
                path = self._find_partial_file(directory, username, live_id)
                if not path:
                    continue
                size = path.stat().st_size
                database.update_status(
                    live_id,
                    "segment_done",
                    download_ended_at=utc_now_iso(),
                    file_path=str(path),
                    file_size_bytes=size,
                )
                database.update_member_last_live(username)
                await self.merge_mgr.add_segment(
                    live_id=live_id,
                    member_username=username,
                    member_name=session["member_name"] or username,
                    started_at=session["started_at"] or utc_now_iso(),
                    file_path=str(path),
                    platform=session["platform"] or "idn",
                )
                logger.info(
                    "Shutdown: segmen parsial %s diselamatkan (%.1f MB) dan "
                    "dimasukkan ke merge group",
                    live_id, size / (1024 * 1024),
                )
            except Exception as exc:  # pragma: no cover - jaring pengaman
                logger.warning(
                    "Shutdown: gagal menyelamatkan segmen parsial %s: %s", live_id, exc
                )

    @staticmethod
    def _find_partial_file(directory: Path, username: str, live_id: str) -> Optional[Path]:
        """
        Cari file output yt-dlp milik sebuah sesi.

        Nama file dibuat `downloader._output_path` memakai timestamp saat itu
        (`<username>_<yyyymmdd_HHMMSS>_<live_id>.<ext>`), jadi timestamp tidak
        bisa direkonstruksi — dicari dengan glob lalu diambil yang terbesar
        (paling lengkap).
        """
        if not directory.exists():
            return None
        candidates: list[Path] = []
        for candidate in directory.glob(f"{username}_*_{live_id}.*"):
            try:
                if candidate.is_file() and candidate.stat().st_size >= _MIN_PARTIAL_BYTES:
                    candidates.append(candidate)
            except OSError:
                continue
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_size)


async def main() -> None:
    _setup_logging()
    bot = JKT48LiveBot()

    loop = asyncio.get_running_loop()

    # Graceful shutdown handler
    def _signal_handler():
        logger.info("Termination signal received.")
        asyncio.create_task(bot.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Signal handling might differ on Windows
            pass

    try:
        await bot.initialize()
        await bot.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interrupt received, exiting...")
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
