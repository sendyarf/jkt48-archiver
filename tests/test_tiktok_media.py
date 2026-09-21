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

from bot.config import Config
from bot.tiktok_client import TikTokItem
from bot.tiktok_media import (
    MediaError,
    _ffconcat_escape,
    build_slideshow,
    cleanup_media,
    copy_fixture_video,
    download_images,
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
