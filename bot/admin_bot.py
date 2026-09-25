"""
admin_bot.py - Telegram admin bot (BotFather token) untuk mengontrol bot dari HP.

Implementasi long polling memakai Bot HTTP API via `httpx` (tanpa dependency baru).
Listener ini otomatis dijalankan di dalam proses `bot.main` (lihat Config.ADMIN_BOT_ENABLED)
atau bisa dijalankan terpisah: `python3 -m bot.admin_bot`.

Hanya chat ID yang terdaftar (ADMIN_CHAT_ID + TELEGRAM_ADMIN_IDS) yang diizinkan.

Command:
    /help                      tampilkan bantuan
    /list [mode]               daftar member (all|active|stopped|live|unknown)
    /info <username>           detail member + HLS
    /add <username> [Nama]     daftar/aktifkan member
    /stop <username...>        hentikan rekam (rekaman aktif langsung dihentikan)
    /resume <username...>      aktifkan kembali member yang di-stop
    /remove <username...>      hapus dari database & members.txt
    /sethls <username> <url>   set HLS URL manual
    /live                      sesi yang sedang berjalan
    /status                    ringkasan + antrian upload
    /id                        tampilkan chat ID Anda
"""
import asyncio
import logging
import signal
from typing import Awaitable, Callable, Optional

import httpx

from bot import database, member_manager
from bot.config import Config

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE_LEN = 3900
POLL_TIMEOUT = 25

HELP_TEXT = (
    "🤖 <b>JKT48 Live Bot — Admin</b>\n\n"
    "<b>Lihat data</b>\n"
    "/list — semua member + status HLS\n"
    "/list active — hanya yang dipantau\n"
    "/list stopped — yang di-stop\n"
    "/list live — sesi berjalan\n"
    "/list unknown — aktif tapi HLS belum diketahui\n"
    "/info &lt;username&gt; — detail 1 member\n"
    "/live — sesi yang sedang direkam/upload\n"
    "/status — ringkasan + antrian upload\n\n"
    "<b>Kelola channel</b>\n"
    "/add &lt;username&gt; [Nama Tampilan]\n"
    "/stop &lt;username&gt; [username2 ...]\n"
    "/resume &lt;username&gt;\n"
    "/remove &lt;username&gt;\n"
    "/sethls &lt;username&gt; &lt;url .m3u8&gt;\n\n"
    "Bisa pakai nama pendek, mis. <code>/stop lulu</code>.\n"
    "Perubahan berlaku pada siklus poll berikutnya (±15 detik) tanpa restart."
)


def split_text(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split a long message into <= limit chunks (newline aware)."""
    text = text or ""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if size + line_len > limit and current:
            chunks.append("\n".join(current))
            current = []
            size = 0
        # Very long single line → hard split
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
            line_len = len(line) + 1
        current.append(line)
        size += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks
class AdminBot:
    """
    Listener Telegram Bot API (long polling) untuk command admin.

    on_stop_recording: callback sinkron `fn(username) -> str` yang dipanggil
    setelah /stop sukses, untuk menghentikan rekaman yang sedang berjalan
    (main.py menghentikan proses ffmpeg & mengembalikan pesan status).
    """

    def __init__(
        self,
        on_stop_recording: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._token: str = Config.TELEGRAM_BOT_TOKEN
        self._admin_ids: list[int] = Config.admin_ids()
        self._on_stop_recording = on_stop_recording
        self._client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._offset = 0
        self._bot_username = ""

    # ── HTTP plumbing ──────────────────────────────────────────────────────
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
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> Optional[int]:
        """Send a (possibly long) message; returns the last message_id."""
        last_id: Optional[int] = None
        for chunk in split_text(text):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            body = await self._api("sendMessage", payload)
            last_id = (body.get("result") or {}).get("message_id")
        return last_id

    async def notify_admins(self, text: str, parse_mode: Optional[str] = None) -> None:
        """Kirim pesan ke semua admin yang terdaftar (best-effort)."""
        for chat_id in self._admin_ids:
            try:
                await self.send_message(chat_id, text, parse_mode=parse_mode)
            except Exception as exc:
                logger.warning("Gagal kirim notifikasi ke %s: %s", chat_id, exc)

    async def _get_updates(self) -> list[dict]:
        payload: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
        if self._offset:
            payload["offset"] = self._offset
        body = await self._api("getUpdates", payload)
        return body.get("result") or []
    # ── Lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> Optional[asyncio.Task]:
        """Start the listener as a background task (no-op without a token)."""
        if not Config.ADMIN_BOT_ENABLED:
            logger.info("Admin bot dinonaktifkan (ADMIN_BOT_ENABLED=false).")
            return None
        if not self._token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN belum diisi — Telegram admin bot tidak dijalankan."
            )
            return None
        if not self._admin_ids:
            logger.error(
                "ADMIN_CHAT_ID / TELEGRAM_ADMIN_IDS kosong — admin bot TIDAK "
                "dijalankan (fail-closed). Isi ADMIN_CHAT_ID di .env lalu restart."
            )
            return None
        self._running = True
        self._task = asyncio.create_task(self.run(), name="admin-bot")
        return self._task

    async def run(self) -> None:
        """Long-polling loop until stopped."""
        self._running = True
        if not self._admin_ids:
            logger.error(
                "ADMIN_CHAT_ID / TELEGRAM_ADMIN_IDS kosong — menutup admin bot "
                "(fail-closed). Isi ADMIN_CHAT_ID di .env lalu restart."
            )
            self._running = False
            return

        try:
            me = await self._api("getMe")
            result = me.get("result") or {}
            self._bot_username = result.get("username") or ""
            logger.info(
                "Telegram admin bot aktif sebagai @%s (admins: %s)",
                self._bot_username or "?",
                self._admin_ids,
            )
        except Exception as exc:
            logger.error("Gagal verifikasi TELEGRAM_BOT_TOKEN: %s", exc)
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

    async def stop(self) -> None:
        """Stop the listener and close the HTTP client."""
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

    # ── Update handling ───────────────────────────────────────────────────
    async def _handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        text = (message.get("text") or "").strip()
        if not text:
            return

        if not self._admin_ids or int(chat_id) not in self._admin_ids:
            logger.warning("Command ditolak dari chat_id=%s", chat_id)
            await self.send_message(
                chat_id,
                "⛔ Akses ditolak.\n"
                f"Chat ID Anda: <code>{chat_id}</code>\n"
                "Tambahkan ID ini ke ADMIN_CHAT_ID / TELEGRAM_ADMIN_IDS di .env.",
                parse_mode="HTML",
            )
            return

        try:
            await self._dispatch(int(chat_id), text)
        except Exception as exc:
            logger.exception("Command error untuk chat %s: %s", chat_id, exc)
            await self.send_message(chat_id, f"❌ Terjadi error: {exc}")

    async def _dispatch(self, chat_id: int, text: str) -> None:
        """Parse & route one command message."""
        if not text.startswith("/"):
            await self.send_message(chat_id, "Kirim /help untuk melihat daftar command.")
            return

        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]

        if command in ("/start", "/help"):
            await self.send_message(chat_id, HELP_TEXT, parse_mode="HTML")
            return

        if command == "/id":
            await self.send_message(
                chat_id,
                f"Chat ID Anda: <code>{chat_id}</code>\nBot: @{self._bot_username or '?'}",
                parse_mode="HTML",
            )
            return

        if command == "/list":
            mode = args[0].lower() if args else "all"
            if mode not in member_manager.LIST_MODES:
                await self.send_message(
                    chat_id,
                    f"Mode '{mode}' tidak dikenal. Pilih: {', '.join(member_manager.LIST_MODES)}",
                )
                return
            rows = member_manager.list_members(mode)
            await self.send_message(chat_id, member_manager.format_member_list(rows, mode))
            await self.send_message(
                chat_id, member_manager.format_status_summary(member_manager.list_members("all"))
            )
            return

        if command == "/info":
            if not args:
                await self.send_message(chat_id, "Format: /info <username>")
                return
            await self._cmd_info(chat_id, args[0])
            return

        if command == "/live":
            rows = member_manager.list_members("live")
            if not rows:
                await self.send_message(chat_id, "🎙 Tidak ada sesi berjalan saat ini.")
                return
            lines = ["🎙 Sesi berjalan:", ""]
            lines.extend(member_manager.format_member_line(r) for r in rows)
            await self.send_message(chat_id, "\n".join(lines))
            return

        if command == "/status":
            await self._cmd_status(chat_id)
            return

        if command == "/add":
            if not args:
                await self.send_message(
                    chat_id,
                    "Format: /add &lt;username&gt; [Nama Tampilan]\n"
                    "Contoh: /add jkt48_baru Baru JKT48",
                    parse_mode="HTML",
                )
                return
            username = args[0]
            display_name = " ".join(args[1:]).strip() or None
            result = member_manager.add_member(username, display_name=display_name)
            await self.send_message(chat_id, result["message"], parse_mode="HTML")
            return

        if command in ("/stop", "/resume", "/remove"):
            if not args:
                await self.send_message(chat_id, f"Format: {command} <username> [username2 ...]")
                return
            await self._cmd_bulk(chat_id, command, args)
            return

        if command == "/sethls":
            if len(args) < 2:
                await self.send_message(chat_id, "Format: /sethls <username> <url .m3u8>")
                return
            await self._cmd_sethls(chat_id, args[0], args[1])
            return

        await self.send_message(chat_id, f"Command '{command}' tidak dikenal.\n\nKirim /help.")

    # ── Handlers ──────────────────────────────────────────────────────────
    async def _cmd_info(self, chat_id: int, token: str) -> None:
        row, error = member_manager.resolve_member_token(token)
        if row is None:
            await self.send_message(chat_id, f"❌ {error}")
            return
        text = member_manager.format_member_detail(row)
        if row["hls_url"]:
            text += f"\nHLS raw    : {row['hls_url']}"
        await self.send_message(chat_id, text)

    async def _cmd_status(self, chat_id: int) -> None:
        summary = member_manager.format_status_summary(member_manager.list_members("all"))
        pending = database.get_all_pending_videos()
        text = f"{summary}\n\n📤 Antrian upload (pending): {len(pending)}"
        for item in pending[:5]:
            text += (
                f"\n  • {item.get('member_name') or item.get('member_username')}"
                f" — {item.get('status')}"
            )
        await self.send_message(chat_id, text)

    async def _cmd_bulk(self, chat_id: int, command: str, tokens: list[str]) -> None:
        """Jalankan /stop, /resume, atau /remove untuk beberapa username."""
        for token in tokens:
            if command == "/stop":
                result = member_manager.stop_member(token)
                if result.get("ok") and result.get("was_enabled"):
                    result["message"] += self._cancel_active_recording(result["username"])
            elif command == "/resume":
                result = member_manager.resume_member(token)
            else:
                result = member_manager.remove_member(token)
            await self.send_message(chat_id, result["message"], parse_mode="HTML")

    def _cancel_active_recording(self, username: str) -> str:
        """Panggil callback main.py untuk menghentikan rekaman yang sedang jalan."""
        if not self._on_stop_recording:
            return ""
        try:
            extra = self._on_stop_recording(username) or ""
        except Exception as exc:
            logger.exception("Gagal membatalkan rekaman %s: %s", username, exc)
            return "\n⚠️ Gagal menghentikan rekaman aktif — cek log bot."
        return f"\n{extra}" if extra else ""

    async def _cmd_sethls(self, chat_id: int, username: str, url: str) -> None:
        result = member_manager.set_member_hls(username, url)
        await self.send_message(chat_id, result["message"], parse_mode="HTML")
# ─── Standalone runner ─────────────────────────────────────────────────────

def _setup_logging() -> None:
    """Minimal console logging for standalone usage."""
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

    if not Config.TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN belum diisi di .env.\n"
            "Buat bot via @BotFather, lalu isi: TELEGRAM_BOT_TOKEN=<token>"
        )

    bot = AdminBot()
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