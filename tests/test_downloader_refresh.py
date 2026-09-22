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
    """yt-dlp palsu: langsung keluar dengan kode 1 tanpa menghasilkan file."""

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


if __name__ == "__main__":
    unittest.main()
