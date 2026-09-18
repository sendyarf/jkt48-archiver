import unittest
from datetime import datetime, timedelta, timezone

from bot.config import Config
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


if __name__ == "__main__":
    unittest.main()
