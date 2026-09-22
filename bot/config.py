"""
config.py - Centralized configuration loader for JKT48 Live Bot.
Reads all settings from environment variables / .env file.
"""
import os
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, find_dotenv, load_dotenv
from dotenv.parser import parse_stream

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

    # ─── Telegram Replay Bot (publik, BotFather token) ──────────────────
    # Bot publik untuk fitur "Download via Bot Telegram" di halaman watch.
    # User menekan tombol → t.me/<bot>?start=<youtube_video_id> → bot
    # menyalin (copyMessage) video dari channel arsip privat ke user.
    TELEGRAM_REPLAY_BOT_TOKEN: str = os.getenv("TELEGRAM_REPLAY_BOT_TOKEN", "").strip()

    # Username bot publik (tanpa @) untuk membentuk link deep-link di website.
    REPLAY_BOT_USERNAME: str = os.getenv("REPLAY_BOT_USERNAME", "").strip().lstrip("@")

    # Channel Telegram PRIVAT sebagai database arsip video replay.
    # Userbot Telethon (TELEGRAM_SESSION_STRING / phone) harus menjadi member
    # dan bot publik harus menjadi admin agar bisa copyMessage dari sini.
    TELEGRAM_ARCHIVE_CHANNEL_ID: int = int(os.getenv("TELEGRAM_ARCHIVE_CHANNEL_ID", "0"))

    # Upload juga video ke channel arsip Telegram (selain YouTube). File lokal
    # baru dihapus setelah KEDUA upload sukses; jika salah satu gagal, sesi
    # ditandai pending_upload agar di-retry.
    TELEGRAM_ARCHIVE_UPLOAD_ENABLED: bool = (
        os.getenv("TELEGRAM_ARCHIVE_UPLOAD_ENABLED", "true").lower() == "true"
    )

    # Enable the public replay bot listener inside the main bot process (true/false)
    REPLAY_BOT_ENABLED: bool = (
        os.getenv("REPLAY_BOT_ENABLED", "true").lower() == "true"
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

    # ── Ketahanan rekaman Showroom ────────────────────────────────────
    # Insiden 18 Sep 2026 (Sona): live mulai 20:18:39 WIB tapi bot baru
    # berhasil merekam ±8-10 menit kemudian karena HLS Showroom belum feeding
    # saat deteksi — task gagal cepat, lalu menunggu siklus deteksi berikutnya,
    # berulang-ulang. Sekarang task rekaman TETAP TINGGAL sampai live benar-
    # benar berakhir: kalau yt-dlp berhenti lebih awal / belum menghasilkan
    # output, task resume dengan URL HLS segar (token Showroom bisa berotasi).
    SHOWROOM_EMPTY_RETRIES: int = int(os.getenv("SHOWROOM_EMPTY_RETRIES", "30"))

    # Berapa banyak resume maksimal per sesi Showroom (40 × ±10 detik jeda +
    # durasi tiap percobaan — cukup untuk jeda reconnect; jeda lebih panjang
    # akan ditangani deteksi ulang di loop utama seperti IDN).
    SHOWROOM_MAX_RESUMES: int = int(os.getenv("SHOWROOM_MAX_RESUMES", "40"))

    # Jeda sebelum tiap percobaan resume (detik).
    SHOWROOM_RESUME_DELAY_SECONDS: float = float(
        os.getenv("SHOWROOM_RESUME_DELAY_SECONDS", "10")
    )

    # Mulai log PERINGATAN bila bot merekam N detik setelah live resmi dimulai.
    SHOWROOM_LATE_START_WARN_SECONDS: int = int(
        os.getenv("SHOWROOM_LATE_START_WARN_SECONDS", "60")
    )

    # Waktu maksimal menunggu segmen aktif selesai saat bot dihentikan (shutdown),
    # supaya segmen parsial tidak hilang ketika `pm2 restart`.
    GRACEFUL_SHUTDOWN_SECONDS: int = int(
        os.getenv("GRACEFUL_SHUTDOWN_SECONDS", "25")
    )

    # Directory on VPS to save downloads temporarily
    DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "/tmp/jkt48-lives")

    # Ruang disk minimum (MB) sebelum mulai recording baru. Di bawah ambang
    # ini deteksi live di-skip + warning log (bukan crash) supaya yt-dlp/ffmpeg
    # tidak gagal di tengah jalan dan meninggalkan sesi macet.
    MIN_FREE_DISK_MB: int = int(os.getenv("MIN_FREE_DISK_MB", "2048"))

    # Thumbnail kolase 3x2 utk video BARU (true/false). Dibuat dari file lokal
    # via ffmpeg lalu dipasang ke YouTube (~50 unit kuota). Gagal membuat /
    # memasang thumbnail TIDAK menggagalkan upload. Video lama tak disentuh.
    THUMBNAIL_COLLAGE_ENABLED: bool = (
        os.getenv("THUMBNAIL_COLLAGE_ENABLED", "true").lower() == "true"
    )

    # SQLite database file path
    DB_PATH: str = os.getenv("DB_PATH", "jkt48_live.db")

    # Delete local file after successful YouTube upload?
    AUTO_DELETE_AFTER_UPLOAD: bool = (
        os.getenv("AUTO_DELETE_AFTER_UPLOAD", "true").lower() == "true"
    )

    # ─── Arsip TikTok (OPSIONAL, default NONAKTIF) ──────────────────────
    # Selama False, bot berjalan PERSIS seperti sebelumnya (jalur TikTok tidak
    # pernah dijalankan). Aktifkan setelah akun di-seed:
    #   python3 -m bot.seed_tiktok
    TIKTOK_ENABLED: bool = (
        os.getenv("TIKTOK_ENABLED", "false").lower() == "true"
    )

    # Sumber daftar akun TikTok (dibaca saat seed, bukan setiap poll).
    TIKTOK_ACCOUNTS_FILE: str = os.getenv("TIKTOK_ACCOUNTS_FILE", "tiktok_accounts.json")

    # Cache daftar member resmi JKT48 (dari jkt48.com/api/v1). Dipakai
    # `bot/seed_tiktok.py` sebagai sumber OTORITATIF pemetaan akun TikTok →
    # member (field `tiktok_account`) + foto member untuk halaman /tiktok.
    # Perbarui dengan: python3 -m bot.jkt48_members --update
    JKT48_MEMBERS_FILE: str = os.getenv("JKT48_MEMBERS_FILE", "jkt48_members.json")

    # Jeda antar-request saat menarik roster resmi (detik). 58 member =
    # 1 request daftar + 58 request detail; jangan dirapatkan berlebihan.
    JKT48_MEMBERS_INTERVAL_SECONDS: float = float(
        os.getenv("JKT48_MEMBERS_INTERVAL_SECONDS", "0.35")
    )

    # Interval satu siklus pemantauan TikTok (detik). Satu siklus memeriksa
    # TIKTOK_ACCOUNTS_PER_CHECK akun (round-robin) supaya tidak menabrak batas
    # request.
    TIKTOK_CHECK_INTERVAL_SECONDS: int = int(
        os.getenv("TIKTOK_CHECK_INTERVAL_SECONDS", "300")
    )

    # Berapa akun diperiksa per siklus (round-robin). Lama kelamaan 1
    # akun/siklus × 300 dtk membuat satu akun baru dicek tiap ±4,25 jam (51
    # akun) — postingan telat masuk arsip dan story (kedaluwarsa 24 jam) mudah
    # terlewat. 3 akun/siklus ≈ tiap akun dicek tiap ±85 menit; bebannya tetap
    # kecil (±2-4 request per akun, diatur TIKTOK_REQUEST_INTERVAL_SECONDS).
    TIKTOK_ACCOUNTS_PER_CHECK: int = int(os.getenv("TIKTOK_ACCOUNTS_PER_CHECK", "3"))

    # Jeda minimum antar request ke sumber data (detik).
    # tikwm.com gratis dibatasi ±1 request/detik; 1.1 detik = aman.
    TIKTOK_REQUEST_INTERVAL_SECONDS: float = float(
        os.getenv("TIKTOK_REQUEST_INTERVAL_SECONDS", "1.1")
    )

    # Penyedia data: "auto" (tikwm lalu yt-dlp), "tikwm", "ytdlp", atau
    # "fixture" (baca JSON lokal — dipakai uji tanpa jaringan).
    TIKTOK_PROVIDER: str = os.getenv("TIKTOK_PROVIDER", "auto").lower().strip()

    # Profil impersonasi TLS untuk curl_cffi. Cloudflare di tikwm.com & halaman
    # TikTok menjawab 403 pada request biasa, tetapi lolos dengan fingerprint
    # browser ini (uji 21 Sep 2026). Kosongkan untuk memakai default "chrome131".
    TIKTOK_IMPERSONATE: str = os.getenv("TIKTOK_IMPERSONATE", "chrome131").strip()

    # Jeda tikwm saat rate-limit per detik (detik) dan saat kuota harian habis.
    # Kuota gratis tikwm ±10.000 request/hari; pesannya memuat "day"/"10000".
    TIKWM_RATE_COOLDOWN_SECONDS: int = int(os.getenv("TIKWM_RATE_COOLDOWN_SECONDS", "5"))
    TIKWM_QUOTA_COOLDOWN_SECONDS: int = int(
        os.getenv("TIKWM_QUOTA_COOLDOWN_SECONDS", "1800")
    )

    # Direktori fixture untuk TIKTOK_PROVIDER=fixture (dan unit test).
    TIKTOK_FIXTURE_DIR: str = os.getenv("TIKTOK_FIXTURE_DIR", "tests/fixtures/tiktok")

    # Ambil story juga (default aktif). Story hilang setelah ±24 jam, jadi
    # story yang ditemukan langsung disimpan.
    TIKTOK_STORIES_ENABLED: bool = (
        os.getenv("TIKTOK_STORIES_ENABLED", "true").lower() == "true"
    )

    # Upload ke YouTube (slide show untuk postingan foto). Bila false, arsip
    # hanya masuk channel Telegram (YouTube dilewati).
    TIKTOK_YT_UPLOAD_ENABLED: bool = (
        os.getenv("TIKTOK_YT_UPLOAD_ENABLED", "true").lower() == "true"
    )

    # Jumlah foto maksimum per album Telegram. Postingan foto > nilai ini
    # dikirim sebagai beberapa part (default 10, batas album Telegram).
    TIKTOK_PHOTOS_PER_PART: int = int(os.getenv("TIKTOK_PHOTOS_PER_PART", "10"))

    # Berapa lama tiap foto tampil di slide show YouTube (detik).
    TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO: float = float(
        os.getenv("TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO", "3")
    )

    # Batas jumlah postingan baru yang diproses per siklus (anti-banjir saat
    # akun baru pertama kali dipantau).
    TIKTOK_MAX_POSTS_PER_CHECK: int = int(
        os.getenv("TIKTOK_MAX_POSTS_PER_CHECK", "10")
    )

    # Jangan proses story lebih tua dari N jam (story kedaluwarsa di TikTok).
    TIKTOK_STORY_MAX_AGE_HOURS: int = int(
        os.getenv("TIKTOK_STORY_MAX_AGE_HOURS", "24")
    )

    # Berapa backlog YouTube (arsip yang sudah masuk Telegram tetapi belum punya
    # video YouTube — mis. karena kuota harian habis saat pertama diarsipkan)
    # yang dikejar per siklus retry (~30 menit di loop utama). Kecil supaya
    # kuota harian tidak habis sekaligus; retry berhenti begitu upload gagal
    # (tanda kuota habis) dan berlanjut di siklus berikutnya.
    TIKTOK_YT_BACKLOG_PER_CHECK: int = int(os.getenv("TIKTOK_YT_BACKLOG_PER_CHECK", "2"))

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
        (comma separated). An empty list means the bot must not start
        (fail-closed — no chat is allowed to run commands).
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
# ─── Diagnostik .env (dipanggil sekali saat bot start) ────────────────────────
#
# Dua masalah .env yang sulit terlihat dan pernah menimbulkan kebingungan nyata:
#
#   1. Baris yang tidak bisa diparse python-dotenv — diabaikan DIAM-DIAM, hanya
#      muncul sebagai satu baris peringatan tanpa konteks. Nomor barisnya pun
#      bisa meleset satu baris bila ada baris kosong tepat sebelumnya (perilaku
#      parser: nomor mengikuti posisi penanda, bukan baris statement).
#      Kasus nyata: catatan "Offset zona waktu penonton terhadap UTC: 7 = WIB …"
#      yang lupa diberi tanda '#'.
#
#   2. Nilai .env yang DIKALAHKAN environment proses. `load_dotenv()` dipanggil
#      tanpa override=True, jadi variabel yang sudah ada di environment proses
#      selalu menang. Di VPS, pm2 menyimpan environment saat proses pertama
#      dijalankan dan memakainya ulang setiap restart (`pm2 restart` biasa tidak
#      memperbarui cache — perlu `--update-env`). Kasus nyata:
#      SHOWROOM_CHECK_INTERVAL_SECONDS tetap 10 padahal .env sudah 30.

logger = logging.getLogger(__name__)

# Nama variabel yang nilainya tidak boleh muncul di log.
_SENSITIVE_KEY_RE = re.compile(
    r"(TOKEN|SECRET|HASH|SESSION|PASSWORD|PASSWD|PHONE|API_ID|_KEY)",
    re.IGNORECASE,
)


def env_file_path() -> Optional[str]:
    """Lokasi berkas .env yang dipakai bot (resolusi sama seperti load_dotenv())."""
    try:
        return find_dotenv() or None
    except Exception:  # pragma: no cover - sangat langka
        return None


def _env_path_or_default(env_path: Optional[str]) -> Optional[str]:
    """Pakai path yang diberikan; kalau tidak, cari .env seperti load_dotenv()."""
    path = env_path or env_file_path()
    if not path or not os.path.exists(path):
        return None
    return path


def mask_env_value(key: str, value: Optional[str]) -> str:
    """
    Samarkan nilai sensitif sebelum ditulis ke log.

    Nilai kredensial hanya ditampilkan panjangnya, sehingga perbandingan
    ".env vs environment" tetap bisa didiagnosis tanpa membocorkan isi.
    """
    if value is None:
        return "(tidak ada)"
    if _SENSITIVE_KEY_RE.search(key or ""):
        return f"<disembunyikan: {len(value)} karakter>"
    return value if len(value) <= 60 else value[:57] + "..."


def mask_env_statement(statement: str) -> str:
    """
    Samarkan sisi nilai sebuah baris .env bila nama variabelnya sensitif.

    Dipakai saat melaporkan baris .env yang tidak terbaca: baris rusak bisa saja
    memuat token, dan pesan diagnostik tidak boleh menjadi jalan kebocoran.
    """
    text = (statement or "").strip()
    if "=" not in text:
        return text
    key, value = text.split("=", 1)
    if _SENSITIVE_KEY_RE.search(key):
        return f"{key.strip()}=<disembunyikan: {len(value.strip())} karakter>"
    return text
def _last_statement_line(raw: str) -> str:
    """Baris terakhir yang tidak kosong dari teks statement mentah."""
    for line in reversed((raw or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _locate_line(lines: list[str], statement: str) -> int:
    """Nomor baris (1-based) sebuah statement; 0 bila tidak ditemukan."""
    if not statement:
        return 0
    for index, line in enumerate(lines, start=1):
        if line.strip() == statement:
            return index
    return 0


def find_invalid_env_lines(env_path: Optional[str] = None) -> list:
    """
    Baris .env yang DIABAIKAN python-dotenv, beserta nomor barisnya.

    Nomor baris dicari ulang dengan mencocokkan ISI baris di berkas, bukan
    memakai nomor dari python-dotenv yang bisa meleset satu baris.

    Return: daftar (nomor_baris, isi_baris). Nomor 0 = baris tidak ditemukan.
    """
    path = _env_path_or_default(env_path)
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        problems: list = []
        with open(path, encoding="utf-8") as handle:
            for binding in parse_stream(handle):
                if not binding.error:
                    continue
                statement = _last_statement_line(binding.original.string)
                problems.append((_locate_line(lines, statement), statement))
        return problems
    except Exception as exc:  # pragma: no cover - jalur IO
        logger.debug("Gagal memeriksa baris .env %s: %s", path, exc)
        return []


def env_value_overrides(env_path: Optional[str] = None) -> list:
    """
    Variabel yang nilainya BERBEDA antara .env dan environment proses.

    Variabel yang hanya ada di .env (tidak ada di environment) BUKAN override:
    `load_dotenv()` sudah memasukkannya ke environment, jadi nilainya berlaku.

    Return: daftar dict {key, file_value, process_value}.
    """
    path = _env_path_or_default(env_path)
    if not path:
        return []
    try:
        file_values = dotenv_values(path)
    except Exception as exc:  # pragma: no cover - jalur IO
        logger.debug("Gagal membaca .env %s: %s", path, exc)
        return []

    overrides: list = []
    for key, file_value in file_values.items():
        process_value = os.environ.get(key)
        if process_value is None:
            continue
        if (file_value or "").strip() != process_value.strip():
            overrides.append(
                {
                    "key": key,
                    "file_value": file_value,
                    "process_value": process_value,
                }
            )
    return overrides


def warn_env_overrides(env_path: Optional[str] = None) -> None:
    """
    Log peringatan untuk baris .env rusak & nilai .env yang dikalahkan environment.

    Dipanggil sekali saat bot start. Tidak mengubah environment apa pun, jadi
    aman dipanggil di produksi.
    """
    path = _env_path_or_default(env_path)
    if not path:
        logger.info("Berkas .env tidak ditemukan — memakai environment proses apa adanya.")
        return

    for number, statement in find_invalid_env_lines(path):
        where = f"baris {number}" if number else "nomor baris tidak terdeteksi"
        logger.warning(
            "Baris .env TIDAK TERBACA (%s) dan diabaikan: %r — beri tanda '#' bila "
            "itu hanya catatan, atau tulis sebagai NAMA=nilai.",
            where, mask_env_statement(statement),
        )

    for item in env_value_overrides(path):
        logger.warning(
            "%s: nilai dari environment proses DIPAKAI, .env diabaikan "
            "(.env=%s, proses=%s). Perbarui cache pm2 dengan "
            "`pm2 restart <app> --update-env` bila ingin memakai nilai .env.",
            item["key"],
            mask_env_value(item["key"], item["file_value"]),
            mask_env_value(item["key"], item["process_value"]),
        )