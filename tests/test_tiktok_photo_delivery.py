"""
test_tiktok_photo_delivery.py - Foto TikTok HARUS bisa diunduh user.

Telegram memakai WebP sebagai format STIKER resmi. Foto WebP yang diunggah
tanpa paksa-an document tampil sebagai stiker dan user tidak bisa
mengunduhnya sebagai berkas. Dua lapis pertahanan diuji di sini:

  1. Normalisasi format (bot/tiktok_media.py) - deteksi lewat MAGIC BYTES,
     bukan ekstensi URL, lalu konversi WebP/HEIC ke JPEG.
  2. Force-document (bot/telegram_sender.py) - foto dikirim sebagai document.
"""
import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.config import Config
from bot.telegram_sender import TelegramSender
from bot.tiktok_media import (
    download_images,
    normalize_image,
    sniff_image_format,
    telegram_safe_suffix,
)


def _make_image(path: Path, color: str = "red") -> Path:
    """Gambar sintetis kecil (agar ffmpeg punya berkas nyata)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=320x568:d=1",
            "-frames:v", "1", str(path),
        ],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return path


def _fake_msg(msg_id: int):
    class _Message:
        def __init__(self, value: int) -> None:
            self.id = value

    return _Message(msg_id)


class _FakeResponse:
    """Respons httpx minimal untuk download_images."""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class TestImageFormatSniffing(unittest.TestCase):
    """Deteksi format harus membaca isi berkas, bukan nama berkas."""

    def test_detects_known_formats(self):
        self.assertEqual(sniff_image_format(b"\xff\xd8\xff\xe0\x00\x10JFIF"), "jpeg")
        self.assertEqual(sniff_image_format(b"\x89PNG\r\n\x1a\n...."), "png")
        self.assertEqual(sniff_image_format(b"RIFF\x24\x00\x00\x00WEBPVP8 "), "webp")
        self.assertEqual(
            sniff_image_format(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"), "heic"
        )

    def test_unknown_payloads_return_empty(self):
        self.assertEqual(sniff_image_format(b""), "")
        self.assertEqual(sniff_image_format(b"<html>404</html>"), "")

    def test_webp_with_jpg_url_maps_to_jpeg(self):
        """Kasus inti: URL .jpg tapi isinya WebP -> dipetakan ke .jpg."""
        self.assertEqual(
            telegram_safe_suffix(b"RIFF\x24\x00\x00\x00WEBPVP8 ", ".jpg"), ".jpg"
        )

    def test_jpeg_and_png_keep_their_extension(self):
        self.assertEqual(telegram_safe_suffix(b"\xff\xd8\xff\xe0\x00\x10", ".jpg"), ".jpg")
        self.assertEqual(telegram_safe_suffix(b"\x89PNG\r\n\x1a\n....", ".jpg"), ".png")

    def test_unknown_payload_falls_back_to_url_guess(self):
        """Payload tak terbaca tidak dibuang; tebakan URL jadi cadangan."""
        self.assertEqual(telegram_safe_suffix(b"?????", ".png"), ".png")


class TestImageNormalization(unittest.TestCase):
    """Berkas non-JPEG/PNG harus jadi JPEG sebelum masuk Telegram."""

    def test_webp_is_converted_to_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            webp = _make_image(Path(directory) / "photo.webp", color="green")
            result = asyncio.run(normalize_image(webp))

            self.assertEqual(result.suffix, ".jpg")
            self.assertEqual(sniff_image_format(result.read_bytes()), "jpeg")
            self.assertFalse(
                webp.exists(), "berkas webp asli harus dihapus setelah konversi"
            )

    def test_jpeg_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            jpeg = _make_image(Path(directory) / "photo.jpg", color="red")
            before = jpeg.read_bytes()

            result = asyncio.run(normalize_image(jpeg))

            self.assertEqual(result, jpeg)
            self.assertEqual(result.read_bytes(), before, "JPEG tidak boleh di-encode ulang")

    def test_webp_bytes_in_jpg_named_file_are_converted(self):
        """
        Regression: WebP bernama `.jpg` = target output == target input.

        ffmpeg menolak menulis in-place ("cannot edit existing files in
        place"), jadi konversi harus lewat nama sementara. Kasus persis ini
        terjadi di produksi: CDN TikTok mengirim byte WebP sementara
        `telegram_safe_suffix` sudah menamai berkasnya `.jpg`.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = _make_image(Path(directory) / "02.jpg", color="red")
            webp = _make_image(Path(directory) / "seed.webp", color="red")
            # Same filename (.jpg), different content (WebP).
            target.write_bytes(webp.read_bytes())

            result = asyncio.run(normalize_image(target))

            self.assertEqual(result.name, "02.jpg")
            self.assertEqual(sniff_image_format(result.read_bytes()), "jpeg")

    def test_missing_ffmpeg_keeps_original_file_intact(self):
        """
        Regression: kegagalan konversi TIDAK BOLEH menghapus foto.

        Percobaan pertama menghapus `path` saat ffmpeg gagal, mengubah masalah
        format yang sepele menjadi media hilang permanen.
        """
        with tempfile.TemporaryDirectory() as directory:
            target = _make_image(Path(directory) / "03.jpg", color="blue")
            webp = _make_image(Path(directory) / "seed.webp", color="blue")
            target.write_bytes(webp.read_bytes())
            before = target.read_bytes()

            async def _no_ffmpeg(*_args, **_kwargs):
                raise FileNotFoundError("ffmpeg")

            with mock.patch(
                "bot.tiktok_media.asyncio.create_subprocess_exec", _no_ffmpeg
            ):
                result = asyncio.run(normalize_image(target))

            self.assertTrue(result.exists(), "foto asli tidak boleh hilang")
            self.assertEqual(result.read_bytes(), before)
            self.assertEqual(sniff_image_format(before), "webp")

    def test_no_temp_file_left_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            target = _make_image(Path(directory) / "04.jpg", color="green")
            webp = _make_image(Path(directory) / "seed.webp", color="green")
            target.write_bytes(webp.read_bytes())

            asyncio.run(normalize_image(target))

            leftovers = list(Path(directory).glob("*.conv.jpg"))
            self.assertEqual(leftovers, [], "file sementara tidak boleh tertinggal")

    def test_download_normalizes_payload_regardless_of_url_extension(self):
        """URL .jpg dengan payload WebP harus berakhir sebagai JPEG asli."""
        with tempfile.TemporaryDirectory() as directory:
            webp = _make_image(Path(directory) / "seed.webp", color="blue")
            jpeg = Path(directory) / "seed.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(webp), "-frames:v", "1", str(jpeg)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

            # Server mengirim JPEG meski URL berakhiran .jpg; analoginya payload
            # WebP pada URL .jpg harus dinormalisasi, bukan diteruskan mentah.
            payload = jpeg.read_bytes()
            out_dir = Path(directory) / "out"

            async def fake_get(_url, **_kwargs):
                return _FakeResponse(payload)

            with mock.patch("bot.tiktok_media.httpx.AsyncClient") as client_cls:
                client_cls.return_value.__aenter__.return_value.get = fake_get
                files = asyncio.run(
                    download_images(["https://cdn.invalid/photo-01.jpg"], out_dir)
                )

            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].suffix, ".jpg")
            self.assertEqual(sniff_image_format(files[0].read_bytes()), "jpeg")


class TestPhotosSentAsDocument(unittest.TestCase):
    """force_document=True menutup jalur "tampil jadi stiker"."""

    def _sender(self) -> TelegramSender:
        sender = TelegramSender.__new__(TelegramSender)
        sender._connected = True
        sender._client = mock.AsyncMock()
        return sender

    def test_single_photo_sent_as_document(self):
        with tempfile.TemporaryDirectory() as directory:
            only = Path(directory) / "1.jpg"
            only.write_bytes(b"x")
            sender = self._sender()
            sender._client.send_file = mock.AsyncMock(return_value=_fake_msg(1))

            ids = asyncio.run(sender._send_media_album([only], "cap", -100123))

            self.assertEqual(ids, [1])
            self.assertTrue(
                sender._client.send_file.await_args.kwargs.get("force_document"),
                "foto tunggal harus dipaksa jadi document agar bisa diunduh",
            )

    def test_album_sent_as_document(self):
        with tempfile.TemporaryDirectory() as directory:
            files = []
            for index in (1, 2, 3):
                path = Path(directory) / f"{index}.jpg"
                path.write_bytes(b"x")
                files.append(path)
            sender = self._sender()
            sender._client.send_file = mock.AsyncMock(
                return_value=[_fake_msg(1), _fake_msg(2), _fake_msg(3)]
            )

            ids = asyncio.run(sender._send_media_album(files, "cap", -100123))

            self.assertEqual(ids, [1, 2, 3])
            self.assertTrue(
                sender._client.send_file.await_args.kwargs.get("force_document"),
                "album foto juga harus dipaksa jadi document",
            )

    def test_force_document_honours_config_switch(self):
        """Menonaktifkan flag mengembalikan perilaku inline (escape hatch)."""
        old = Config.TIKTOK_PHOTOS_AS_DOCUMENT
        Config.TIKTOK_PHOTOS_AS_DOCUMENT = False
        try:
            with tempfile.TemporaryDirectory() as directory:
                only = Path(directory) / "1.jpg"
                only.write_bytes(b"x")
                sender = self._sender()
                sender._client.send_file = mock.AsyncMock(return_value=_fake_msg(1))

                asyncio.run(sender._send_media_album([only], "cap", -100123))

                self.assertFalse(
                    sender._client.send_file.await_args.kwargs.get("force_document")
                )
        finally:
            Config.TIKTOK_PHOTOS_AS_DOCUMENT = old


if __name__ == "__main__":
    unittest.main()