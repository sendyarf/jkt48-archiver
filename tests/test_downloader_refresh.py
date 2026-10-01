"""
test_downloader_refresh.py - Retry download memakai URL HLS segar dari url_refresher.

Akar masalah yang dijaga (insiden 22 Sep 2026, jkt48_nayla): URL HLS Showroom
yang sesi broadcast-nya sudah mati DIGANTUNG CDN (showroom-txlive.com) tanpa
respons — bukan 404 — sehingga menunggu URL itu aktif kembali tidak akan
pernah berhasil. Bot membuang ±1 jam me-retry URL mati yang sama dan video
akhirnya ter-upload terpotong. `download_stream` kini meminta URL segar lewat
`url_refresher` di awal SETIAP retry.
"""
import asyncio
import tempfile
import unittest
from unittest.mock import patch

from bot import downloader
from bot.config import Config

OLD_URL = "https://cdn.showroom.example/lama.m3u8"
FRESH_URL = "https://cdn.showroom.example/segar.m3u8"


class _FakeProcess:
    """Process palsu: langsung keluar dengan kode 1 tanpa menghasilkan file."""

    def __init__(self):
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_eof()

    async def wait(self):
        self.returncode = 1
        return 1

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class UrlRefresherTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = Config.DOWNLOAD_DIR
        Config.DOWNLOAD_DIR = self._tmp.name
        # URL yang dicek downloader, urut per percobaan.
        self.checked_urls = []
        self.refresher_calls = {"count": 0}

    def tearDown(self):
        Config.DOWNLOAD_DIR = self._old_dir
        self._tmp.cleanup()

    def _run_download(self, refresher):
        async def fake_wait(hls_url, auth_token=None, **kwargs):
            self.checked_urls.append(hls_url)
            return True

        async def fake_exec(*cmd, **kwargs):
            return _FakeProcess()

        async def run():
            with (
                patch("bot.downloader.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"),
                patch("bot.downloader._wait_for_hls_url", side_effect=fake_wait),
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            ):
                with self.assertRaises(downloader.DownloadError):
                    await downloader.download_stream(
                        hls_url=OLD_URL,
                        member_username="jkt48_nayla",
                        live_id="sr_JKT48_Nayla_1",
                        max_empty_retries=2,
                        empty_retry_delay=0.01,
                        url_refresher=refresher,
                    )

        asyncio.run(run())

    def test_retry_memakai_url_segar_dari_refresher(self):
        async def refresher():
            self.refresher_calls["count"] += 1
            return FRESH_URL

        self._run_download(refresher)
        self.assertEqual(self.checked_urls, [OLD_URL, FRESH_URL])
        # Refresher dipanggil mulai percobaan ke-2 (percobaan pertama memakai
        # URL yang baru saja diambil pemanggil).
        self.assertEqual(self.refresher_calls["count"], 1)

    def test_refresher_tanpa_url_baru_mempertahankan_url_lama(self):
        async def refresher():
            self.refresher_calls["count"] += 1
            return None

        self._run_download(refresher)
        self.assertEqual(self.checked_urls, [OLD_URL, OLD_URL])
        self.assertEqual(self.refresher_calls["count"], 1)

    def test_refresher_error_tidak_menggagalkan_retry(self):
        async def refresher():
            raise RuntimeError("API Showroom hiccup")

        self._run_download(refresher)
        self.assertEqual(self.checked_urls, [OLD_URL, OLD_URL])

    def test_tanpa_refresher_perilaku_lama(self):
        self._run_download(None)
        self.assertEqual(self.checked_urls, [OLD_URL, OLD_URL])


class ShouldContinueTestCase(unittest.TestCase):
    """Live yang sudah berakhir tidak boleh di-retry 30 kali (Nachia, 1 Okt 2026).

    Gejalanya: 30 percobaan x (15 probe URL + satu ffmpeg) = +/- 1 jam bekerja
    untuk URL Showroom yang sudah mati, padahal room-nya sudah offline lebih
    dari sejam. `should_continue` memberi downloader tahu kapan harus menyerah.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = Config.DOWNLOAD_DIR
        Config.DOWNLOAD_DIR = self._tmp.name
        self.checked_urls: list[str] = []
        self.ffmpeg_calls = {"count": 0}

    def tearDown(self):
        Config.DOWNLOAD_DIR = self._old_dir
        self._tmp.cleanup()

    def _run(self, verdict, max_empty_retries: int = 30):
        """Jalankan download_stream dengan `should_continue` mengembalikan `verdict`."""

        async def fake_wait(hls_url, auth_token=None, **kwargs):
            self.checked_urls.append(hls_url)
            return True

        async def fake_exec(*cmd, **kwargs):
            self.ffmpeg_calls["count"] += 1
            return _FakeProcess()

        async def should_continue():
            return verdict

        async def run():
            with (
                patch("bot.downloader.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"),
                patch("bot.downloader._wait_for_hls_url", side_effect=fake_wait),
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            ):
                await downloader.download_stream(
                    hls_url=OLD_URL,
                    member_username="jkt48_nachia",
                    live_id="sr_JKT48_Nachia_1790838576",
                    max_empty_retries=max_empty_retries,
                    empty_retry_delay=0.01,
                    should_continue=should_continue,
                )

        return asyncio.run(run())

    def test_stop_langsung_bila_live_sudah_selesai(self):
        """Harus berhenti di percobaan pertama - tanpa probe URL, tanpa ffmpeg."""
        with self.assertRaises(downloader.LiveEndedError):
            self._run(False)
        self.assertEqual(
            self.checked_urls, [],
            "tidak boleh membuang 105 detik probe URL untuk live yang sudah selesai",
        )
        self.assertEqual(self.ffmpeg_calls["count"], 0)

    def test_live_masih_jalan_melanjut_seperti_biasa(self):
        with self.assertRaises(downloader.DownloadError):
            self._run(True, max_empty_retries=2)
        self.assertEqual(self.checked_urls, [OLD_URL, OLD_URL])
        self.assertEqual(self.ffmpeg_calls["count"], 2)

    def test_status_tidak_diketahui_melanjut(self):
        """None = API room tidak bisa dihubungi; rekaman jangan dibunuh."""
        with self.assertRaises(downloader.DownloadError):
            self._run(None, max_empty_retries=2)
        self.assertEqual(self.ffmpeg_calls["count"], 2)

    def test_liveended_adalah_subclass_downloaderror(self):
        """Pemanggil lama yang menangkap DownloadError tetap bekerja."""
        self.assertTrue(issubclass(downloader.LiveEndedError, downloader.DownloadError))


if __name__ == "__main__":
    unittest.main()
