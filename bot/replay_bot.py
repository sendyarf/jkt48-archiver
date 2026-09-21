"""
replay_bot.py - Bot Telegram PUBLIK untuk fitur "Download via Bot Telegram".

Alur pengguna:
    halaman watch (web) -> tombol Download -> modal -> link
    https://t.me/<bot>?start=<youtube_video_id> -> bot mengirim video replay.

Bot ini TIDAK meng-upload ulang file. Video sudah tersimpan di channel arsip
privat (TELEGRAM_ARCHIVE_CHANNEL_ID); bot hanya menyalinnya dengan Bot API
`copyMessage` sehingga hampir tanpa bandwidth dan instan di sisi server.

Implementasi long polling memakai Bot HTTP API via `httpx` (tanpa dependency
baru), mengikuti pola `bot/admin_bot.py`. Listener otomatis dijalankan di dalam
proses `bot.main` (lihat Config.REPLAY_BOT_ENABLED) atau terpisah:
`python3 -m bot.replay_bot`.

Berbeda dengan admin bot, bot ini terbuka untuk SIAPA SAJA (publik), tetapi
hanya merespons deep-link /start dengan payload YouTube video ID yang valid.
"""
import asyncio
import logging
import signal
from typing import Optional

import httpx

from bot import database
from bot.config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 25

WELCOME_TEXT = (
    "👋 <b>JKT48 Replay Bot</b>\n\n"
    "Bot ini mengirim arsip replay live JKT48 — sekarang termasuk arsip "
    "TikTok (video, foto, dan story) member.\n\n"
    "Cara pakai: buka halaman tontonan atau halaman <b>TikTok</b> di situs, "
    "lalu tekan tombol <b>Download via Bot Telegram</b>. Kamu akan diarahkan "
    "ke sini dengan tautan khusus dan medianya otomatis dikirim. Untuk "
    "postingan foto, kamu menerima <b>fotonya</b> (bukan hanya videonya).\n\n"
    "Bot ini tidak menerima perintah manual."
)
NOT_FOUND_TEXT = (
    "😕 Replay tidak ditemukan atau belum tersedia untuk diunduh.\n"
    "Coba lagi dari tombol download di halaman tontonan."
)
TIKTOK_NOT_FOUND_TEXT = (
    "😕 Arsip TikTok ini belum tersedia untuk diunduh.\n"
    "Biasanya karena arsipnya masih diproses (unduh + upload). Coba lagi "
    "sebentar, lalu tekan tombol download di halaman TikTok."
)
TIKTOK_PHOTO_INFO_TEXT = (
    "🖼️ <b>Postingan foto TikTok</b>\n\n"
    "Sebanyak <b>{count} foto</b> akan dikirim dalam <b>{parts} album</b> "
    "(maksimum 10 foto per album). Tunggu sampai semua album selesai masuk ya."
)
NOTIFY_TEXT = (
    "🔔 <b>Siap!</b>\n\n"
    "Replay ini masih dalam masa tunggu dan akan tersedia sebentar lagi. "
    "Begitu terbit, kamu bisa kembali ke halaman tontonannya lalu menekan "
    "tombol <b>Download via Bot Telegram</b> untuk menerima videonya di sini."
)
ERROR_TEXT = "❌ Terjadi kesalahan saat mengambil video. Coba lagi nanti."


def _parse_message_ids(raw: Optional[str]) -> list[int]:
    """'12,13,14' -> [12, 13, 14] (abaikan entri rusak)."""
    ids: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


class ReplayBot:
    """
    Listener Telegram Bot API (long polling) untuk deep-link download publik.

    Hanya merespons `/start <youtube_video_id>`; command lain dijawab pesan
    sambutan. Tidak ada whitelist karena memang untuk publik.
    """

    def __init__(self) -> None:
        self._token: str = Config.TELEGRAM_REPLAY_BOT_TOKEN
        self._archive_channel: int = Config.TELEGRAM_ARCHIVE_CHANNEL_ID
        self._client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._offset = 0
        self._bot_username = ""

    # ── HTTP plumbing (meniru AdminBot) ────────────────────────────────────
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=POLL_TIMEOUT + 15)
        return self._client

    async def _api(self, method: str, payload: Optional[dict] = None) -> dict:
        """Call a Telegram Bot API method, retrying on 429 / network errors."""
        url = API_BASE.format(token=self._token, method=method)
        last_error: Optional[Exception] = None

        for attempt in range(1, 4):
            try:
                resp = await self._get_client().post(url, json=payload or {})
                try:
                    body = resp.json()
                except ValueError:
                    raise RuntimeError(f"Telegram API {method}: respons bukan JSON")

                if body.get("ok"):
                    return body

                params = body.get("parameters") or {}
                if resp.status_code == 429 and params.get("retry_after"):
                    wait = float(params["retry_after"]) + 1
                    logger.warning("Telegram rate limit — tunggu %.0fs", wait)
                    await asyncio.sleep(wait)
                    continue
                raise RuntimeError(
                    f"Telegram API {method} gagal: {body.get('description')}"
                )
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning(
                    "Telegram API %s network error (attempt %d/3): %s", method, attempt, exc
                )
                await asyncio.sleep(2 * attempt)

        raise RuntimeError(f"Telegram API {method} gagal: {last_error}")

    async def send_message(
        self, chat_id: int, text: str, parse_mode: Optional[str] = None
    ) -> Optional[int]:
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        body = await self._api("sendMessage", payload)
        return (body.get("result") or {}).get("message_id")

    async def copy_message(
        self, chat_id: int, from_chat_id: int, message_id: int
    ) -> Optional[int]:
        """Salin pesan (video) dari channel arsip ke user. Tanpa re-upload."""
        body = await self._api(
            "copyMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
            },
        )
        return (body.get("result") or {}).get("message_id")

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start background polling task. False bila dinonaktifkan/config kurang."""
        if not Config.REPLAY_BOT_ENABLED:
            logger.info("Replay bot dinonaktifkan (REPLAY_BOT_ENABLED=false).")
            return False
        if not self._token:
            logger.warning(
                "TELEGRAM_REPLAY_BOT_TOKEN belum diisi — replay bot tidak dijalankan."
            )
            return False
        if not self._archive_channel:
            logger.warning(
                "TELEGRAM_ARCHIVE_CHANNEL_ID belum diisi — replay bot tidak dijalankan."
            )
            return False
        if self._task and not self._task.done():
            return True
        self._running = True
        self._task = asyncio.create_task(self.run(), name="replay-bot")
        return True

    async def run(self) -> None:
        try:
            me = await self._api("getMe")
            result = me.get("result") or {}
            self._bot_username = result.get("username") or ""
            logger.info("Replay bot aktif sebagai @%s", self._bot_username or "?")
        except Exception as exc:
            logger.error("Gagal verifikasi TELEGRAM_REPLAY_BOT_TOKEN: %s", exc)
            self._running = False
            return

        while self._running:
            try:
                updates = await self._get_updates()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("getUpdates gagal (retry 5s): %s", exc)
                await asyncio.sleep(5)
                continue

            for update in updates:
                try:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                except (TypeError, ValueError):
                    pass
                try:
                    await self._handle_update(update)
                except Exception as exc:
                    logger.exception("Gagal memproses update: %s", exc)

    async def _get_updates(self) -> list[dict]:
        payload: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
        if self._offset:
            payload["offset"] = self._offset
        body = await self._api("getUpdates", payload)
        return body.get("result") or []

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.close()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Update handling ────────────────────────────────────────────────────
    async def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        text = (message.get("text") or "").strip()
        if not text:
            return

        try:
            await self._dispatch(int(chat_id), text)
        except Exception as exc:
            logger.exception("Replay bot error untuk chat %s: %s", chat_id, exc)
            try:
                await self.send_message(chat_id, ERROR_TEXT)
            except Exception:
                pass

    async def _dispatch(self, chat_id: int, text: str) -> None:
        if not text.startswith("/"):
            await self.send_message(chat_id, WELCOME_TEXT, parse_mode="HTML")
            return

        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]

        if command == "/start":
            if not args:
                await self.send_message(chat_id, WELCOME_TEXT, parse_mode="HTML")
                return
            await self._send_replay(chat_id, args[0])
            return

        # /help atau command lain -> pesan sambutan saja
        await self.send_message(chat_id, WELCOME_TEXT, parse_mode="HTML")

    async def _send_replay(self, chat_id: int, payload: str) -> None:
        """Tangani payload deep-link: 'notify_<id>' (pra-rilis) atau download."""
        # Payload "notify_" = user menekan "Ingatkan" pada video yang BELUM rilis.
        # Jangan coba kirim video (memang belum ada) — cukup konfirmasi.
        if payload.startswith("notify_"):
            await self.send_message(chat_id, NOTIFY_TEXT, parse_mode="HTML")
            return

        # Payload "tt_<post_id>" = arsip TikTok (video/foto/story). Foto dikirim
        # sebagai album sehingga user menerima FOTONYA, bukan hanya video slide.
        if payload.startswith("tt_"):
            await self._send_tiktok(chat_id, payload[3:])
            return

        session = database.get_archived_session_by_youtube_id(payload)
        if not session:
            logger.info("Payload tidak dikenal / belum diarsipkan: %r", payload)
            await self.send_message(chat_id, NOT_FOUND_TEXT)
            return

        msg_ids = _parse_message_ids(session.get("telegram_message_ids"))
        if not msg_ids:
            logger.warning(
                "Sesi %s punya arsip tapi telegram_message_ids kosong/rusak",
                session.get("live_id"),
            )
            await self.send_message(chat_id, NOT_FOUND_TEXT)
            return

        sent = 0
        for mid in msg_ids:
            try:
                res = await self.copy_message(chat_id, self._archive_channel, mid)
                if res:
                    sent += 1
            except Exception as exc:
                logger.exception("copyMessage gagal (msg %s): %s", mid, exc)

        if sent == 0:
            await self.send_message(chat_id, ERROR_TEXT)
            return

        logger.info(
            "Replay %s (%s) terkirim ke chat %s (%d/%d part)",
            session.get("live_id"),
            payload,
            chat_id,
            sent,
            len(msg_ids),
        )

    async def _send_tiktok(self, chat_id: int, post_id: str) -> None:
        """
        Kirim arsip TikTok (video/foto/story) dari channel arsip ke user.

        Foto dikirim apa adanya (copyMessage per pesan album) sehingga user
        menerima FOTONYA — sesuai kebutuhan fitur "download dalam bentuk foto".
        """
        post = database.tiktok_posts_for_replay(post_id)
        if not post:
            logger.info("Arsip TikTok tidak dikenal / belum siap: %r", post_id)
            await self.send_message(chat_id, TIKTOK_NOT_FOUND_TEXT)
            return

        msg_ids = _parse_message_ids(post.get("telegram_message_ids"))
        if not msg_ids:
            await self.send_message(chat_id, TIKTOK_NOT_FOUND_TEXT)
            return

        kind = (post.get("kind") or "video").strip() or "video"
        is_story = bool(post.get("is_story"))
        image_count = int(post.get("image_count") or 0)

        # Info singkat lebih dulu: menjelaskan bahwa foto dikirim sebagai album
        # (multi-part) supaya user tidak mengira ada media yang hilang.
        if kind == "photo":
            total_parts = max(1, (len(msg_ids) + 9) // 10)
            await self.send_message(
                chat_id,
                TIKTOK_PHOTO_INFO_TEXT.format(
                    count=image_count or len(msg_ids),
                    parts=total_parts,
                ),
                parse_mode="HTML",
            )

        sent = 0
        for mid in msg_ids:
            try:
                res = await self.copy_message(chat_id, self._archive_channel, mid)
                if res:
                    sent += 1
            except Exception as exc:
                logger.exception("copyMessage TikTok gagal (msg %s): %s", mid, exc)

        if sent == 0:
            await self.send_message(chat_id, ERROR_TEXT)
            return
        logger.info(
            "Arsip TikTok %s (%s%s) terkirim ke chat %s (%d/%d pesan)",
            post_id, kind, " story" if is_story else "", chat_id, sent, len(msg_ids),
        )


# ─── Standalone runner ─────────────────────────────────────────────────────
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


async def _run_standalone() -> None:
    _setup_logging()
    database.init_db()

    if not Config.TELEGRAM_REPLAY_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_REPLAY_BOT_TOKEN belum diisi di .env.\n"
            "Buat bot via @BotFather, lalu isi: TELEGRAM_REPLAY_BOT_TOKEN=<token>"
        )

    bot = ReplayBot()
    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("Sinyal berhenti diterima...")
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, ValueError):
            pass  # e.g. Windows

    try:
        await bot.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await bot.stop()


def main() -> None:
    asyncio.run(_run_standalone())


if __name__ == "__main__":
    main()
