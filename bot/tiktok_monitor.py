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
from bot.tiktok_media import MediaError, cleanup_media, media_from_disk, prepare_post_media
from bot.youtube_uploader import YouTubeChannelPool

logger = logging.getLogger(__name__)


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
        base_method = getattr(BaseProvider, method_name, None)
        for provider in self._providers:
            if not provider.is_healthy(capability):
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
                continue
            try:
                items = await bound(account, **kwargs)
            except ProviderBlocked as exc:
                provider.mark_unhealthy(str(exc), capability=capability)
                last_error = exc
                continue
            except ProviderError as exc:
                # HTTP 503/500 dari embed = overload sementara → jeda singkat,
                # BUKAN tandai penyedia tidak sehat 15 menit (retry sudah ada di
                # dalam penyedia; kegagalan di sini berarti upstream benar-benar
                # sedang bermasalah).
                logger.warning("Penyedia '%s' gagal (%s): %s", provider.name, capability, exc)
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001 - jangan matikan loop utama
                logger.exception("Penyedia '%s' error tak terduga (%s): %s",
                                 provider.name, capability, exc)
                last_error = exc
                continue
            if items:
                return items, provider
            # Berhasil tapi kosong (mis. akun tidak punya story) → itu jawaban sah.
            return [], provider
        if last_error is not None:
            logger.warning(
                "Semua penyedia gagal mengambil %s untuk %s (%s).",
                capability, account.get("unique_id"), last_error,
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
            if not item.created_at or (item.kind == "photo" and not item.images)
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
                except ProviderError as exc:
                    logger.debug("Detail %s via %s gagal: %s", item.id, provider.name, exc)
                    continue
                except ProviderBlocked as exc:
                    provider.mark_unhealthy(str(exc))
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
            if item.created_at and (item.kind != "photo" or item.images):
                logger.debug("Detail %s/%s lengkap.", item.unique_id, item.id)

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

        Urutan wajib: Telegram dulu (channel arsip = sumber unduhan publik),
        baru YouTube. Bila Telegram gagal, media TIDAK dihapus dan status menjadi
        `pending_upload` supaya di-retry tanpa kehilangan hasil unduhan.
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
                database.update_tiktok_post(
                    post_id, status="failed", error_message=str(exc)[:400]
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error tak terduga saat menyiapkan media %s: %s", post_id, exc)
                database.update_tiktok_post(
                    post_id, status="pending_upload", error_message=str(exc)[:400]
                )
                return

        media_path = str(media.video_path or (media.images[0] if media.images else ""))
        database.update_tiktok_post(
            post_id,
            status="uploading_telegram",
            media_path=media_path,
            media_size_bytes=int(media.size_bytes),
            local_images_json=json.dumps([str(p) for p in media.images]),
            duration_seconds=int(media.duration_seconds),
        )

        try:
            message_ids = await self.tg.send_tiktok_archive(media, post=post, account=account)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Kirim arsip TikTok %s ke Telegram gagal: %s", post_id, exc)
            message_ids = []

        if not message_ids:
            logger.warning("Arsip TikTok %s belum masuk Telegram; diantrikan.", post_id)
            database.update_tiktok_post(
                post_id, status="pending_upload", error_message="Kirim ke Telegram gagal"
            )
            return

        database.update_tiktok_post(
            post_id, telegram_message_ids=",".join(str(m) for m in message_ids)
        )
        logger.info("Arsip TikTok %s tersimpan di Telegram (%d pesan).", post_id, len(message_ids))

        youtube_id = await self._upload_to_youtube(item, post, account, media)
        if youtube_id:
            database.update_tiktok_post(post_id, youtube_video_id=youtube_id)

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

        if Config.AUTO_DELETE_AFTER_UPLOAD:
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
            video_id, channel_label = pool.upload_video(media.video_path, title, description)
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
                thumb_path = build_collage(media.video_path)
                if thumb_path is not None:
                    pool.set_thumbnail(video_id, thumb_path, channel_label=channel_label)
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
        ulang; Telegram TIDAK dikirim ulang. Berhenti lebih awal begitu satu
        upload gagal (hampir pasti kuota habis) supaya tidak membakar unduhan
        untuk sisa antrian — dilanjutkan pada siklus berikutnya.

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
            try:
                media = media_from_disk(row) or await prepare_post_media(
                    item_from_db_row(row), build_slideshow_video=True
                )
            except MediaError as exc:
                # Postingan dihapus / story kedaluwarsa → memang tidak bisa
                # dikejar; lewati tanpa menghentikan antrian.
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
