"""
test_disk_space.py — Guard ruang disk sebelum mulai recording.

Latar: ffmpeg bisa gagal di tengah jalan bila disk penuh dan
meninggalkan sesi macet (insiden yang tercatat di SHOWROOM-PLAN §6.1).
`has_enough_disk_space()` dipanggil di loop utama (IDN & Showroom) sebelum
spawn task rekaman BARU; rekaman yang sudah berjalan tidak disentuh.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.config import Config
from bot.downloader import has_enough_disk_space


class HasEnoughDiskSpaceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = Config.DOWNLOAD_DIR
        self._old_min = Config.MIN_FREE_DISK_MB
        Config.DOWNLOAD_DIR = self._tmp.name

    def tearDown(self):
        Config.DOWNLOAD_DIR = self._old_dir
        Config.MIN_FREE_DISK_MB = self._old_min
        self._tmp.cleanup()

    def test_cukup_space_mengizinkan_rekaman(self):
        Config.MIN_FREE_DISK_MB = 1
        # disk_usage nyata directory temp: free biasanya jutaan MB.
        self.assertTrue(has_enough_disk_space())

    def test_space_kurang_menolak_rekaman(self):
        Config.MIN_FREE_DISK_MB = 10**9  # 1 PB — mustahil cukup di mesin uji
        self.assertFalse(has_enough_disk_space())

    def test_parameter_min_mb_mengalahkan_config(self):
        Config.MIN_FREE_DISK_MB = 10**9
        # Parameter eksplisit di atas ambang config → tetap diizinkan.
        self.assertTrue(has_enough_disk_space(min_mb=1))

    def test_gagal_cek_space_tidak_memblokir_rekaman(self):
        Config.MIN_FREE_DISK_MB = 10**9
        with patch("bot.downloader.shutil.disk_usage", side_effect=OSError("FS aneh")):
            # Fail-open: tidak bisa cek → jangan blokir recording.
            self.assertTrue(has_enough_disk_space())

    def test_membuat_download_dir_bila_belum_ada(self):
        missing = Path(self._tmp.name) / "belum" / "ada"
        Config.DOWNLOAD_DIR = str(missing)
        Config.MIN_FREE_DISK_MB = 1
        self.assertTrue(has_enough_disk_space())
        self.assertTrue(missing.is_dir())


class MainLoopGuardTestCase(unittest.TestCase):
    """Loop utama memanggil guard SEBELUM spawn task rekaman baru."""

    def test_import_has_enough_disk_space_dipakai_di_main(self):
        import inspect

        from bot import main as main_mod

        source = inspect.getsource(main_mod)
        self.assertIn("has_enough_disk_space(", source)
        # Dipakai di jalur IDN (active_now) dan Showroom.
        self.assertGreaterEqual(source.count("has_enough_disk_space("), 2)


if __name__ == "__main__":
    unittest.main()
