"""
config.py - Centralized configuration loader for JKT48 Live Bot.
Reads all settings from environment variables / .env file.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ChannelConfig:
    label: str
    token_file: str
    secret_file: str


class Config:
    # ─── Telegram (Telethon Userbot for Text Notifications) ─────────────
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_PHONE: str = os.getenv("TELEGRAM_PHONE", "")
    TELEGRAM_CHANNEL_ID: int = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
    TELEGRAM_SESSION_STRING: str = os.getenv("TELEGRAM_SESSION_STRING", "")

    # Optional Admin Chat ID for critical error alerts (e.g. quota limit reached on all channels)
    ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0"))

    # ─── Telegram Admin Bot (BotFather token, long polling) ─────────────
    # Token from @BotFather, e.g. 123456:AAE...
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    # Extra admin chat IDs allowed to control the bot (comma separated).
    # ADMIN_CHAT_ID is always included automatically.
    TELEGRAM_ADMIN_IDS: str = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()

    # Enable the admin bot listener inside the main bot process (true/false)
    ADMIN_BOT_ENABLED: bool = (
        os.getenv("ADMIN_BOT_ENABLED", "true").lower() == "true"
    )

    # Upload Target: "telegram" (default) or "youtube"
    UPLOAD_TARGET: str = os.getenv("UPLOAD_TARGET", "telegram").lower().strip()

    # Telegram max file size threshold before splitting (MB)
    # Default: 1950 MB (safe buffer under 2000 MB / 2 GB limit for standard accounts)
    TELEGRAM_MAX_FILE_SIZE_MB: int = int(os.getenv("TELEGRAM_MAX_FILE_SIZE_MB", "1950"))

    # ─── YouTube Data API v3 ────────────────────────────────────────────
    # Fallback single channel config (backward compatibility)
    YOUTUBE_CLIENT_SECRET_FILE: str = os.getenv(
        "YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json"
    )
    YOUTUBE_TOKEN_FILE: str = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")

    # ─── Zona Waktu ─────────────────────────────────────────────────────
    # VPS bisa berjalan di zona waktu mana pun (mis. Seoul = UTC+9). Waktu
    # DISIMPAN sebagai UTC; nilai di bawah hanya untuk TAMPILAN (caption
    # Telegram, judul/deskripsi YouTube, log).
    #
    # Offset zona waktu penonton terhadap UTC. 7 = WIB. 8 = WITA. 9 = WIT.
    DISPLAY_TIMEZONE_OFFSET_HOURS: float = float(
        os.getenv("DISPLAY_TIMEZONE_OFFSET_HOURS", "7")
    )

    # Label zona waktu yang ikut ditampilkan.
    DISPLAY_TIMEZONE_LABEL: str = os.getenv("DISPLAY_TIMEZONE_LABEL", "WIB")

    # Hanya untuk baris LAMA yang waktunya tidak punya penanda zona waktu.
    # Nilai naif dianggap UTC kecuali offset di sini diisi (mis. 7 bila server
    # lama memakai WIB, atau 8/9 sesuai kenyataannya). Nilai baru selalu UTC
    # sehingga pengaturan ini tidak memengaruhinya.
    LEGACY_NAIVE_TIME_OFFSET_HOURS: float = float(
        os.getenv("LEGACY_NAIVE_TIME_OFFSET_HOURS", "0")
    )

    # ─── Bot Behaviour & Timers ─────────────────────────────────────────
    MEMBERS_FILE: str = os.getenv("MEMBERS_FILE", "members.txt")

    # How often to check HLS streams of registered members (seconds)
    HLS_CHECK_INTERVAL_SECONDS: int = int(
        os.getenv("HLS_CHECK_INTERVAL_SECONDS", "15")
    )

    # Merge window: duration to wait for reconnection before uploading (seconds)
    # Default: 3600 seconds (1 hour) — batas ATAS (safety upper bound)
    MERGE_WINDOW_SECONDS: int = int(
        os.getenv("MERGE_WINDOW_SECONDS", "3600")
    )

    # Finalize lebih cepat bila HLS sudah offline & idle selama N detik.
    # DEFAULT 0 = NONAKTIF (selalu tunggu MERGE_WINDOW_SECONDS penuh).
    #
    # JANGAN dinaikkan tanpa alasan: bukti nyata live jkt48_daisy (13 Sep 2026)
    # menunjukkan reconnect BISA lebih dari 50 menit:
    #   haii-260913192155 (19:21) -> haii-260913201248 (20:12) -> haii-260913201503
    # Kalau ambang ini lebih kecil dari jeda reconnect terpanjang, SATU live akan
    # terpecah menjadi beberapa video.
    MERGE_IDLE_FINALIZE_SECONDS: int = int(
        os.getenv("MERGE_IDLE_FINALIZE_SECONDS", "0")
    )

    # Pisahkan video bila JUDUL live berubah (indikasi live baru).
    # DEFAULT false = NONAKTIF, dan itu memang yang benar:
    #   - jkt48_carissa (15 Sep 2026) MENGEDIT JUDUL di tengah live → 4 slug, 3 judul,
    #     jeda 1-8 menit, tetap SATU live. Kalau fitur ini aktif, live itu jadi 3 video.
    #   - jkt48_daisy (13 Sep 2026) reconnect dengan judul sama tapi slug baru (jeda 51 mnt).
    # Kesimpulan: slug & judul TIDAK bisa dipakai sebagai penentu "live baru".
    # Satu-satunya penentu andal = JEDA WAKTU (MERGE_WINDOW_SECONDS).
    MERGE_SPLIT_ON_TITLE_CHANGE: bool = (
        os.getenv("MERGE_SPLIT_ON_TITLE_CHANGE", "false").lower() == "true"
    )

    # Hard cap: finalize paksa sebuah merge group setelah N jam supaya stream yang
    # "hang" tanpa disconnect tidak menunda upload selamanya. 0 = nonaktifkan.
    MERGE_MAX_GROUP_HOURS: float = float(
        os.getenv("MERGE_MAX_GROUP_HOURS", "6")
    )

    # Interval re-check ketika finalize ditunda (mis. member masih live).
    MERGE_DEFER_SECONDS: int = int(os.getenv("MERGE_DEFER_SECONDS", "300"))

    # ── IDN Lookup (OPSIONAL) ─────────────────────────────────────────
    # Dipakai HANYA untuk membedakan "reconnect" vs "live baru" (slug per sesi)
    # dan mengambil judul live. Bot tetap berfungsi penuh tanpa ini (HLS-only).
    # Set false kalau server IDN sedang bermasalah.
    IDN_LOOKUP_ENABLED: bool = (
        os.getenv("IDN_LOOKUP_ENABLED", "true").lower() == "true"
    )
    IDN_LOOKUP_TIMEOUT_SECONDS: float = float(
        os.getenv("IDN_LOOKUP_TIMEOUT_SECONDS", "8")
    )
    IDN_LOOKUP_CACHE_SECONDS: int = int(
        os.getenv("IDN_LOOKUP_CACHE_SECONDS", "30")
    )

    # ─── Showroom (OPSIONAL, default NONAKTIF) ────────────────────────────────
    # Selama False, bot berjalan PERSIS seperti sebelumnya (jalur Showroom tidak
    # pernah dijalankan). Aktifkan hanya setelah Fase 2 selesai & room sudah
    # di-seed lewat `python3 -m bot.seed_showroom`.
    SHOWROOM_ENABLED: bool = (
        os.getenv("SHOWROOM_ENABLED", "false").lower() == "true"
    )

    # Interval probe Showroom dipisah dari HLS_CHECK_INTERVAL_SECONDS (5 detik).
    # Menyematkan 58 room ke interval 5 detik ≈ 11,6 request/detik ke API Showroom
    # → risiko rate-limit. 30 detik ≈ 1,9 req/detik dan masih jauh lebih cepat
    # daripada durasi live (< 30 menit).
    SHOWROOM_CHECK_INTERVAL_SECONDS: int = int(
        os.getenv("SHOWROOM_CHECK_INTERVAL_SECONDS", "30")
    )

    # Batas request Showroom bersamaan per siklus.
    SHOWROOM_CONCURRENCY: int = int(os.getenv("SHOWROOM_CONCURRENCY", "10"))

    # Timeout satu panggilan API Showroom.
    SHOWROOM_TIMEOUT_SECONDS: float = float(
        os.getenv("SHOWROOM_TIMEOUT_SECONDS", "8")
    )

    # Berapa pembacaan offline BERTURUT-TURUT sebelum sinyal offline dianggap sah.
    # Ini hanya MEMPERLAMBAT finalisasi (mencegah satu hiccup API memecah live),
    # tidak pernah mempercepat — window 1 jam tetap berlaku seperti IDN.
    SHOWROOM_OFFLINE_CONFIRMATIONS: int = int(
        os.getenv("SHOWROOM_OFFLINE_CONFIRMATIONS", "3")
    )

    # Sumber daftar room Showroom untuk seed.
    SHOWROOM_ROOMS_FILE: str = os.getenv("SHOWROOM_ROOMS_FILE", "showroom_rooms.json")

    # Waktu maksimal menunggu segmen aktif selesai saat bot dihentikan (shutdown),
    # supaya segmen parsial tidak hilang ketika `pm2 restart`.
    GRACEFUL_SHUTDOWN_SECONDS: int = int(
        os.getenv("GRACEFUL_SHUTDOWN_SECONDS", "25")
    )

    # Directory on VPS to save downloads temporarily
    DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "/tmp/jkt48-lives")

    # SQLite database file path
    DB_PATH: str = os.getenv("DB_PATH", "jkt48_live.db")

    # Delete local file after successful YouTube upload?
    AUTO_DELETE_AFTER_UPLOAD: bool = (
        os.getenv("AUTO_DELETE_AFTER_UPLOAD", "true").lower() == "true"
    )

    @classmethod
    def load_youtube_channels(cls) -> list[ChannelConfig]:
        """
        Load configured YouTube channels from environment variables.
        Supports multi-channel:
          YT_CHANNEL_1_TOKEN, YT_CHANNEL_1_SECRET, YT_CHANNEL_1_LABEL
          YT_CHANNEL_2_TOKEN, YT_CHANNEL_2_SECRET, YT_CHANNEL_2_LABEL
          ...
        Falls back to legacy YOUTUBE_TOKEN_FILE / YOUTUBE_CLIENT_SECRET_FILE if no numbered channels found.
        """
        channels: list[ChannelConfig] = []
        i = 1
        while True:
            token = os.getenv(f"YT_CHANNEL_{i}_TOKEN")
            secret = os.getenv(f"YT_CHANNEL_{i}_SECRET")
            label = os.getenv(f"YT_CHANNEL_{i}_LABEL", f"Channel {i}")
            if token and secret:
                channels.append(ChannelConfig(label=label, token_file=token, secret_file=secret))
                i += 1
            else:
                break

        # Fallback to single channel if none found
        if not channels:
            channels.append(
                ChannelConfig(
                    label="Channel Utama",
                    token_file=cls.YOUTUBE_TOKEN_FILE,
                    secret_file=cls.YOUTUBE_CLIENT_SECRET_FILE,
                )
            )

        return channels

    @classmethod
    def load_members(cls) -> set[str]:
        """
        Load the member username whitelist from MEMBERS_FILE.
        Returns a set of lowercase usernames (e.g. {'jkt48_delynn', 'jkt48_intan'}).
        """
        members: set[str] = set()
        path = cls.MEMBERS_FILE
        if not path or not os.path.exists(path):
            return members
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if not line or line.startswith("#"):
                    continue
                if "/" in line:
                    line = line.rstrip("/").split("/")[-1]
                members.add(line)
        return members

    @classmethod
    def admin_ids(cls) -> list[int]:
        """
        Chat IDs allowed to control the admin bot.
        ADMIN_CHAT_ID is always included, plus any IDs in TELEGRAM_ADMIN_IDS
        (comma separated). An empty list means "no restriction" — the caller
        should warn about that.
        """
        ids: list[int] = []
        if cls.ADMIN_CHAT_ID:
            ids.append(int(cls.ADMIN_CHAT_ID))
        for part in cls.TELEGRAM_ADMIN_IDS.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value not in ids:
                ids.append(value)
        return ids

    @classmethod
    def validate(cls) -> None:
        """Raise ValueError if any required config is missing."""
        required = {
            "TELEGRAM_API_ID": cls.TELEGRAM_API_ID,
            "TELEGRAM_API_HASH": cls.TELEGRAM_API_HASH,
            "TELEGRAM_PHONE": cls.TELEGRAM_PHONE,
            "TELEGRAM_CHANNEL_ID": cls.TELEGRAM_CHANNEL_ID,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Please copy .env.example → .env and fill in the values."
            )
