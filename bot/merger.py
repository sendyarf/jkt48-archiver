"""
merger.py - Auto-merge segments so that ONE live = ONE video.

Segmen yang selesai (termasuk setelah lag/reconnect) digabung ke satu merge group
per member, lalu diupload setelah live benar-benar selesai.

Kapan sebuah group dianggap selesai (finalize)?
  1. `MERGE_WINDOW_SECONDS` habis sejak **SEGMEN TERAKHIR** (batas atas pengaman), ATAU
  2. IDN Lookup (OPSIONAL) memastikan member TIDAK live lagi + HLS offline, ATAU
  3. HLS offline & idle >= `MERGE_IDLE_FINALIZE_SECONDS` (percepatan upload), ATAU
  4. Hard cap `MERGE_MAX_GROUP_HOURS` tercapai (anti stream "hang" tanpa disconnect).

Finalize SELALU ditunda selama masih ada rekaman berjalan, dan penundaan dilakukan
dengan menjadwalkan ulang (bukan membatalkan), sehingga segmen berikutnya tetap masuk
ke group yang sama.
"""
import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from bot import timeutil
from bot.config import Config
from bot.database import (
    create_merge_group,
    set_session_merge_group,
    get_merge_group,
    get_merge_group_timing,
    get_active_merge_group,
    get_merge_segments,
    get_waiting_merge_groups,
    get_orphaned_segments,
    close_merge_group,
    fail_merge_group,
    update_merge_group_last_segment,
    update_merge_group_slug,
    mark_session_failed,
)
from bot.downloader import delete_file
from bot.idn_lookup import live_key_from

logger = logging.getLogger(__name__)

MERGE_WINDOW = Config.MERGE_WINDOW_SECONDS
IDLE_FINALIZE_SECONDS = Config.MERGE_IDLE_FINALIZE_SECONDS
MAX_GROUP_SECONDS = int(Config.MERGE_MAX_GROUP_HOURS * 3600)
DEFER_SECONDS = Config.MERGE_DEFER_SECONDS
SPLIT_ON_TITLE_CHANGE = Config.MERGE_SPLIT_ON_TITLE_CHANGE


class MergeManager:
    """
    Tracks completed downloads and merges segments from the same member
    that belong to the same live session (or within the reconnection window).
    """

    def __init__(
        self,
        on_upload_ready: Optional[Callable] = None,
        probe_active: Optional[Callable[..., Awaitable[Optional[bool]]]] = None,
        fetch_live: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
    ) -> None:
        """
        on_upload_ready: async callback function signature:
          async def callback(live_id: str, member_username: str, member_name: str,
                             started_at: str, file_path: str, thumbnail_url: str,
                             live_title: str)

        probe_active (WAJIB efektif): async callback (username, platform)
          -> True (stream masih mengalir) / False (offline) / None (tidak diketahui)

        fetch_live (OPSIONAL, IDN — boleh dimatikan/tidak tersedia): async callback
          (username, platform)
          -> None (tidak diketahui) / {"slug": ""} (tidak live) / {"slug": "..."} (live)

        `platform` ('idn' | 'showroom') diteruskan ke callback supaya member yang
        punya dua platform sekaligus di-probe pada sumber yang benar.
        """
        self._on_upload_ready = on_upload_ready
        self._probe_active = probe_active
        self._fetch_live = fetch_live
        self._timers: dict[str, asyncio.Task] = {}  # scope -> timer task
        self._active_downloads: dict[str, int] = {}  # scope -> count of in-flight downloads
        self._lock = asyncio.Lock()

    @staticmethod
    def _scope(member_username: str, platform: str = "idn") -> str:
        """
        Kunci internal per (member, platform).

        Tanpa pemisahan ini, rekaman IDN dan Showroom milik orang yang sama akan
        saling menghitung sebagai "sedang merekam", sehingga finalisasi tertunda
        tanpa alasan dan timer bisa saling membatalkan.
        """
        return f"{member_username.lower()}|{(platform or 'idn').lower()}"

    def download_started(self, member_username: str, platform: str = "idn") -> None:
        """Mark that a recording is currently in flight for this member+platform."""
        key = self._scope(member_username, platform)
        self._active_downloads[key] = self._active_downloads.get(key, 0) + 1

    def download_ended(self, member_username: str, platform: str = "idn") -> None:
        """Mark that a recording has finished for this member+platform."""
        key = self._scope(member_username, platform)
        count = self._active_downloads.get(key, 0)
        if count <= 1:
            self._active_downloads.pop(key, None)
        else:
            self._active_downloads[key] = count - 1

    async def add_segment(
        self,
        live_id: str,
        member_username: str,
        member_name: str,
        started_at: str,
        file_path: str,
        thumbnail_url: str = "",
        live_title: str = "",
        live_slug: str = "",
        platform: str = "idn",
    ) -> None:
        """
        Called after a segment download completes.
        Appends segment to current active merge group (or creates one), then resets the timer
        from this (newest) segment.

        Bila `live_slug`/`live_title` (dari IDN, OPSIONAL) menunjukkan JUDUL live yang
        berbeda dari grup yang sedang menunggu -> member memulai LIVE BARU: grup lama
        difinalize lebih dulu (diupload terpisah), segmen ini membuka grup baru.

        CATATAN PENTING: pembandingnya adalah JUDUL live (`live_key`), BUKAN slug,
        karena slug IDN berubah setiap reconnect (kasus nyata jkt48_daisy):
            haii-260913192155 -> haii-260913201248 -> haii-260913201503  (satu live sama)

        `platform` memisahkan grup IDN dan Showroom untuk member yang sama. Showroom
        tidak punya judul dari IDN, jadi `live_slug`/`live_title` dibiarkan kosong dan
        deteksi live-baru-via-judul otomatis tidak aktif untuk platform ini.
        """
        u = member_username.lower()
        scope = self._scope(u, platform)
        live_key = live_key_from(live_slug, live_title)
        async with self._lock:
            group = get_active_merge_group(u, MERGE_WINDOW, platform=platform)

            # Deteksi live baru via JUDUL live (slug mentah TIDAK dipakai)
            if group and live_key and SPLIT_ON_TITLE_CHANGE:
                group_key = (group.get("live_key") or "").strip()
                if group_key and group_key != live_key:
                    logger.info(
                        "%s: JUDUL LIVE BERUBAH ('%s' -> '%s', grup %d) = live baru: "
                        "grup lama difinalize dulu, lalu mulai grup baru",
                        u, group_key, live_key, group["id"],
                    )
                    self._cancel_timer(u, platform)
                    await self._merge_and_upload(group["id"], u)
                    group = None

            if group:
                group_id = group["id"]
                logger.info("%s: adding segment to existing merge group %d (extending timer %ds)",
                            u, group_id, MERGE_WINDOW)
                update_merge_group_last_segment(group_id)
                update_merge_group_slug(group_id, live_slug, live_title, live_key)
            else:
                group_id = create_merge_group(
                    u, member_name, started_at,
                    thumbnail_url=thumbnail_url, live_title=live_title,
                    platform=platform,
                )
                update_merge_group_slug(group_id, live_slug, live_title, live_key)
                logger.info("%s: started new merge group %d (platform %s, window %ds, judul '%s')",
                            u, group_id, platform, MERGE_WINDOW, live_key or "-")

            set_session_merge_group(live_id, group_id)

            # Cancel previous timer and start a fresh timer from this segment
            self._cancel_timer(u, platform)
            self._timers[scope] = asyncio.create_task(
                self._finalize_group(u, group_id, platform)
            )

    def _cancel_timer(self, member_username: str, platform: str = "idn") -> None:
        task = self._timers.pop(self._scope(member_username, platform), None)
        if task and not task.done():
            task.cancel()

    async def recover_stuck_groups(self) -> None:
        """
        Re-schedule finalize for any 'waiting' merge group that still has
        segments but no running timer (e.g. after bot restart).
        Also adopts any orphaned segment_done sessions into a merge group.
        """
        # Adopt any orphaned segment_done sessions without a waiting merge group
        orphans = get_orphaned_segments()
        for s in orphans:
            u = s["member_username"].lower()
            name = s.get("member_name") or u
            started = s.get("started_at") or s.get("created_at") or timeutil.utc_now_iso()
            orphan_platform = (s.get("platform") or "idn")
            gid = create_merge_group(u, name, started, platform=orphan_platform)
            set_session_merge_group(s["live_id"], gid)
            logger.info("Adopted orphaned session %s (%s) into new merge group %d",
                        s["live_id"], u, gid)

        groups = get_waiting_merge_groups()
        for g in groups:
            member = g["member_username"].lower()
            gid = g["id"]
            platform = (g.get("platform") or "idn")
            scope = self._scope(member, platform)
            if scope in self._timers:
                continue
            segments = get_merge_segments(gid)
            if not segments:
                continue

            # Sisa waktu dihitung dari jarak SEGMEN TERAKHIR, dihitung di SQL
            # (UTC vs UTC). Jangan pakai datetime.now() terhadap kolom SQLite
            # karena kolom tsb UTC → selisihnya salah sebesar offset zona waktu.
            idle = get_merge_group_timing(gid)["idle_seconds"]
            remaining = max(0.0, MERGE_WINDOW - idle)

            delay = remaining if remaining > 5 else 5
            logger.info("Recovering pending merge group %d for %s (%s, delay: %.0fs)",
                        gid, member, platform, delay)
            self._timers[scope] = asyncio.create_task(
                self._finalize_group(member, gid, delay, platform)
            )

    async def _finalize_group(
        self,
        member_username: str,
        group_id: int,
        delay_seconds: Optional[float] = None,
        platform: str = "idn",
    ) -> None:
        """
        Tunggu window (atau sisa waktunya), lalu putuskan: finalize atau tunda lagi.
        """
        wait_time = delay_seconds if delay_seconds is not None else MERGE_WINDOW
        try:
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            return

        async with self._lock:
            self._timers.pop(self._scope(member_username, platform), None)

            group = get_merge_group(group_id)
            if not group or group.get("status") != "waiting":
                # Sudah diproses (mis. grup lama di-finalize saat live baru terdeteksi)
                return

            action, delay = await self._decide(group)
            if action == "finalize":
                await self._merge_and_upload(group_id, member_username)
                return

            logger.info(
                "%s: merge group %d ditunda %.0fs (menunggu live benar-benar selesai)",
                member_username, group_id, delay,
            )
            self._timers[self._scope(member_username, platform)] = asyncio.create_task(
                self._finalize_group(member_username, group_id, delay, platform)
            )

    async def _decide(self, group: dict) -> tuple:
        """
        Tentukan ("finalize" | "defer", delay_detik) untuk sebuah merge group.

        Urutan keputusan:
          1. hard cap tercapai                → finalize paksa (kecuali masih merekam)
          2. masih ada rekaman berjalan       → defer
          3. Probe platform (HLS IDN / API Showroom):
             masih aktif                      → defer   (tunggu segmen berikutnya)
             terbukti offline & idle cukup    → finalize
             belum diketahui                  → jangan finalize lebih awal
          4. IDN (hanya platform idn): judul live berubah → finalize (LIVE BARU)
          5. window habis sejak segmen terakhir → finalize
          6. selain itu                        → defer sampai sisa window habis

        `platform` diambil dari kolom grup. Untuk Showroom, `fetch_live` (IDN) tidak
        dipanggil sama sekali dan probe memakai API Showroom dengan debounce offline
        (lihat bot/showroom_monitor.py) sehingga satu hiccup API tidak memecah live.
        """
        u = (group.get("member_username") or "").lower()
        platform = (group.get("platform") or "idn")
        # Timing dihitung di SQL (UTC vs UTC) — jangan pakai datetime.now() (lokal)
        # terhadap kolom yang diisi SQLite dengan datetime('now') (UTC).
        timing = get_merge_group_timing(group.get("id"))
        idle = timing["idle_seconds"]
        age = timing["age_seconds"]
        recording = self._scope(u, platform) in self._active_downloads

        # 1. Hard cap — anti stream "hang" tanpa disconnect
        if MAX_GROUP_SECONDS > 0 and age >= MAX_GROUP_SECONDS:
            if recording:
                # Jangan potong rekaman yang sedang jalan; cek lagi nanti
                return "defer", DEFER_SECONDS
            logger.warning(
                "%s: hard cap %.1f jam tercapai → finalize paksa group %d",
                u, Config.MERGE_MAX_GROUP_HOURS, group.get("id"),
            )
            return "finalize", 0

        # 2. Sedang merekam → jangan merge/upload sekarang
        if recording:
            return "defer", DEFER_SECONDS

        # 3. Pemisahan live berbasis JUDUL hanya berlaku untuk IDN.
        #    Reconnect IDN menghasilkan slug BARU dengan judul SAMA
        #    (haii-260913192155 -> haii-260913201248 -> haii-260913201503),
        #    jadi slug tidak boleh dipakai sebagai identitas live.
        #    Showroom tidak punya judul dari IDN, dan identitas sesinya (live_id)
        #    bernilai 0 selama offline — jadi pemeriksaan ini dilewati total.
        #    Pengaman untuk Showroom adalah debounce offline di langkah 5.
        live = await self._safe_fetch_live(u, platform) if (self._fetch_live and platform == "idn") else None
        if live is not None:
            live_key = (live.get("live_key") or "").strip()
            group_key = (group.get("live_key") or "").strip()
            if live_key:
                # Judul berubah HANYA dianggap live baru bila fitur ini dinyalakan.
                # Bukti lapangan: member sering MENGEDIT JUDUL di tengah live
                # (jkt48_carissa 15 Sep 2026: 4 slug & 3 judul berbeda dalam 11 menit
                # = SATU live), jadi default-nya NONAKTIF.
                if SPLIT_ON_TITLE_CHANGE and group_key and group_key != live_key:
                    logger.info(
                        "%s: judul live berubah ('%s' -> '%s') = LIVE BARU, finalize group %d",
                        u, group_key, live_key, group.get("id"),
                    )
                    return "finalize", 0
                # judul sama / fitur split dimatikan = masih live yang sama
                return "defer", DEFER_SECONDS
            # Member TIDAK live saat ini BUKAN berarti live-nya sudah selesai:
            # reconnect membuat record IDN baru, jadi jangan finalize di sini -
            # tunggu MERGE_WINDOW_SECONDS / ambang idle di bawah.

        # 4. WINDOW HABIS = penentu utama "live sudah selesai" (berbasis JEDA WAKTU,
        #    bukan slug/judul). Ini pengaman agar video PASTI terupload: sebelum
        #    perbaikan ini, kondisi "idle > window" hanya menghasilkan defer 5 detik
        #    berulang sehingga upload tidak pernah terjadi.
        #    Berlaku sama untuk IDN dan Showroom (keputusan D4).
        if idle >= MERGE_WINDOW:
            logger.info(
                "%s: window %ds habis sejak segmen terakhir (idle %.0f menit) "
                "-> finalize group %d",
                u, MERGE_WINDOW, idle / 60, group.get("id"),
            )
            return "finalize", 0

        # 5. Konfirmasi via probe platform.
        #    IDN     : cek URL HLS tetap.
        #    Showroom: cek API room, dan "offline" hanya sah setelah beberapa
        #              pembacaan offline BERTURUT-TURUT (debounce). Selama belum
        #              terkonfirmasi hasilnya None ("tidak diketahui"), sehingga
        #              satu hiccup API tidak pernah memecah live.
        active = await self._safe_probe(u, platform) if self._probe_active else None
        if active is True:
            return "defer", DEFER_SECONDS

        # 6. Percepatan berbasis idle (OPSIONAL, default nonaktif).
        #    Karena `active` hanya bernilai False setelah offline TERKONFIRMASI,
        #    langkah ini tidak bisa terpicu oleh gangguan API sesaat.
        if IDLE_FINALIZE_SECONDS > 0 and idle >= IDLE_FINALIZE_SECONDS:
            logger.info(
                "%s: stream offline & idle %.0f menit → finalize group %d lebih awal",
                u, idle / 60, group.get("id"),
            )
            return "finalize", 0

        # 7. Tunggu sisa window
        return "defer", max(5.0, MERGE_WINDOW - idle)

    async def _safe_probe(self, member_username: str, platform: str = "idn") -> Optional[bool]:
        """
        Probe dengan proteksi error — kegagalan dianggap 'tidak diketahui'.

        Untuk Showroom, `probe_active` memakai debounce sehingga nilai False
        berarti offline sudah terkonfirmasi (lihat bot/showroom_monitor.py).
        """
        try:
            return await self._probe_active(member_username, platform)  # type: ignore[misc]
        except Exception as exc:
            logger.debug("Probe %s untuk %s gagal (diabaikan): %s", platform, member_username, exc)
            return None

    async def _safe_fetch_live(self, member_username: str, platform: str = "idn") -> Optional[dict]:
        """IDN lookup dengan proteksi error — gagal = None (bot tetap jalan, HLS-only)."""
        try:
            return await self._fetch_live(member_username, platform)  # type: ignore[misc]
        except Exception as exc:
            logger.debug("IDN lookup untuk %s gagal (diabaikan): %s", member_username, exc)
            return None

    async def _merge_and_upload(self, group_id: int, member_username: str) -> None:
        """Concatenate segments if needed and dispatch to upload callback."""
        segments = get_merge_segments(group_id)
        if not segments:
            logger.warning("Merge group %d has no segments", group_id)
            return

        # Buang segmen yang filenya hilang / 0 byte supaya concat tidak rusak.
        # Segmen seperti itu ditandai failed agar tidak diadopsi ulang selamanya.
        valid_segments: list[dict] = []
        for seg in segments:
            fp = seg.get("file_path")
            if not fp:
                continue
            path = Path(fp)
            try:
                size = path.stat().st_size if path.exists() else 0
            except OSError:
                size = 0
            if size <= 0:
                logger.warning("%s: segmen %s dilewati (file hilang/kosong): %s",
                               member_username, seg.get("live_id"), fp)
                mark_session_failed(seg["live_id"], "Segment file missing or empty at merge time")
                continue
            valid_segments.append(seg)

        segments = valid_segments
        file_paths = [s["file_path"] for s in segments]
        if not file_paths:
            logger.warning("Merge group %d has no valid file paths", group_id)
            fail_merge_group(group_id)
            return

        group = get_merge_group(group_id)
        meta_member_name = group["member_name"] if group else member_username
        meta_started_at = group["started_at"] if group else timeutil.utc_now_iso()
        meta_thumbnail_url = group.get("thumbnail_url", "") if group else ""
        meta_live_title = group.get("live_title", "") if group else ""

        first = segments[0]

        # Only one segment: no concat needed
        if len(file_paths) == 1:
            merged_path = file_paths[0]
            merged_live_id = first["live_id"]
            logger.info("%s: single segment, no concat needed", member_username)
        else:
            logger.info("%s: concatenating %d segments for group %d...",
                        member_username, len(file_paths), group_id)
            merged_path = await self._concat_files(file_paths, member_username, group_id)
            if not merged_path:
                logger.error("%s: concat failed, dispatching segments individually", member_username)
                for seg in segments:
                    if self._on_upload_ready:
                        await self._on_upload_ready(
                            live_id=seg["live_id"],
                            member_username=member_username,
                            member_name=meta_member_name,
                            started_at=seg.get("started_at", meta_started_at),
                            file_path=seg["file_path"],
                            thumbnail_url=meta_thumbnail_url,
                            live_title=meta_live_title,
                        )
                fail_merge_group(group_id)
                return

            merged_live_id = f"merged_{group_id}"

        close_merge_group(group_id, merged_path, merged_live_id)

        if self._on_upload_ready:
            await self._on_upload_ready(
                live_id=merged_live_id,
                member_username=member_username,
                member_name=meta_member_name,
                started_at=meta_started_at,
                file_path=merged_path,
                thumbnail_url=meta_thumbnail_url,
                live_title=meta_live_title,
            )

        # Cleanup individual segment files after successful merged output
        if Config.AUTO_DELETE_AFTER_UPLOAD and len(file_paths) > 1:
            for fp in file_paths:
                if fp != merged_path:
                    delete_file(fp)

    async def _concat_files(
        self,
        file_paths: list[str],
        member_username: str,
        group_id: int,
    ) -> Optional[str]:
        """
        Use ffmpeg concat demuxer to merge video files losslessly.

        Dua percobaan: (1) `-c copy` murni (tercepat), (2) remux dengan
        `-fflags +genpts -avoid_negative_ts make_zero` yang sering menyelamatkan
        gabungan segmen dengan timestamp tidak kontinu (efek lag/reconnect) —
        sehingga tidak perlu jatuh ke mode "kirim segmen terpisah".
        """
        download_dir = Path(Config.DOWNLOAD_DIR)
        download_dir.mkdir(parents=True, exist_ok=True)
        list_path = download_dir / f"merge_{group_id}.txt"
        merged_path = download_dir / f"{member_username}_merged_{group_id}.mp4"

        variants = [
            ["-c", "copy"],
            ["-fflags", "+genpts", "-avoid_negative_ts", "make_zero", "-c", "copy"],
        ]

        try:
            list_path.write_text(
                "\n".join(f"file '{Path(p).resolve()}'" for p in file_paths),
                encoding="utf-8",
            )

            for idx, extra in enumerate(variants, start=1):
                merged_path.unlink(missing_ok=True)
                cmd = [
                    "ffmpeg",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(list_path),
                    *extra,
                    "-y",
                    str(merged_path),
                ]

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await proc.communicate()

                if proc.returncode == 0 and merged_path.exists() and merged_path.stat().st_size > 0:
                    size_mb = merged_path.stat().st_size / (1024 * 1024)
                    logger.info("%s: merge complete (varian %d) → %s (%.1f MB)",
                                member_username, idx, merged_path.name, size_mb)
                    return str(merged_path)

                logger.error("FFmpeg concat gagal (varian %d, code %d): %s",
                             idx, proc.returncode, stdout.decode(errors="replace")[:400])

            return None

        except Exception as exc:
            logger.exception("Merge concat error: %s", exc)
            return None
        finally:
            list_path.unlink(missing_ok=True)

    async def shutdown(self) -> None:
        """Cancel all pending merge timers."""
        for u, task in list(self._timers.items()):
            task.cancel()
        self._timers.clear()
