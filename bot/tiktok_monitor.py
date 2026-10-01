"""
tiktok_monitor.py - Pemantau arsip TikTok JKT48 (postingan, foto, story).

Alur satu siklus (`run_once`):
    1. Pilih SATU akun berikutnya (round-robin) dari `tiktok_accounts`.
    2. Ambil postingan terbaru + story aktif lewat penyedia yang sehat
       (tikwm → yt-dlp, lihat `bot/tiktok_client.py`).
    3. Postingan/story yang belum ada di tabel `tiktok_posts` disimpan
       (`status='detected'`), lalu diproses SATU PER SATU:
         unduh media → kirim ke channel arsip Telegram (foto dipecah per 10
         foto/album) → (opsional) unggah ke YouTube (foto → slide show).
    4. Status akhir `done`, atau `pending_upload` bila salah satu upload gagal
       (media tetap di disk; `retry_pending()` mencoba lagi di siklus berikut).

Beberapa akun per siklus (`TIKTOK_ACCOUNTS_PER_CHECK`, round-robin) & jeda
antar request (`TIKTOK_REQUEST_INTERVAL_SECONDS`) dipilih supaya bot tidak
menabrak batas gratis tikwm (±1 request/detik) dan tidak dicap scraping oleh
TikTok, tetapi rotasi tetap cukup rapat untuk menangkap story (kedaluwarsa
±24 jam).

Jalankan mandiri (untuk uji di VPS tanpa menunggu loop utama)::

    python3 -m bot.tiktok_monitor --once            # satu siklus untuk 1 akun
    python3 -m bot.tiktok_monitor --account lulu_jkt48
    python3 -m bot.tiktok_monitor --dry-run         # hanya deteksi, tanpa unduh

Skrip ini selalu berjalan SATU siklus lalu keluar (`--once` hanya penegas);
loop berulang ditangani `bot/main.py` selama `TIKTOK_ENABLED=true`.
"""
import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot import database
from bot.config import Config
from bot.telegram_sender import TelegramSender, build_tiktok_notification
from bot.thumbnail_collage import build_collage
from bot.tiktok_client import (
    BaseProvider,
    ProviderBlocked,
    ProviderError,
    TikTokItem,
    build_providers,
)
from bot.tiktok_media import (
    MediaError,
    MediaPermanentError,
    cleanup_media,
    is_permanent_media_error,
    media_from_disk,
    prepare_post_media,
    save_cover_image,
)
from bot.youtube_uploader import YouTubeChannelPool

logger = logging.getLogger(__name__)

# Jeda minimum sebelum baris 'failed' (percobaan belum habis) dicoba ulang:
# blokir IP TikTok sesaat butuh waktu pulih, jadi jangan langsung dipukul lagi
# pada siklus yang sama.
RETRY_FAILED_MIN_AGE_SECONDS = 900


def _story_kedaluwarsa(item: TikTokItem) -> bool:
    """True bila item adalah story yang lebih tua dari TIKTOK_STORY_MAX_AGE_HOURS.

    Story kedaluwarsa tidak akan pernah bisa diunduh ulang dari TikTok, jadi
    kegagalannya dihitung PERMANEN seketika (tidak menunggu batas percobaan).
    """
    if not item.is_story:
        return False
    try:
        stamp = datetime.fromisoformat(str(item.created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age > max(0, Config.TIKTOK_STORY_MAX_AGE_HOURS) * 3600


def _catat_kegagalan_unduh(item: TikTokItem, exc: BaseException) -> bool:
    """
    Naikkan penghitung percobaan unduh dan tentukan apakah kegagalan ini
    PERMANEN (penyebab memang permanen / story kedaluwarsa / percobaan habis).
    Baris tetap 'failed' di DB: yang non-permanen diambil kembali lewat
    `get_tiktok_retryable_failed` — memakai satu status membuat backlog tidak
    mengulang video IP-blocked selamanya.

    Returns:
        True bila gagal permanen (jangan di-retry lagi).
    """
    attempts = database.increment_tiktok_download_attempt(item.id)
    max_attempts = max(1, Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS)
    permanen = (
        is_permanent_media_error(exc)
        or _story_kedaluwarsa(item)
        or attempts >= max_attempts
    )
    update: dict = {"status": "failed", "error_message": str(exc)[:400]}
    if permanen and attempts < max_attempts:
        # Penyebab permanen (IP diblokir / story kedaluwarsa) langsung TERMINAL:
        # samakan penghitung dengan batas supaya get_tiktok_retryable_failed
        # tidak lagi mengambilnya.
        attempts = max_attempts
        update["download_attempts"] = attempts
    database.update_tiktok_post(item.id, **update)
    if permanen:
        logger.warning(
            "Media TikTok %s gagal PERMANEN (%s, percobaan %d/%d).",
            item.id, exc, attempts, Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS,
        )
    else:
        logger.warning(
            "Media TikTok %s gagal sementara (%s, percobaan %d/%d) — di-retry nanti.",
            item.id, exc, attempts, Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS,
        )
    return permanen


def item_from_db_row(row: dict) -> TikTokItem:
    """Bangun ulang TikTokItem dari baris `tiktok_posts` (untuk retry upload)."""
    try:
        images = json.loads(row.get("images_json") or "[]")
    except ValueError:
        images = []
    return TikTokItem(
        id=str(row.get("id") or ""),
        unique_id=str(row.get("unique_id") or ""),
        kind=str(row.get("kind") or "video"),
        title=str(row.get("title") or ""),
        created_at=str(row.get("created_at") or ""),
        duration_seconds=int(row.get("duration_seconds") or 0),
        images=[u for u in images if u],
        source_url=str(row.get("source_url") or ""),
        cover_url=str(row.get("cover_url") or ""),
        is_story=bool(row.get("is_story")),
    )


class TikTokMonitor:
    """Pemantau & pengarsip TikTok (dipakai loop utama bot atau berdiri sendiri)."""

    def __init__(
        self,
        telegram: Optional[TelegramSender] = None,
        youtube_pool: Optional[YouTubeChannelPool] = None,
        dry_run: bool = False,
    ) -> None:
        self.tg = telegram or TelegramSender()
        self._yt = youtube_pool
        self._providers = build_providers()
        self._cursor = 0
        self._dry_run = dry_run
        # (capability, unique_id) -> pesan error terakhir yang sudah dilaporkan.
        # Dedup: "Semua penyedia gagal ..." hanya di-log saat PESANNYA BERUBAH
        # atau pertama kali; pulih → dihapus (kepulihan tercatat oleh mark
        # unhealthy/provider log). Tanpa ini satu blokir IP yang sama di-log
        # untuk 51 akun setiap siklus.
        self._cap_fail_reported: dict[tuple[str, str], str] = {}
        # (nama penyedia, kapabilitas) -> jumlah ProviderError berturut-turut.
        # 5xx/429 tidak langsung menutup health gate (satu kegagalan mungkin
        # kebetulan), tetapi setelah beberapa kali berturut-turut penyedia
        # ditutup 15 menit supaya 51 akun tidak menembak endpoint yang sama.
        self._provider_error_streak: dict[tuple[str, str], int] = {}

    # ── utilitas ────────────────────────────────────────────────────────────
    def _yt_pool(self) -> Optional[YouTubeChannelPool]:
        """Buat pool YouTube hanya saat dibutuhkan (token OAuth bisa belum ada)."""
        if not Config.TIKTOK_YT_UPLOAD_ENABLED:
            return None
        if self._yt is None:
            try:
                self._yt = YouTubeChannelPool()
            except Exception as exc:  # noqa: BLE001 - arsip Telegram tetap jalan
                logger.warning("YouTube dilewati untuk arsip TikTok: %s", exc)
                return None
        return self._yt

    async def close(self) -> None:
        for provider in self._providers:
            try:
                await provider.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Gagal menutup penyedia %s: %s", provider.name, exc)

    # ── siklus pemantauan ───────────────────────────────────────────────────
    async def run_once(self, only_account: str = "") -> int:
        """
        Periksa TIKTOK_ACCOUNTS_PER_CHECK akun (round-robin) dan proses
        postingan barunya.

        Returns:
            Jumlah postingan baru yang diproses pada siklus ini.
        """
        accounts = database.get_tiktok_accounts()
        if only_account:
            accounts = [a for a in accounts if a["unique_id"] == only_account]
        if not accounts:
            logger.debug("Tidak ada akun TikTok aktif untuk diperiksa.")
            return 0

        if only_account:
            return await self._check_account(accounts[0])

        total = 0
        batch = min(max(1, Config.TIKTOK_ACCOUNTS_PER_CHECK), len(accounts))
        for index in range(batch):
            account = accounts[self._cursor % len(accounts)]
            self._cursor = (self._cursor + 1) % len(accounts)
            total += await self._check_account(account)
            # Jeda sopan antar akun dalam satu siklus (jeda per-request penyedia
            # sudah diatur RateLimiter; ini jarak antar akun).
            if index < batch - 1:
                await asyncio.sleep(max(0.0, Config.TIKTOK_REQUEST_INTERVAL_SECONDS))
        return total

    async def _check_account(self, account: dict) -> int:
        """Periksa SATU akun dan proses postingan barunya (dipakai run_once)."""
        try:
            items = await self._fetch_items(account)
        except ProviderError as exc:
            logger.warning("Pemeriksaan akun TikTok %s dilewati: %s", account["unique_id"], exc)
            return 0

        new_items = [i for i in items if i.id and not database.tiktok_post_exists(i.id)]
        database.mark_tiktok_account_checked(
            account["unique_id"],
            last_post_at=max((i.created_at for i in items if i.created_at), default=""),
        )
        if not new_items:
            logger.debug("Akun %s: tidak ada postingan baru.", account["unique_id"])
            return 0

        # Terbaru dulu, lalu dibatasi agar akun yang baru dipantau tidak
        # membanjiri arsip (sisanya terdeteksi pada siklus berikutnya).
        new_items.sort(key=lambda i: i.created_at or "", reverse=True)
        limit = max(1, Config.TIKTOK_MAX_POSTS_PER_CHECK)
        process_items = new_items[:limit]

        logger.info(
            "Akun %s: %d postingan baru (diproses %d, sisa %d menyusul).",
            account["unique_id"], len(new_items), len(process_items), len(new_items) - len(process_items),
        )
        await self._enrich_new_items(new_items, account)
        for item in process_items:
            database.insert_tiktok_post(item.to_db_row())
            if self._dry_run:
                logger.info(
                    "[dry-run] %s/%s (%s, %d foto) terdeteksi.",
                    item.unique_id, item.id, item.kind, item.image_count,
                )
                continue
            await self._process_item(item, account)
        return len(process_items)

    async def _fetch_items(self, account: dict) -> list[TikTokItem]:
        """
        Ambil postingan + story akun.

        Postingan dan story dicari dari penyedia yang sehat SECARA TERPISAH,
        karena Cloudflare memblokir per-path: 21 Sep 2026 tikwm menjawab 403 untuk
        `/user/posts` tetapi 200 untuk `/user/story`, sedangkan halaman embed
        TikTok hanya punya postingan. Dengan pemisahan ini listing tetap jalan
        (embed) DAN story tetap terambil (tikwm) pada siklus yang sama.
        """
        collected: list[TikTokItem] = []
        seen: set[str] = set()

        posts, provider = await self._collect(
            account, "fetch_user_posts", "posts", limit=max(1, Config.TIKTOK_MAX_POSTS_PER_CHECK)
        )
        if posts and provider is not None:
            await self._remember_sec_uid(account, posts, provider)
        for item in posts:
            if item.id not in seen:
                seen.add(item.id)
                collected.append(item)

        if Config.TIKTOK_STORIES_ENABLED:
            stories, _ = await self._collect(account, "fetch_user_stories", "stories")
            for item in stories:
                if item.id not in seen:
                    seen.add(item.id)
                    collected.append(item)

        return collected

    async def _collect(
        self,
        account: dict,
        method_name: str,
        capability: str,
        **kwargs,
    ) -> tuple[list[TikTokItem], Optional[BaseProvider]]:
        """
        Coba tiap penyedia sehat sampai ada yang mengembalikan item.

        Penyedia yang tidak mengimplementasikan kapabilitas (masih memakai stub
        `BaseProvider` → mis. halaman embed tanpa story) langsung dilewati, dan
        kegagalan hanya menandai kapabilitas itu sebagai tidak sehat.

        Returns:
            (daftar item, penyedia yang berhasil) — ([], None) bila semua gagal.
        """
        last_error: Optional[Exception] = None
        fail_key = (capability, account.get("unique_id") or "")
        base_method = getattr(BaseProvider, method_name, None)
        # Ringkasan kondisi SETIAP penyedia, bukan hanya yang terakhir. Log
        # lama hanya menyebut error penyedia terakhir, sehingga operator
        # menyimpulkan penyebabnya adalah penyedia itu — padahal biasanya
        # rangkaian: tikwm 403 (dilewati) → embed 503 → ytdlp JSON error.
        # Diagnostik 1 Okt 2026 berjalan salah karena log tidak menunjukkan
        # dua penyedia sebelumnya sudah tumbang.
        outcomes: list[tuple[str, str]] = []
        for provider in self._providers:
            if not provider.is_healthy(capability):
                outcomes.append((provider.name, "dilewati (sedang tidak sehat)"))
                continue
            bound = getattr(provider, method_name, None)
            if bound is None:
                continue
            # Bandingkan FUNGSI aslinya (`__func__`): metode terikat tidak pernah
            # `is` fungsi kelas, jadi perbandingan langsung selalu False dan
            # penyedia tanpa kapabilitas akan ikut dipanggil — mengembalikan []
            # yang menghentikan pencarian sebelum penyedia berikutnya dicoba.
            func = getattr(bound, "__func__", bound)
            if base_method is not None and func is base_method:
                provider.mark_capability_missing(capability)
                outcomes.append((provider.name, "tidak mendukung kapabilitas ini"))
                continue
            streak_key = (provider.name, capability)
            try:
                items = await bound(account, **kwargs)
            except ProviderBlocked as exc:
                provider.mark_unhealthy(str(exc), capability=capability)
                self._provider_error_streak.pop(streak_key, None)
                outcomes.append((provider.name, f"diblokir: {exc}"))
                last_error = exc
                continue
            except ProviderError as exc:
                # HTTP 503/500/429 = upstream sedang bermasalah. Satu kegagalan
                # belum berarti apa-apa, tapi siklus yang memproses 51 akun akan
                # menembak penyedia sama berulang kali (51 permintaan sia-sia,
                # dan tiap 429 memperpanjang jendela rate-limit untuk IP itu
                # juga). Setelah beberapa kali berturut-turut, health gate ikut
                # menutup supaya sisa akun pada siklus ini langsung melompatinya.
                count = self._provider_error_streak.get(streak_key, 0) + 1
                self._provider_error_streak[streak_key] = count
                threshold = max(
                    1, int(Config.TIKTOK_PROVIDER_ERROR_STREAK_BEFORE_UNHEALTHY)
                )
                if count >= threshold:
                    provider.mark_unhealthy(
                        f"{exc} (gagal {count}x berturut-turut)",
                        capability=capability,
                    )
                    self._provider_error_streak.pop(streak_key, None)
                logger.warning(
                    "Penyedia '%s' gagal (%s): %s [ke-%d]",
                    provider.name, capability, exc, count,
                )
                outcomes.append((provider.name, f"gagal: {exc}"))
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001 - jangan matikan loop utama
                logger.exception("Penyedia '%s' error tak terduga (%s): %s",
                                 provider.name, capability, exc)
                outcomes.append((provider.name, f"error tak terduga: {exc}"))
                last_error = exc
                continue
            self._provider_error_streak.pop(streak_key, None)
            if items:
                self._cap_fail_reported.pop(fail_key, None)
                return items, provider
            # Berhasil tapi kosong (mis. akun tidak punya story) → itu jawaban sah.
            self._cap_fail_reported.pop(fail_key, None)
            return [], provider
        if outcomes:
            # Dedup per (kapabilitas, daftar kondisi) — bukan per akun — supaya
            # outage TikTok-wide untuk 51 akun menghasilkan blok ringkas, bukan
            # 51 salinan pesan yang identik.
            summary = " | ".join(f"{name}: {state}" for name, state in outcomes)
            if self._cap_fail_reported.get(capability) != summary:
                self._cap_fail_reported[capability] = summary
                logger.warning(
                    "Semua penyedia gagal mengambil %s untuk %s. Kondisi "
                    "penyedia: %s",
                    capability, account.get("unique_id"), summary,
                )
        return [], None


    async def _enrich_new_items(self, items: list[TikTokItem], account: dict) -> None:
        """
        Lengkapi detail postingan BARU (maksimum satu request per postingan).

        Halaman listing embed tidak memuat `createTime` dan jumlah foto, jadi
        untuk postingan baru saja detailnya diambil dari halaman embed per-post
        (`/embed/v2/<id>`) — atau dari tikwm bila penyedia itu yang dipakai.
        Postingan lama tidak disentuh supaya tidak ada request berulang.

        Fungsi ini hanya memutakhirkan objek `TikTokItem` di memori; kegagalan
        apa pun diabaikan (media tetap bisa diunduh, tanggal saja yang kosong).
        """
        targets = [
            item for item in items
            if not item.created_at
            or (item.kind == "photo" and not item.images)
            # Posting dari `scrape` HANYA berisi id + waktu (createTime
            # disimpulkan dari snowflake ID), jadi `created_at` selalu terisi
            # padahal belum ada satu pun sumber media. Tanpa syarat ini
            # posting seperti itu dilewati enrich dan langsung gagal saat
            # unduhan karena `video_url`/foto kosong — persis gejala "data
            # muncul di website, kontennya tidak ada" (1 Okt 2026).
            or not (item.video_url or item.images)
        ]
        for item in targets:
            for provider in self._providers:
                if not provider.is_healthy():
                    continue
                fetch_detail = getattr(provider, "fetch_item_detail", None)
                if fetch_detail is None:
                    continue
                try:
                    detail = await fetch_detail(item.page_url, item.unique_id)
                except ProviderBlocked as exc:
                    # Urutan WAJIB sebelum ProviderError (ProviderBlocked adalah
                    # subclassnya — kalau tidak, cabang ini tidak pernah jalan).
                    # Hanya kapabilitas 'posts' yang ditandai: satu blokir
                    # detail-fetch tidak berarti story/listing penyedia ikut
                    # mati (Cloudflare memblokir per-path, bukan per-domain).
                    provider.mark_unhealthy(str(exc), capability="posts")
                    continue
                except ProviderError as exc:
                    logger.debug("Detail %s via %s gagal: %s", item.id, provider.name, exc)
                    continue
                if detail is None:
                    continue
                # Hanya isi bagian yang masih kosong; listing tetap sumber utama.
                item.created_at = item.created_at or detail.created_at
                if not item.images and detail.images:
                    item.images = detail.images
                    item.kind = "photo"
                item.duration_seconds = item.duration_seconds or detail.duration_seconds
                item.video_url = item.video_url or detail.video_url
                item.cover_url = item.cover_url or detail.cover_url
                break
            if item.created_at and item.video_url and item.images:
                logger.debug("Detail %s/%s lengkap.", item.unique_id, item.id)
            elif item.created_at and (item.video_url or item.images):
                logger.debug(
                    "Detail %s/%s parsial (video=%s, foto=%d).",
                    item.unique_id, item.id, bool(item.video_url), len(item.images),
                )

    async def _remember_sec_uid(self, account: dict, items: list[TikTokItem], provider) -> None:
        """
        Simpan secUid akun begitu diketahui.

        secUid membuat listing profil di yt-dlp stabil (`tiktokuser:<secUid>`),
        jadi nilainya diambil sekali lalu dipakai selamanya.
        """
        if account.get("sec_uid") or not items:
            return
        unique_id = account["unique_id"]
        try:
            info = await provider.fetch_user_info(unique_id)
        except ProviderError:
            return
        sec_uid = (info or {}).get("sec_uid") or ""
        nickname = (info or {}).get("nickname") or ""
        if sec_uid or nickname:
            database.set_tiktok_account_meta(
                unique_id, sec_uid=sec_uid or None, display_name=nickname or None
            )
            logger.info("Metadata akun TikTok %s diperbarui (secUid/nama).", unique_id)

    # ── pemrosesan satu postingan ───────────────────────────────────────────
    async def _process_item(self, item: TikTokItem, account: dict) -> None:
        """
        Unduh + unggah satu postingan TikTok.

        Urutan: Telegram dulu bila TIKTOK_ARCHIVE_UPLOAD_ENABLED (channel arsip
        = sumber unduhan publik), lalu YouTube. Bila Telegram diaktifkan dan
        gagal, media TIDAK dihapus dan status menjadi `pending_upload` supaya
        di-retry tanpa kehilangan hasil unduhan. Bila Telegram dimatikan, posting
        langsung ke YouTube tanpa terhalang tahap Telegram.
        """
        post_id = item.id
        post = database.get_tiktok_post(post_id) or item.to_db_row()
        logger.info(
            "Memproses TikTok %s/%s (%s%s)...",
            item.unique_id, post_id, item.kind, " story" if item.is_story else "",
        )
        database.update_tiktok_post(post_id, status="downloading", error_message="")

        # Retry: pakai media yang masih ada di disk agar tidak mengunduh ulang.
        media = media_from_disk(post)
        if media is not None:
            logger.info("Media TikTok %s dipakai ulang dari disk.", post_id)
        else:
            try:
                media = await prepare_post_media(
                    item, build_slideshow_video=Config.TIKTOK_YT_UPLOAD_ENABLED
                )
            except MediaError as exc:
                logger.error("Media TikTok %s gagal disiapkan: %s", post_id, exc)
                _catat_kegagalan_unduh(item, exc)
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error tak terduga saat menyiapkan media %s: %s", post_id, exc)
                # Penghitung tetap dinaikkan supaya error tak terduga yang
                # berulang akhirnya juga dead-letter. Status 'failed' (bukan
                # pending_upload — media belum ada di disk) supaya ikut diambil
                # get_tiktok_retryable_failed sampai percobaan habis.
                attempts = database.increment_tiktok_download_attempt(post_id)
                database.update_tiktok_post(
                    post_id, status="failed", error_message=str(exc)[:400]
                )
                if attempts >= max(1, Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS):
                    logger.warning("Media TikTok %s gagal PERMANEN (percobaan habis).", post_id)
                return

        media_path = str(media.video_path or (media.images[0] if media.images else ""))
        # Cover disimpan permanen (bukan di folder kerja) karena website
        # memakai thumbnail YouTube bila ada, atau gambar ini bila tidak —
        # sedangkan cover CDN TikTok bertanda tangan dan cepat kedaluwarsa.
        cover_path = await save_cover_image(item, item.cover_url or post.get("cover_url") or "")
        database.update_tiktok_post(
            post_id,
            status="uploading_telegram",
            media_path=media_path,
            media_size_bytes=int(media.size_bytes),
            local_images_json=json.dumps([str(p) for p in media.images]),
            duration_seconds=int(media.duration_seconds),
            cover_path=str(cover_path) if cover_path else "",
        )

        # Arsip Telegram opsional (Config.TIKTOK_ARCHIVE_UPLOAD_ENABLED, default
        # true). Saat dimatikan, tahap Telegram dilewati begitu saja dan
        # upload langsung ke YouTube supaya file hasil unduhan tetap terpakai.
        message_ids: list[int] = []
        if Config.TIKTOK_ARCHIVE_UPLOAD_ENABLED:
            try:
                message_ids = await self.tg.send_tiktok_archive(media, post=post, account=account)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Kirim arsip TikTok %s ke Telegram gagal: %s", post_id, exc)
                message_ids = []
        else:
            logger.info(
                "Arsip Telegram dimatikan (TIKTOK_ARCHIVE_UPLOAD_ENABLED=false); "
                "postingan %s proceeds ke YouTube saja.",
                post_id,
            )

        if Config.TIKTOK_ARCHIVE_UPLOAD_ENABLED and not message_ids:
            logger.warning("Arsip TikTok %s belum masuk Telegram; diantrikan.", post_id)
            database.update_tiktok_post(
                post_id, status="pending_upload", error_message="Kirim ke Telegram gagal"
            )
            return

        if message_ids:
            database.update_tiktok_post(
                post_id, telegram_message_ids=",".join(str(m) for m in message_ids)
            )
            logger.info(
                "Arsip TikTok %s tersimpan di Telegram (%d pesan).", post_id, len(message_ids)
            )

        youtube_id = await self._upload_to_youtube(item, post, account, media)
        if youtube_id:
            database.update_tiktok_post(post_id, youtube_video_id=youtube_id)
        elif Config.TIKTOK_YT_UPLOAD_ENABLED:
            # Arsip Telegram sudah aman, jadi postingannya SAH dan website tetap
            # menampilkannya (unduhan lewat bot). Hanya videonya belum ada —
            # ini dicatat agar tidak dianggap "selesai sepenuhnya", dan
            # `get_tiktok_youtube_backlog` akan mengejarnya.
            logger.warning(
                "TikTok %s: arsip Telegram aman, tapi video YouTube belum ada "
                "(antrean backlog).", post_id,
            )

        # Notifikasi publik (link YouTube) — hanya bila videonya benar-benar ada.
        if youtube_id:
            try:
                text = build_tiktok_notification(
                    member_name=(
                        account.get("display_name") or account.get("member_username")
                        or item.unique_id
                    ),
                    unique_id=item.unique_id,
                    posted_at=post.get("created_at") or "",
                    video_id=youtube_id,
                    title=post.get("title") or "",
                    kind=post.get("kind") or "video",
                    is_story=bool(post.get("is_story")),
                )
                await self.tg.send_message(text)
            except Exception as exc:  # noqa: BLE001 - notifikasi best-effort
                logger.warning("Notifikasi TikTok gagal dikirim: %s", exc)

        database.update_tiktok_post(post_id, status="done", error_message="")
        logger.info(
            "Arsip TikTok selesai: %s/%s (YouTube: %s)",
            item.unique_id, post_id, youtube_id or "dilewati",
        )

        # Hapus media hanya bila YouTube sukses ATAU fitur YT mati.
        # Bila YT aktif tapi upload gagal (kuota dsb.), berkas HARUS tetap
        # ada di disk: backlog akan memakai file yang SAMA dengan yang sudah
        # dikirim ke Telegram — unduhan ulang bisa mengambil varian berwatermark
        # dan membuat website beda dari arsip Telegram (anomali 22 Sep 2026).
        if Config.AUTO_DELETE_AFTER_UPLOAD and (
            youtube_id or not Config.TIKTOK_YT_UPLOAD_ENABLED
        ):
            cleanup_media(media)



    async def _upload_to_youtube(
        self,
        item: TikTokItem,
        post: dict,
        account: dict,
        media,
    ) -> str:
        """
        Unggah media ke YouTube (postingan foto dikirim sebagai slide show).

        Returns:
            video_id YouTube, atau '' bila dilewati/gagal (arsip Telegram tetap sah).
        """
        pool = self._yt_pool()
        if pool is None or media.video_path is None:
            return ""

        member_name = (
            account.get("display_name") or account.get("member_username") or item.unique_id
        )
        title = pool.build_tiktok_title(
            member_name=member_name,
            posted_at=post.get("created_at") or item.created_at,
            kind=post.get("kind") or item.kind,
            is_story=bool(post.get("is_story")),
        )
        description = pool.build_tiktok_description(
            member_name=member_name,
            unique_id=item.unique_id,
            posted_at=post.get("created_at") or item.created_at,
            kind=post.get("kind") or item.kind,
            is_story=bool(post.get("is_story")),
            image_count=item.image_count,
            source_url=item.page_url,
            caption_text=(post.get("title") or "")[:1500],
        )
        database.update_tiktok_post(item.id, status="uploading_youtube")
        try:
            video_id, channel_label = await asyncio.to_thread(
                pool.upload_video,
                media.video_path,
                title,
                description,
            )
        except Exception as exc:  # noqa: BLE001 - termasuk kuota YouTube habis
            logger.warning("Upload YouTube untuk TikTok %s dilewati: %s", item.id, exc)
            return ""
        if not video_id:
            logger.warning("Upload YouTube TikTok %s tidak mengembalikan video ID.", item.id)
            return ""

        # Thumbnail kolase 3x2 dari video (slide show → potongan foto).
        # Best-effort: gagal memasang thumbnail tidak menggagalkan upload.
        thumb_path = None
        try:
            if Config.THUMBNAIL_COLLAGE_ENABLED:
                thumb_path = await asyncio.to_thread(build_collage, media.video_path)
                if thumb_path is not None:
                    ok_thumb = await asyncio.to_thread(
                        pool.set_thumbnail,
                        video_id,
                        thumb_path,
                        channel_label=channel_label,
                    )
                    if not ok_thumb:
                        logger.warning(
                            "Thumbnail TikTok gagal terpasang ke YouTube %s.",
                            video_id,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Thumbnail TikTok dilewati (%s): %s", item.id, exc)
        finally:
            if thumb_path is not None:
                try:
                    Path(thumb_path).unlink(missing_ok=True)
                except OSError:
                    pass
        return video_id

    # ── retry ───────────────────────────────────────────────────────────────
    async def retry_pending(self, limit: int = 5) -> int:
        """
        Coba ulang postingan yang medianya sudah ada tapi uploadnya belum lengkap.

        Returns:
            Jumlah postingan yang berhasil diselesaikan (`status='done'`).
        """
        pending = database.get_tiktok_pending_posts()
        # Baris 'failed' dengan percobaan di bawah batas ikut di-retry: blokir
        # IP TikTok sering hanya sesaat.
        retryable = database.get_tiktok_retryable_failed(
            limit=max(1, limit),
            max_attempts=max(1, Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS),
            min_age_seconds=RETRY_FAILED_MIN_AGE_SECONDS,
        )
        pending = pending + retryable
        if not pending:
            return 0
        logger.info("Retry arsip TikTok: %d postingan menunggu.", len(pending))
        done = 0
        for row in pending[: max(1, limit)]:
            account = database.get_tiktok_account(row.get("unique_id") or "") or {
                "unique_id": row.get("unique_id") or "",
                "display_name": "",
            }
            await self._process_item(item_from_db_row(row), account)
            refreshed = database.get_tiktok_post(row.get("id") or "")
            if refreshed and refreshed.get("status") == "done":
                done += 1
        return done

    # ── backlog YouTube ─────────────────────────────────────────────────────
    async def retry_youtube_backlog(self, limit: int = 0) -> int:
        """
        Kejar arsip yang sudah aman di Telegram tetapi belum punya video
        YouTube (upload pertama gagal/dilewati, mis. kuota harian habis).

        Media dipakai ulang dari disk bila masih ada, kalau tidak diunduh
        ulang; Telegram TIDAK dikirim ulang. Baris yang percobaan unduhnya sudah
        habis (TIKTOK_MAX_DOWNLOAD_ATTEMPTS) atau errornya permanen (mis. IP
        diblokir TikTok) dilewati permanen supaya backlog tidak mengulang video
        yang memang tidak bisa diunduh selamanya. Berhenti lebih awal hanya
        begitu satu UPLOAD gagal (hampir pasti kuota habis) supaya tidak
        membakar unduhan untuk sisa antrian — dilanjutkan di siklus berikutnya.

        Returns:
            Jumlah arsip yang kini punya video YouTube.
        """
        if not Config.TIKTOK_YT_UPLOAD_ENABLED:
            return 0
        if self._yt_pool() is None:
            return 0
        cap = limit or max(1, Config.TIKTOK_YT_BACKLOG_PER_CHECK)
        rows = database.get_tiktok_youtube_backlog(limit=cap)
        if not rows:
            return 0
        logger.info("Backlog YouTube TikTok: %d arsip dicoba.", len(rows))
        uploaded = 0
        for row in rows:
            post_id = str(row.get("id") or "")
            account = database.get_tiktok_account(row.get("unique_id") or "") or {
                "unique_id": row.get("unique_id") or "",
                "display_name": "",
            }
            item = item_from_db_row(row)
            media = media_from_disk(row)
            if media is None:
                max_attempts = max(1, Config.TIKTOK_MAX_DOWNLOAD_ATTEMPTS)
                if int(row.get("download_attempts") or 0) >= max_attempts:
                    logger.info(
                        "Backlog YouTube %s dilewati permanen: percobaan unduh habis (%d/%d).",
                        post_id, int(row.get("download_attempts") or 0), max_attempts,
                    )
                    continue
                if _story_kedaluwarsa(item):
                    logger.info(
                        "Backlog YouTube %s dilewati permanen: story kedaluwarsa.", post_id
                    )
                    continue
                try:
                    media = await prepare_post_media(item, build_slideshow_video=True)
                except MediaError as exc:
                    permanen = _catat_kegagalan_unduh(item, exc)
                    if permanen:
                        logger.info("Backlog YouTube %s dilewati permanen: %s", post_id, exc)
                    else:
                        logger.info("Backlog YouTube %s dilewati: %s", post_id, exc)
                    continue
                except Exception as exc:  # noqa: BLE001 - penyedia diblokir dsb.
                    logger.warning("Backlog YouTube dihentikan sementara: %s", exc)
                    break
            youtube_id = await self._upload_to_youtube(
                item_from_db_row(row), row, account, media
            )
            if youtube_id:
                database.update_tiktok_post(
                    post_id, youtube_video_id=youtube_id,
                    status="done", error_message="",
                )
                uploaded += 1
                logger.info("Backlog YouTube %s terunggah: %s", post_id, youtube_id)
                if Config.AUTO_DELETE_AFTER_UPLOAD:
                    cleanup_media(media)
            else:
                # Pulihkan status semula lalu berhenti — sisa antrian dicoba
                # lagi pada siklus berikutnya.
                database.update_tiktok_post(
                    post_id, status=str(row.get("status") or "done")
                )
                break
        return uploaded



# ─── Runner mandiri (uji/operasi manual di VPS) ─────────────────────────────
def _setup_logging() -> None:
    try:
        import colorlog  # type: ignore

        handler = colorlog.StreamHandler()
        handler.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s%(asctime)s [%(levelname)s] %(name)s%(reset)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root = logging.getLogger()
        if not root.handlers:
            root.addHandler(handler)
        root.setLevel(logging.INFO)
    except ImportError:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )


async def _run_cli(account: str, dry_run: bool, retry: bool) -> None:
    database.init_db()
    monitor = TikTokMonitor(dry_run=dry_run)
    try:
        found = await monitor.run_once(only_account=account)
        logger.info("Siklus selesai: %d postingan baru diproses.", found)
        if retry and not dry_run:
            done = await monitor.retry_pending()
            logger.info("Retry selesai: %d postingan kini berstatus 'done'.", done)
            yt = await monitor.retry_youtube_backlog()
            logger.info("Backlog YouTube: %d arsip kini punya video YouTube.", yt)
    finally:
        await monitor.close()
        await monitor.tg.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pemantau arsip TikTok JKT48 (satu siklus)."
    )
    parser.add_argument("--account", default="", help="hanya periksa akun ini (unique_id)")
    parser.add_argument("--dry-run", action="store_true", help="hanya deteksi, tanpa unduh/upload")
    parser.add_argument("--no-retry", action="store_true", help="lewati retry postingan tertunda")
    parser.add_argument(
        "--once", action="store_true",
        help="jalankan SATU siklus lalu keluar (memang perilaku default skrip ini)",
    )
    args = parser.parse_args()

    _setup_logging()
    logger.info(
        "TikTok provider=%s | akun=%s | dry-run=%s",
        Config.TIKTOK_PROVIDER, args.account or "(round-robin)", args.dry_run,
    )
    try:
        asyncio.run(_run_cli(args.account, args.dry_run, not args.no_retry))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
