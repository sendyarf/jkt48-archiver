"""
test_youtube_channel_quota.py - Unit test jendela reset counter kuota YouTube
dan log rotasi channel.

Dua perilaku yang dijaga di sini:

1. `uploads_today` reset pada tengah malam WAKTU PASIFIK, bukan UTC, karena
   kuota harian Google/YouTube memang reset pada tengah malam PT. Dengan reset
   UTC, counter berganti 7-8 jam lebih awal: pada 00.00-07.00 UTC semua channel
   terlihat "segar" padahal kuota YouTube kemarin belum pulih.
2. Log rotasi mencetak `reason` HTTP yang SEBENARNYA (mis. `uploadLimitExceeded`),
   bukan `True` hardcoded — supaya operator bisa membedakan kuota proyek, batas
   upload channel, dan rate limit sesaat dari log pm2.
"""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httplib2
from googleapiclient.errors import HttpError

from bot import database
from bot.config import Config
from bot.youtube_uploader import (
    YouTubeChannelPool,
    YouTubeQuotaExceeded,
    _describe_quota_reasons,
)


def _http_error(reason: str, message: str = "quota") -> HttpError:
    """403 yang bentuknya sama dengan respons asli YouTube Data API v3."""
    body = json.dumps(
        {
            "error": {
                "code": 403,
                "message": message,
                "errors": [{"reason": reason, "domain": "youtube.quota"}],
            }
        }
    ).encode()
    return HttpError(httplib2.Response({"status": "403", "reason": "Forbidden"}), body)


class ChannelCounterResetTestCase(unittest.TestCase):
    """`uploads_today` harus mengikuti hari kuota (tengah malam PT), bukan UTC."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = Config.DB_PATH
        Config.DB_PATH = str(Path(self._tmp.name) / "quota.db")
        database.init_db()
        database.sync_youtube_channels(
            [{"channel_label": "Channel uji", "token_file": "t.json", "secret_file": "s.json"}]
        )

    def tearDown(self):
        Config.DB_PATH = self._old_db
        self._tmp.cleanup()

    @staticmethod
    def _row() -> dict:
        conn = sqlite3.connect(Config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return dict(conn.execute("SELECT * FROM youtube_channels").fetchone())
        finally:
            conn.close()

    @staticmethod
    def _seed(last_reset_at: str, uploads_today: int = 5) -> None:
        conn = sqlite3.connect(Config.DB_PATH)
        try:
            conn.execute(
                "UPDATE youtube_channels SET uploads_today = ?, last_reset_at = ?",
                (uploads_today, last_reset_at),
            )
            conn.commit()
        finally:
            conn.close()

    def test_tidak_reset_di_tengah_malam_utc(self):
        """
        02.00 UTC masih 19.00 HARI SEBELUMNYA di Pasifik. Versi lama (UTC) akan
        mereset di sini; versi baru tidak, karena hari kuota belum berganti.
        """
        self._seed("2026-10-07 23:30:00", uploads_today=5)
        database.reset_channel_counters_if_new_day(datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc))
        self.assertEqual(self._row()["uploads_today"], 5, "counter tidak boleh reset sebelum tengah malam PT")

    def test_reset_saat_tengah_malam_pasifik_walaupun_tanggal_utc_sama(self):
        """08.00 UTC = 01.00 PDT → hari kuota berganti walau tanggal UTC masih sama."""
        self._seed("2026-10-08 05:30:00", uploads_today=5)
        database.reset_channel_counters_if_new_day(datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(self._row()["uploads_today"], 0, "counter harus reset tepat di tengah malam PT")

    def test_reset_memakai_offset_pst_di_musim_dingin(self):
        """Januari = PST (UTC-8), jadi tengah malam PT jatuh pukul 08.00 UTC."""
        self._seed("2026-01-08 06:00:00", uploads_today=5)
        database.reset_channel_counters_if_new_day(datetime(2026, 1, 8, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(self._row()["uploads_today"], 5, "07.00 UTC masih 23.00 PST kemarin")
        database.reset_channel_counters_if_new_day(datetime(2026, 1, 8, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(self._row()["uploads_today"], 0)

    def test_reset_menulis_last_reset_at_dalam_utc(self):
        """`last_reset_at` wajib UTC (aturan proyek); menyimpan waktu Pasifik
        akan meleset 7-8 jam dan membuat bacaan admin salah."""
        self._seed("2026-10-08 05:30:00")
        database.reset_channel_counters_if_new_day(datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc))
        stored = datetime.strptime(self._row()["last_reset_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        selisih = abs((stored - datetime.now(timezone.utc)).total_seconds())
        self.assertLess(selisih, 120, "last_reset_at harus ditulis dalam UTC, bukan waktu Pasifik/server")

    def test_memanggil_reset_tanpa_argumen_tetap_jalan(self):
        """Jalur produksi (`now=None`) tidak boleh melempar error."""
        database.reset_channel_counters_if_new_day()
        self.assertIn("uploads_today", self._row())


class QuotaRotationLogTestCase(unittest.TestCase):
    """Log rotasi harus membawa reason asli agar bisa didiagnosis dari pm2."""

    @staticmethod
    def _pool() -> YouTubeChannelPool:
        pool = YouTubeChannelPool.__new__(YouTubeChannelPool)
        pool._services = {}
        pool._channel_auth_disabled_until = {}
        pool._channel_auth_failures = {}
        return pool

    @staticmethod
    def _channel(uploads_today: int = 10) -> dict:
        return {
            "id": 1,
            "channel_label": "Channel librani098",
            "token_file": "t.json",
            "secret_file": "s.json",
            "uploads_today": uploads_today,
        }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.video = Path(self._tmp.name) / "uji.mp4"
        self.video.write_bytes(b"\x00" * 64)

    def tearDown(self):
        self._tmp.cleanup()

    def _service_dengan_error(self, reason: str) -> MagicMock:
        service = MagicMock()
        service.videos.return_value.insert.return_value.next_chunk.side_effect = _http_error(reason)
        return service

    def test_log_mencetak_reason_asli_bukan_true(self):
        pool = self._pool()
        service = self._service_dengan_error("uploadLimitExceeded")
        with (
            patch("bot.youtube_uploader.database.get_youtube_channels", return_value=[self._channel()]),
            patch("bot.youtube_uploader.MediaFileUpload", return_value=MagicMock()),
            patch.object(pool, "_get_service_for_channel", return_value=service),
            self.assertLogs("bot.youtube_uploader", level="WARNING") as captured,
        ):
            with self.assertRaises(YouTubeQuotaExceeded):
                pool.upload_video(self.video, title="Uji kuota")

        log = "\n".join(captured.output)
        self.assertIn("Quota exceeded for channel 'Channel librani098'", log)
        self.assertIn("reasons=uploadLimitExceeded", log)
        self.assertIn("batas upload harian CHANNEL", log)
        self.assertNotIn("quota_or_limit_in_response", log, "format lama yang selalu 'True' harus hilang")

    def test_rate_limit_saat_bisa_dibedakan_dari_kuota_harian(self):
        pool = self._pool()
        service = self._service_dengan_error("userRateLimitExceeded")
        with (
            patch("bot.youtube_uploader.database.get_youtube_channels", return_value=[self._channel()]),
            patch("bot.youtube_uploader.MediaFileUpload", return_value=MagicMock()),
            patch.object(pool, "_get_service_for_channel", return_value=service),
            self.assertLogs("bot.youtube_uploader", level="WARNING") as captured,
        ):
            with self.assertRaises(YouTubeQuotaExceeded):
                pool.upload_video(self.video, title="Uji rate limit")

        log = "\n".join(captured.output)
        self.assertIn("reasons=userRateLimitExceeded", log)
        self.assertIn("rate limit sesaat", log)

    def test_error_bukan_kuota_tidak_dilaporkan_sebagai_kuota(self):
        """
        Kasus 'Channel greezeal' (YouTube Data API belum di-enable di project):
        403 accessNotConfigured BUKAN kuota, jadi tidak boleh memutar rotasi
        dan tidak boleh tercatat sebagai 'Quota exceeded'.
        """
        pool = self._pool()
        service = self._service_dengan_error("accessNotConfigured")
        with (
            patch("bot.youtube_uploader.database.get_youtube_channels", return_value=[self._channel()]),
            patch("bot.youtube_uploader.MediaFileUpload", return_value=MagicMock()),
            patch.object(pool, "_get_service_for_channel", return_value=service),
            self.assertLogs("bot.youtube_uploader", level="WARNING") as captured,
        ):
            hasil = pool.upload_video(self.video, title="Uji akses")

        self.assertEqual(hasil, (None, None))
        log = "\n".join(captured.output)
        self.assertIn("HTTP error uploading to channel", log)
        self.assertNotIn("Quota exceeded", log)

    def test_describe_reasons_kosong_tidak_menghasilkan_true(self):
        self.assertEqual(_describe_quota_reasons(set()), "tanpa reason (baca teks error asli)")
        self.assertIn("quotaExceeded", _describe_quota_reasons({"quotaExceeded"}))


if __name__ == "__main__":
    unittest.main()