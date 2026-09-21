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

Satu akun per siklus & jeda antar request (`TIKTOK_REQUEST_INTERVAL_SECONDS`)
dipilih supaya bot tidak menabrak batas gratis tikwm (±1 request/detik) dan tidak
dicap scraping oleh TikTok.

Jalankan mandiri (untuk uji di VPS tanpa menunggu loop utama)::

    python3 -m bot.tiktok_monitor --once            # satu siklus untuk 1 akun
    python3 -m bot.tiktok_monitor --account lulu_jkt48
    python3 -m bot.tiktok_monitor --dry-run         # hanya deteksi, tanpa unduh
"""
import argparse
import asyncio
import json
import logging
import signal
from typing import Optional

from bot import database
from bot.config import Config
from bot.telegram_sender import TelegramSender, build_tiktok_notification
from bot.thumbnail_collage import build_collage
from bot.tiktok_client import (
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
        Periksa satu akun (round-robin) dan proses postingan barunya.

        Returns:
            Jumlah postingan baru yang diproses pada siklus ini.
        """
        accounts = database.get_tiktok_accounts()
        if only_account:
            accounts = [a for a in accounts if a["unique_id"] == only_account]
        if not accounts:
            logger.debug("Tidak ada akun TikTok aktif untuk diperiksa.")
            return 0

        account = accounts[self._cursor % len(accounts)]
        if not only_account:
            self._cursor = (self._cursor + 1) % len(accounts)

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
        Ambil postingan (+ story) akun dari penyedia sehat pertama.

        Penyedia yang gagal (blokir/timeout) ditandai tidak sehat lalu penyedia
        berikutnya dicoba; bila semuanya gagal, daftar kosong dikembalikan supaya
        pemanggil tidak perlu tahu detailnya.
        """
        last_error: Optional[Exception] = None
        for provider in self._providers:
            if not provider.is_healthy():
                continue
            try:
                posts = await provider.fetch_user_posts(
                    account, max(1, Config.TIKTOK_MAX_POSTS_PER_CHECK)
                )
                stories = (
                    await provider.fetch_user_stories(account)
                    if Config.TIKTOK_STORIES_ENABLED else []
                )
                if posts or stories:
                    await self._remember_sec_uid(account, posts or stories, provider)
                    return [*posts, *stories]
                return []
            except ProviderBlocked as exc:
                provider.mark_unhealthy(str(exc))
                last_error = exc
            except ProviderError as exc:
                logger.warning("Penyedia '%s' gagal: %s", provider.name, exc)
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - jangan matikan loop utama
                logger.exception("Penyedia '%s' error tak terduga: %s", provider.name, exc)
                last_error = exc
        if last_error is not None:
            logger.warning(
                "Semua penyedia TikTok gagal untuk %s (%s).",
                account["unique_id"], last_error,
            )
        return []

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
