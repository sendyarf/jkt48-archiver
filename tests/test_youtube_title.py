import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.config import Config
from bot.thumbnail_collage import (
    CELL_HEIGHT,
    CELL_WIDTH,
    COLLAGE_COLS,
    COLLAGE_COUNT,
    COLLAGE_ROWS,
    COLUMN_WIDTHS,
    THUMB_HEIGHT,
    THUMB_WIDTH,
    _candidate_times,
    _cover_single_frame,
    _extract_frame_at,
    build_collage,
    build_xstack_filter,
    pick_sample_times,
)
from bot.youtube_uploader import YouTubeChannelPool

_ID_MONTHS = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


def _expected(started_at: str) -> str:
    """Judul yang diharapkan, dihitung lewat timezone Config agar tes tetap
    benar bila DISPLAY_TIMEZONE_* diubah via environment."""
    dt = datetime.fromisoformat(started_at).astimezone(
        timezone(timedelta(hours=Config.DISPLAY_TIMEZONE_OFFSET_HOURS))
    )
    date_part = f"{dt.day} {_ID_MONTHS[dt.month]} {dt.year}"
    time_part = dt.strftime("%H:%M")
    return f"{{}} - {date_part} | {time_part} {Config.DISPLAY_TIMEZONE_LABEL}"


class TestYouTubeTitleFormat(unittest.TestCase):
    """Judul YouTube harus identik dengan format judul website (lib/wib.ts):
    'LIVE <PLATFORM> <NAMA> - <tanggal Indonesia> | <HH:MM> <label>'."""

    def test_showroom_title_matches_web_format(self):
        expected = _expected("2026-09-18T13:18:00+00:00").format(
            "LIVE SHOWROOM SONA JKT48"
        )
        title = YouTubeChannelPool.build_title(
            "Sona JKT48", "2026-09-18T13:18:00+00:00", platform="showroom"
        )
        self.assertEqual(title, expected)

    def test_idn_title_matches_web_format(self):
        expected = _expected("2026-09-15T09:39:00+00:00").format(
            "LIVE IDN FAHIRA JKT48"
        )
        title = YouTubeChannelPool.build_title(
            "Fahira JKT48", "2026-09-15T09:39:00+00:00", platform="idn"
        )
        self.assertEqual(title, expected)

    def test_default_platform_is_idn(self):
        title = YouTubeChannelPool.build_title(
            "Rilly JKT48", "2026-09-18T13:18:00+00:00"
        )
        self.assertTrue(title.startswith("LIVE IDN "))

    def test_name_uppercased_like_web(self):
        title = YouTubeChannelPool.build_title(
            "sona jkt48", "2026-09-18T13:18:00+00:00", platform="showroom"
        )
        self.assertIn("SONA JKT48", title)

    def test_unparsable_time_falls_back_without_date(self):
        title = YouTubeChannelPool.build_title("Test JKT48", "bukan-waktu", platform="idn")
        self.assertEqual(title, "LIVE IDN TEST JKT48")

    def test_title_truncated_to_100_chars(self):
        long_name = "NamaSangatPanjang" * 10
        title = YouTubeChannelPool.build_title(
            long_name, "2026-09-18T13:18:00+00:00", platform="showroom"
        )
        self.assertLessEqual(len(title), 100)

    def test_description_mentions_platform(self):
        desc = YouTubeChannelPool.build_description(
            "Sona JKT48", "jkt48_sona", "2026-09-18T13:18:00+00:00", platform="showroom"
        )
        self.assertIn("Showroom Live", desc)
        self.assertIn("#ShowroomLive", desc)


class TestThumbnailCollage(unittest.TestCase):
    """Kolase 3x2: 6 slot cover penuh 1280x720, gagal = None (tak ganggu upload)."""

    def test_grid_dimensions_fill_720p(self):
        self.assertEqual((COLLAGE_COLS, COLLAGE_ROWS, COLLAGE_COUNT), (3, 2, 6))
        self.assertEqual((THUMB_WIDTH, THUMB_HEIGHT), (1280, 720))
        # Kolase harus PERSIS 1280x720 (syarat thumbnail YouTube).
        self.assertEqual(COLUMN_WIDTHS, [426, 426, 428])
        self.assertEqual(CELL_WIDTH, COLUMN_WIDTHS[0])
        self.assertEqual(sum(COLUMN_WIDTHS), THUMB_WIDTH)
        self.assertEqual(CELL_HEIGHT * COLLAGE_ROWS, THUMB_HEIGHT)
        self.assertTrue(all(w % 2 == 0 for w in COLUMN_WIDTHS))
        self.assertEqual(CELL_HEIGHT % 2, 0)

    def test_sample_times_spread_with_edge_margin(self):
        times = pick_sample_times(3600.0)
        self.assertEqual(len(times), 6)
        self.assertTrue(all(180.0 < t < 3420.0 for t in times))
        self.assertEqual(times, sorted(times))

    def test_sample_times_short_video_unique(self):
        times = pick_sample_times(30.0)
        self.assertEqual(len(times), 6)
        self.assertTrue(all(t >= 0.5 for t in times))
        self.assertEqual(len(set(times)), 6)
        self.assertEqual(times, sorted(times))

    def test_sample_times_tiny_video_no_hang(self):
        times = pick_sample_times(3.0)
        self.assertEqual(len(times), 6)
        self.assertEqual(len(set(times)), 6)

    def test_xstack_filter_cover_no_bars(self):
        filt = build_xstack_filter()
        self.assertIn("force_original_aspect_ratio=increase", filt)
        self.assertIn("crop=426:360", filt)
        self.assertIn("crop=428:360", filt)
        self.assertIn("xstack=inputs=6", filt)
        # Pixel format disterilkan: segmen live bisa campur yuv420p/yuvj420p
        # dan xstack menolak input yang tidak seragam (insiden kolase gagal
        # intermiten → YouTube pakai thumbnail otomatis berpilar hitam).
        self.assertIn("format=yuv420p", filt)
        # Posisi grid: kolom 0/426/852, baris 0/360.
        for x, y in [(0, 0), (426, 0), (852, 0), (0, 360), (426, 360), (852, 360)]:
            self.assertIn(f"{x}_{y}", filt)

    def test_candidate_times_retries_near_primary(self):
        cands = _candidate_times(10.0, 100.0)
        self.assertEqual(cands[0], 10.0)
        self.assertGreater(len(cands), 1)
        self.assertTrue(all(t >= 0.5 for t in cands))
        # Tidak melampaui durasi ke atas bila durasi diketahui.
        self.assertTrue(all(t < 100.0 for t in cands))

    def test_extract_frame_failure_returns_false(self):
        with (
            patch("bot.thumbnail_collage.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("bot.thumbnail_collage._run") as run,
        ):
            failed = MagicMock()
            failed.returncode = 1
            failed.stderr = b"seek boom"
            run.return_value = failed
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "f.jpg"
                self.assertFalse(_extract_frame_at(Path(__file__), 1.0, dest))

    def test_cover_single_frame_fallback_when_xstack_unusable(self):
        """Bila kolase tak bisa dibuat, fallback 1 frame tetap 1280x720 cover."""
        with (
            patch("bot.thumbnail_collage.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("bot.thumbnail_collage.get_duration_seconds", return_value=30.0),
            patch("bot.thumbnail_collage._extract_frames", return_value=[]),
            patch("bot.thumbnail_collage._cover_single_frame", return_value=True) as cover,
        ):
            out = build_collage(Path(__file__), Path(tempfile.gettempdir()) / "thumb_fb.jpg")
            self.assertIsNotNone(out)
            cover.assert_called_once()

    def test_build_collage_missing_file_returns_none(self):
        self.assertIsNone(build_collage(Path("tidak-ada.mp4")))

    def test_build_collage_no_ffmpeg_returns_none(self):
        with patch("bot.thumbnail_collage.shutil.which", return_value=None):
            self.assertIsNone(build_collage(Path(__file__)))

    def test_build_collage_ffmpeg_failure_returns_none(self):
        with (
            patch("bot.thumbnail_collage.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("bot.thumbnail_collage.get_duration_seconds", return_value=100.0),
            patch("bot.thumbnail_collage._run") as run,
        ):
            failed = MagicMock()
            failed.returncode = 1
            failed.stderr = b"boom"
            run.return_value = failed
            self.assertIsNone(build_collage(Path(__file__)))

    def test_set_thumbnail_invalid_args_no_api_call(self):
        pool = YouTubeChannelPool.__new__(YouTubeChannelPool)
        pool._services = {}
        self.assertFalse(pool.set_thumbnail("", Path(__file__)))
        self.assertFalse(pool.set_thumbnail("vid123", Path("tidak-ada.jpg")))
        empty = Path(__file__).with_name("empty_thumb_test.jpg")
        try:
            empty.write_bytes(b"")
            self.assertFalse(pool.set_thumbnail("vid123", empty))
        finally:
            empty.unlink(missing_ok=True)

    def test_set_thumbnail_fresh_media_per_channel(self):
        """MediaFileUpload dibuat ulang per channel — stream pertama tidak boleh
        dikonsumsi saat request channel pertama gagal (thumbnail tidak pernah
        terpasang di channel berikut)."""
        pool = YouTubeChannelPool.__new__(YouTubeChannelPool)
        pool._services = {}
        ch_a = MagicMock(label="a", token_file="ta", secret_file="sa")
        ch_b = MagicMock(label="b", token_file="tb", secret_file="sb")
        media_calls = []
        service_a = MagicMock()
        service_a.thumbnails.return_value.set.return_value.execute.side_effect = RuntimeError("quota")
        service_b = MagicMock()

        def fake_media(*args, **kwargs):
            m = MagicMock(name=f"media{len(media_calls)}")
            media_calls.append(m)
            return m

        with (
            patch("bot.youtube_uploader.Config.load_youtube_channels", return_value=[ch_a, ch_b]),
            patch("bot.youtube_uploader.MediaFileUpload", side_effect=fake_media),
            patch.object(pool, "_get_service_for_channel", side_effect=[service_a, service_b]),
        ):
            thumb = Path(__file__).with_name("tmp_set_thumb.jpg")
            try:
                thumb.write_bytes(b"\xff\xd8\xff\xd9")  # JPEG minimal
                self.assertTrue(pool.set_thumbnail("vidOK", thumb))
            finally:
                thumb.unlink(missing_ok=True)

        self.assertEqual(len(media_calls), 2, "harus ada MediaFileUpload baru per channel")
        self.assertIsNot(media_calls[0], media_calls[1])
        service_b.thumbnails.return_value.set.assert_called_once()


if __name__ == "__main__":
    unittest.main()
