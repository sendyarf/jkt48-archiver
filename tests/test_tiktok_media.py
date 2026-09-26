"""
test_tiktok_media.py - Unit test pengolahan media TikTok.

Menguji pembagian foto per part album Telegram (batas 10), unduhan foto
(termasuk path lokal), pembuatan slide show lewat ffmpeg nyata, serta
penyiapan media satu postingan foto/video.
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.config import Config
from bot.tiktok_client import TikTokItem
from bot.tiktok_media import (
    MediaError,
    MediaPermanentError,
    _ffconcat_escape,
    _ytdlp_config_args,
    build_slideshow,
    cleanup_media,
    copy_fixture_video,
    download_images,
    download_video,
    is_permanent_media_error,
    prepare_post_media,
    split_image_paths,
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


def _make_video(path: Path, seconds: int = 3) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency=800:duration={seconds}",
            "-c:v", "libx264", "-c:a", "aac", str(path),
        ],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return path


class TestSplitImagePaths(unittest.TestCase):
    def test_splits_twelve_photos_into_10_and_2(self):
        paths = [f"foto-{i:02d}.jpg" for i in range(1, 13)]
        parts = split_image_paths(paths, per_part=10)
        self.assertEqual([len(p) for p in parts], [10, 2])
        self.assertEqual(parts[1][0], Path("foto-11.jpg"))
        self.assertEqual(parts[1][1], Path("foto-12.jpg"))

    def test_exactly_ten_stays_single_part(self):
        parts = split_image_paths([f"{i}.jpg" for i in range(10)], per_part=10)
        self.assertEqual(len(parts), 1)

    def test_more_than_ten_per_part_is_capped_to_telegram_limit(self):
        """Batas album Telegram = 10; nilai konfigurasi lebih besar dipotong."""
        parts = split_image_paths([f"{i}.jpg" for i in range(25)], per_part=20)
        self.assertEqual([len(p) for p in parts], [10, 10, 5])

    def test_empty_list_gives_no_parts(self):
        self.assertEqual(split_image_paths([]), [])

    def test_default_comes_from_config(self):
        original = Config.TIKTOK_PHOTOS_PER_PART
        try:
            Config.TIKTOK_PHOTOS_PER_PART = 4
            parts = split_image_paths([f"{i}.jpg" for i in range(9)])
            self.assertEqual([len(p) for p in parts], [4, 4, 1])
        finally:
            Config.TIKTOK_PHOTOS_PER_PART = original


class TestDownloadImages(unittest.TestCase):
    def test_copies_local_paths_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sources = [_make_image(base / f"src-{i}.jpg", color=("red", "green", "blue")[i])
                       for i in range(3)]
            out = base / "hasil"
            files = asyncio.run(download_images([str(p) for p in sources], out))
            self.assertEqual(len(files), 3)
            self.assertEqual([f.name for f in files], ["01.jpg", "02.jpg", "03.jpg"])
            for path in files:
                self.assertGreater(path.stat().st_size, 0)

    def test_bad_entry_is_skipped_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            good = _make_image(base / "src.jpg")
            files = asyncio.run(
                download_images([str(good), str(base / "tidak-ada.jpg")], base / "hasil")
            )
            self.assertEqual(len(files), 1)

    def test_empty_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(asyncio.run(download_images([], tmp)), [])


class TestSlideshow(unittest.TestCase):
    """Slide show dibuat dengan ffmpeg nyata (bukan mock)."""

    def test_slideshow_from_two_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            images = [
                _make_image(base / "a.jpg", "red"),
                _make_image(base / "b.jpg", "blue"),
            ]
            out = asyncio.run(
                build_slideshow(images, base / "slide.mp4", seconds_per_photo=1)
            )
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=width,height:format=duration", "-of", "json", str(out)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            ).stdout.decode("utf-8")
            self.assertIn('"width": 1080', probe)
            self.assertIn('"height": 1920', probe)

    def test_slideshow_needs_at_least_one_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MediaError):
                asyncio.run(build_slideshow([], Path(tmp) / "slide.mp4"))

    def test_slideshow_cleans_up_concat_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            images = [_make_image(base / "a.jpg")]
            asyncio.run(build_slideshow(images, base / "slide.mp4", seconds_per_photo=1))
            self.assertEqual(list(base.glob("*_concat.txt")), [])

    def test_slideshow_works_with_relative_paths(self):
        """
        Regresi 21 Sep 2026: DOWNLOAD_DIR relatif membuat ffmpeg gagal
        "No such file or directory" karena demuxer `concat` menyelesaikan path
        relatif terhadap LOKASI BERKAS DAFTAR, bukan CWD. Path harus absolut.
        """
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                # Direktori kerja dipindah ke dalam temp agar path relatif benar.
                os.chdir(tmp)
                os.makedirs("media", exist_ok=True)
                images = [
                    _make_image(Path("media") / "a.jpg", "red"),
                    _make_image(Path("media") / "b.jpg", "green"),
                ]
                self.assertFalse(images[0].is_absolute())
                out = asyncio.run(
                    build_slideshow(images, Path("media") / "slide.mp4", seconds_per_photo=1)
                )
                self.assertTrue(out.exists())
                self.assertGreater(out.stat().st_size, 0)
            finally:
                os.chdir(cwd)

    def test_concat_list_uses_absolute_posix_paths(self):
        path = Path("media/foto 1.jpg")
        escaped = _ffconcat_escape(path)
        self.assertNotIn("\\", escaped, "path Windows harus jadi posix agar ffmpeg membacanya")
        self.assertTrue(Path(escaped).is_absolute())


class TestPreparePostMedia(unittest.TestCase):
    def _set_download_dir(self, base: Path) -> str:
        original = Config.DOWNLOAD_DIR
        Config.DOWNLOAD_DIR = str(base)
        return original

    def test_photo_post_builds_parts_and_slideshow(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = self._set_download_dir(base)
            try:
                images = [
                    str(_make_image(base / f"src-{i}.jpg", ("red", "green", "blue")[i % 3]))
                    for i in range(12)
                ]
                item = TikTokItem(
                    id="foto-1", unique_id="indahjkt48", kind="photo", images=images,
                )
                media = asyncio.run(prepare_post_media(item, build_slideshow_video=True))
                self.assertEqual(media.kind, "photo")
                self.assertEqual(len(media.images), 12)
                self.assertEqual([len(p) for p in media.image_parts], [10, 2])
                self.assertIsNotNone(media.video_path)
                self.assertTrue(media.video_path.exists())
                self.assertIsNone(media.archive_video_path)   # foto dikirim sebagai album
                self.assertGreater(media.size_bytes, 0)
            finally:
                Config.DOWNLOAD_DIR = original

    def test_photo_post_without_slideshow_has_no_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = self._set_download_dir(base)
            try:
                item = TikTokItem(
                    id="foto-2", unique_id="indahjkt48", kind="photo",
                    images=[str(_make_image(base / "a.jpg"))],
                )
                media = asyncio.run(prepare_post_media(item, build_slideshow_video=False))
                self.assertIsNone(media.video_path)
                self.assertEqual(len(media.image_parts), 1)
            finally:
                Config.DOWNLOAD_DIR = original

    def test_photo_post_without_downloadable_image_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = self._set_download_dir(base)
            try:
                item = TikTokItem(
                    id="foto-3", unique_id="indahjkt48", kind="photo",
                    images=[str(base / "hilang.jpg")],
                )
                with self.assertRaises(MediaError):
                    asyncio.run(prepare_post_media(item, build_slideshow_video=False))
            finally:
                Config.DOWNLOAD_DIR = original

    def test_video_post_uses_fixture_media_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = self._set_download_dir(base)
            try:
                sample = _make_video(base / "sample.mp4", seconds=2)
                item = TikTokItem(
                    id="video-1", unique_id="indahjkt48", kind="video",
                    raw={"fixture_media": {"video": str(sample)}},
                )
                media = asyncio.run(prepare_post_media(item, build_slideshow_video=False))
                self.assertEqual(media.kind, "video")
                self.assertTrue(media.archive_video_path.exists())
                self.assertEqual(media.archive_video_path, media.video_path)
                self.assertGreater(media.duration_seconds, 1.0)
            finally:
                Config.DOWNLOAD_DIR = original



class _FakeProcess:
    """Proses yt-dlp tiruan untuk uji tanpa memanggil yt-dlp sungguhan."""

    def __init__(self, returncode: int = 1, stdout: bytes = b"", hang: bool = False):
        self.returncode = returncode
        self._stdout = stdout
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(30)  # tidak pernah selesai → memicu timeout
        return self._stdout, None

    def kill(self):
        self.killed = True
        if self.returncode is None:
            self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


class TestDownloadVideoYtdlp(unittest.TestCase):
    """Perilaku jalur yt-dlp di download_video (proses dimock)."""

    def _item(self) -> TikTokItem:
        return TikTokItem(
            id="video-x", unique_id="indahjkt48", kind="video",
            source_url="https://www.tiktok.com/@indahjkt48/video/123", video_url="",
        )

    def test_timeout_kills_ytdlp_process(self):
        """
        Timeout unduhan: proses yt-dlp harus di-KILL (tidak bocor jadi orphan),
        lalu alur lanjut ke fallback (berujung MediaError karena tak ada sumber lain).
        """
        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            fake = _FakeProcess(hang=True)
            captured["process"] = fake
            return fake

        async def fake_embed(*args, **kwargs):
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(asyncio, "create_subprocess_exec", fake_exec), \
                 mock.patch("bot.tiktok_media.fetch_embed_post_media", fake_embed):
                with self.assertRaises(MediaError):
                    asyncio.run(download_video(self._item(), dest_dir=tmp, timeout_seconds=0.05))
        process = captured["process"]
        self.assertTrue(process.killed, "proses yt-dlp harus di-kill saat timeout")
        self.assertTrue(process.waited, "proses yt-dlp harus di-await setelah kill")

    def test_config_args_appended_before_url(self):
        """Cookies/proxy/extra args dari Config disisipkan sebelum URL."""
        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return _FakeProcess(returncode=1, stdout=b"gagal biasa")

        async def fake_embed(*args, **kwargs):
            return {}

        saved = (
            Config.TIKTOK_YTDLP_COOKIES_FILE,
            Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER,
            Config.TIKTOK_YTDLP_PROXY,
            Config.TIKTOK_YTDLP_EXTRA_ARGS,
        )
        Config.TIKTOK_YTDLP_COOKIES_FILE = "C:/tmp/cookies.txt"
        Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER = ""
        Config.TIKTOK_YTDLP_PROXY = "http://user:pass@127.0.0.1:8080"
        Config.TIKTOK_YTDLP_EXTRA_ARGS = "--sleep-requests 2"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(asyncio, "create_subprocess_exec", fake_exec), \
                     mock.patch("bot.tiktok_media.fetch_embed_post_media", fake_embed):
                    with self.assertRaises(MediaError):
                        asyncio.run(download_video(self._item(), dest_dir=tmp, timeout_seconds=5))
        finally:
            (
                Config.TIKTOK_YTDLP_COOKIES_FILE,
                Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER,
                Config.TIKTOK_YTDLP_PROXY,
                Config.TIKTOK_YTDLP_EXTRA_ARGS,
            ) = saved

        cmd = captured["cmd"]
        url = cmd[-1]
        self.assertTrue(url.startswith("https://www.tiktok.com/"))
        self.assertIn("--cookies", cmd)
        self.assertIn("C:/tmp/cookies.txt", cmd)
        self.assertIn("--proxy", cmd)
        self.assertIn("http://user:pass@127.0.0.1:8080", cmd)
        self.assertIn("--sleep-requests", cmd)
        # Argumen konfigurasi berada sebelum URL.
        self.assertLess(cmd.index("--cookies"), len(cmd) - 1)

    def test_ip_block_raises_media_permanent_error(self):
        """Pesan blokir IP dari yt-dlp diklasifikasikan kegagalan PERMANEN."""
        stderr = (
            b"ERROR: [TikTok] 123: Your IP address is blocked from accessing this post"
        )

        async def fake_exec(*cmd, **kwargs):
            return _FakeProcess(returncode=1, stdout=stderr)

        embed_called = {"count": 0}

        async def fake_embed(*args, **kwargs):
            embed_called["count"] += 1
            return {"video_url": "https://cdn.example.com/fresh.mp4"}

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(asyncio, "create_subprocess_exec", fake_exec), \
                 mock.patch("bot.tiktok_media.fetch_embed_post_media", fake_embed):
                with self.assertRaises(MediaPermanentError) as caught:
                    asyncio.run(download_video(self._item(), dest_dir=tmp, timeout_seconds=5))
        # Tidak layak mencoba fallback embed — blokir IP berlaku untuk post yang sama.
        self.assertEqual(embed_called["count"], 0)
        self.assertTrue(caught.exception.permanent)
        self.assertTrue(is_permanent_media_error(caught.exception))

    def test_generic_media_error_is_not_permanent(self):
        self.assertFalse(MediaError("x").permanent)
        self.assertFalse(is_permanent_media_error(MediaError("x")))
        self.assertFalse(is_permanent_media_error(ValueError("x")))

    def test_ytdlp_config_args_noop_when_unset(self):
        saved = (
            Config.TIKTOK_YTDLP_COOKIES_FILE,
            Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER,
            Config.TIKTOK_YTDLP_PROXY,
            Config.TIKTOK_YTDLP_EXTRA_ARGS,
        )
        Config.TIKTOK_YTDLP_COOKIES_FILE = ""
        Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER = ""
        Config.TIKTOK_YTDLP_PROXY = ""
        Config.TIKTOK_YTDLP_EXTRA_ARGS = ""
        try:
            self.assertEqual(_ytdlp_config_args(), [])
        finally:
            (
                Config.TIKTOK_YTDLP_COOKIES_FILE,
                Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER,
                Config.TIKTOK_YTDLP_PROXY,
                Config.TIKTOK_YTDLP_EXTRA_ARGS,
            ) = saved

    def test_ytdlp_config_args_browser_cookie(self):
        saved = Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER
        Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER = "firefox"
        try:
            args = _ytdlp_config_args()
        finally:
            Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER = saved
        if Config.TIKTOK_YTDLP_COOKIES_FILE or Config.TIKTOK_YTDLP_PROXY or Config.TIKTOK_YTDLP_EXTRA_ARGS:
            self.skipTest("lingkungan uji mengisi variabel yt-dlp lain")
        self.assertEqual(args, ["--cookies-from-browser", "firefox"])


class TestCleanup(unittest.TestCase):
    def test_cleanup_removes_files_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = Config.DOWNLOAD_DIR
            Config.DOWNLOAD_DIR = str(base)
            try:
                sample = _make_video(base / "sample.mp4", seconds=2)
                item = TikTokItem(
                    id="video-2", unique_id="indahjkt48", kind="video",
                    raw={"fixture_media": {"video": str(sample)}},
                )
                media = asyncio.run(prepare_post_media(item, build_slideshow_video=False))
                target = media.archive_video_path
                self.assertTrue(target.exists())
                cleanup_media(media)
                self.assertFalse(target.exists())
            finally:
                Config.DOWNLOAD_DIR = original

    def test_copy_fixture_video_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Config.DOWNLOAD_DIR
            Config.DOWNLOAD_DIR = tmp
            try:
                item = TikTokItem(
                    id="video-3", unique_id="indahjkt48",
                    raw={"fixture_media": {"video": str(Path(tmp) / "tidak-ada.mp4")}},
                )
                with self.assertRaises(MediaError):
                    copy_fixture_video(item)
            finally:
                Config.DOWNLOAD_DIR = original



if __name__ == "__main__":
    unittest.main()
