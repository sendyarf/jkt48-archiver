import unittest
from datetime import timedelta

from bot.showroom_monitor import (
    detection_gap_seconds,
    should_resume_showroom,
)
from bot.timeutil import utc_now


class TestDetectionGapSeconds(unittest.TestCase):
    """Observability telatnya mulai merekam sejak live Showroom dimulai."""

    def test_recent_start_returns_small_gap(self):
        started = (utc_now() - timedelta(seconds=20)).isoformat()
        gap = detection_gap_seconds(started)
        self.assertIsNotNone(gap)
        self.assertLessEqual(gap, 25.0)
        self.assertGreaterEqual(gap, 0.0)

    def test_late_start_returns_positive_gap(self):
        # Live dimulai 90 detik lalu → kita telat ±90 detik.
        started = (utc_now() - timedelta(seconds=90)).isoformat()
        gap = detection_gap_seconds(started)
        self.assertIsNotNone(gap)
        self.assertGreaterEqual(gap, 85.0)
        self.assertLessEqual(gap, 95.0)

    def test_naive_timestamp_treated_as_utc(self):
        # Format DB lama tanpa offset: jangan sampai exception.
        gap = detection_gap_seconds("2026-01-01T00:00:00")
        self.assertIsNotNone(gap)
        self.assertGreater(gap, 0.0)

    def test_invalid_or_empty_returns_none(self):
        self.assertIsNone(detection_gap_seconds(None))
        self.assertIsNone(detection_gap_seconds(""))
        self.assertIsNone(detection_gap_seconds("bukan-waktu"))


class TestShouldResumeShowroom(unittest.TestCase):
    """Keputusan lanjut merekam setelah ffmpeg berhenti lebih awal."""

    def test_resume_when_room_live(self):
        self.assertTrue(should_resume_showroom(0, 40, True))

    def test_resume_when_unknown(self):
        # API hiccup / offline belum terkonfirmasi → aman lanjut.
        self.assertTrue(should_resume_showroom(0, 40, None))

    def test_stop_when_offline_confirmed(self):
        self.assertFalse(should_resume_showroom(0, 40, False))

    def test_stop_when_quota_exhausted(self):
        self.assertFalse(should_resume_showroom(40, 40, True))
        self.assertFalse(should_resume_showroom(41, 40, None))

    def test_last_allowed_attempt_still_resumes(self):
        self.assertTrue(should_resume_showroom(39, 40, True))


if __name__ == "__main__":
    unittest.main()
