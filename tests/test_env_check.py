"""
test_env_check.py - Tes diagnostik .env (baris rusak + nilai yang dikalahkan
environment proses).

Latar (insiden nyata 19 Sep 2026):
  * `.env` memuat satu kalimat catatan tanpa tanda '#' → python-dotenv
    mengabaikannya diam-diam dan hanya menulis satu baris peringatan yang
    nomor barisnya bisa meleset satu.
  * `SHOWROOM_CHECK_INTERVAL_SECONDS` efektif 10 padahal `.env` sudah 30, karena
    cache environment pm2 menang atas `.env` (`load_dotenv` tanpa override=True).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.config import (
    env_value_overrides,
    find_invalid_env_lines,
    mask_env_statement,
    mask_env_value,
    warn_env_overrides,
)


class EnvCheckTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = Path(self._tmp.name) / ".env"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, content: str) -> str:
        self.env.write_text(content, encoding="utf-8")
        return str(self.env)


class TestInvalidEnvLines(EnvCheckTestCase):
    def test_mendeteksi_kalimat_catatan_tanpa_pagar(self):
        path = self._write(
            "# komentar\n"
            "VALID_SATU=1\n"
            "\n"
            "Offset zona waktu penonton terhadap UTC: 7 = WIB, 8 = WITA.\n"
            "VALID_DUA=2\n"
        )
        problems = find_invalid_env_lines(path)
        self.assertEqual(len(problems), 1, f"harus tepat 1 baris rusak: {problems}")
        number, statement = problems[0]
        # Nomor baris dicari dari ISI berkas, jadi tepat 4 (bukan 3 seperti
        # nomor versi python-dotenv).
        self.assertEqual(number, 4)
        self.assertIn("Offset zona waktu", statement)

    def test_env_bersih_tidak_menghasilkan_masalah(self):
        path = self._write("# komentar\nA=1\n\nB='dua tiga'\nexport C=3\n")
        self.assertEqual(find_invalid_env_lines(path), [])

    def test_berkas_tidak_ada_dianggap_bersih(self):
        self.assertEqual(find_invalid_env_lines(str(self.env)), [])


class TestEnvValueOverrides(EnvCheckTestCase):
    def test_mendeteksi_nilai_yang_dikalahkan_environment(self):
        path = self._write(
            "SHOWROOM_CHECK_INTERVAL_SECONDS=30\nHLS_CHECK_INTERVAL_SECONDS=5\n"
        )
        with patch.dict(os.environ, {"SHOWROOM_CHECK_INTERVAL_SECONDS": "10"}):
            overrides = env_value_overrides(path)
        self.assertEqual([o["key"] for o in overrides], ["SHOWROOM_CHECK_INTERVAL_SECONDS"])
        self.assertEqual(overrides[0]["file_value"], "30")
        self.assertEqual(overrides[0]["process_value"], "10")

    def test_nilai_sama_dianggap_cocok(self):
        path = self._write("SAMA=7\n")
        with patch.dict(os.environ, {"SAMA": "7"}):
            self.assertEqual(env_value_overrides(path), [])

    def test_kunci_hanya_di_env_bukan_override(self):
        # Variabel yang hanya ada di .env memang berlaku (dimasukkan load_dotenv),
        # jadi tidak boleh dilaporkan sebagai override.
        path = self._write("HANYA_DI_ENV=7\n")
        os.environ.pop("HANYA_DI_ENV", None)
        self.assertEqual(env_value_overrides(path), [])


class TestMasking(unittest.TestCase):
    def test_nilai_sensitif_disamarkan(self):
        masked = mask_env_value("TELEGRAM_BOT_TOKEN", "123456:AArahasia")
        self.assertIn("disembunyikan", masked)
        self.assertNotIn("AArahasia", masked)

    def test_nilai_biasa_ditampilkan(self):
        self.assertEqual(mask_env_value("MERGE_WINDOW_SECONDS", "3600"), "3600")

    def test_nilai_panjang_dipotong(self):
        self.assertTrue(mask_env_value("CATATAN", "x" * 100).endswith("..."))

    def test_statement_rusak_yang_memuat_token_disamarkan(self):
        masked = mask_env_statement("TELEGRAM_BOT_TOKEN = 123456:AArahasia")
        self.assertNotIn("AArahasia", masked)
        self.assertIn("TELEGRAM_BOT_TOKEN", masked)


class TestWarnEnvOverrides(EnvCheckTestCase):
    def test_mencatat_dua_jenis_peringatan_tanpa_melempar(self):
        path = self._write(
            "VALID=1\n"
            "BUKAN BARIS VALID\n"
            "IDN_LOOKUP_TIMEOUT_SECONDS=8\n"
        )
        with patch.dict(os.environ, {"IDN_LOOKUP_TIMEOUT_SECONDS": "99"}):
            with self.assertLogs("bot.config", level="WARNING") as captured:
                warn_env_overrides(path)
        joined = "\n".join(captured.output)
        self.assertIn("TIDAK TERBACA", joined)
        self.assertIn("IDN_LOOKUP_TIMEOUT_SECONDS", joined)


if __name__ == "__main__":
    unittest.main()
