"""
Tes untuk bot/timeutil.py — memastikan waktu tidak bergantung zona waktu server.

Cakupan:
  * Waktu yang disimpan adalah UTC dan membawa penanda zona waktu.
  * Nilai bermarker ('Z' / '+07:00') dihormati apa adanya.
  * Nilai naif (baris lama) diperlakukan sebagai UTC, atau memakai offset
    warisan bila dikonfigurasi.
  * Konversi tampilan menghasilkan jam WIB yang benar.
"""
from datetime import datetime, timedelta, timezone

import pytest

from bot import timeutil


def test_utc_now_iso_membawa_penanda_utc():
    text = timeutil.utc_now_iso()
    assert text.endswith("+00:00"), text
    assert timeutil.has_timezone_marker(text)


def test_utc_now_iso_tidak_memakai_waktu_dinding_lokal():
    """Selisih terhadap UTC harus nol, bukan offset server."""
    parsed = datetime.fromisoformat(timeutil.utc_now_iso())
    delta = abs((timeutil.utc_now() - parsed).total_seconds())
    assert delta < 2, "utc_now_iso() tidak menghasilkan waktu UTC"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-15T18:36:37.428469+00:00", True),
        ("2026-09-15T18:36:37.428469Z", True),
        ("2026-09-15T18:36:37+07:00", True),
        ("2026-09-15T18:36:37.428469", False),
        ("2026-09-15 18:36:37", False),
        ("", False),
    ],
)
def test_has_timezone_marker(value, expected):
    assert timeutil.has_timezone_marker(value) is expected


def test_parse_stored_menghormati_penanda():
    """Nilai bermarker dipertahankan apa adanya, namun menunjuk instan UTC yang sama."""
    parsed = timeutil.parse_stored("2026-09-15T18:36:37+07:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=7)
    # 18:36 WIB menunjuk instan yang sama dengan 11:36 UTC.
    assert parsed.astimezone(timezone.utc).hour == 11
    assert parsed.astimezone(timezone.utc).minute == 36


def test_parse_stored_penanda_utc_eksplisit():
    parsed = timeutil.parse_stored("2026-09-15T11:36:37Z")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.hour == 11 and parsed.minute == 36


def test_parse_stored_naif_dianggap_utc_secara_default():
    parsed = timeutil.parse_stored("2026-09-15T18:36:37.428469")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.hour == 18


def test_parse_stored_naif_memakai_offset_warisan():
    parsed = timeutil.parse_stored("2026-09-15T18:36:37.428469", legacy_offset_hours=8)
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=8)
    # 18:36 waktu dinding UTC+8 menunjuk instan 10:36 UTC.
    assert parsed.astimezone(timezone.utc).hour == 10
    assert parsed.astimezone(timezone.utc).minute == 36


def test_parse_stored_nilai_tidak_valid():
    assert timeutil.parse_stored("bukan waktu") is None
    assert timeutil.parse_stored("") is None
    assert timeutil.parse_stored(None) is None


def test_format_display_utc_ke_wib():
    """Nilai UTC harus tampil sebagai WIB (UTC+7), bukan waktu dinding server."""
    result = timeutil.format_display(
        "2026-09-15T10:36:37+00:00", "%Y-%m-%d %H:%M", offset_hours=7
    )
    assert result == "2026-09-15 17:36"


def test_format_display_naif_dibaca_utc_tanpa_offset_warisan():
    result = timeutil.format_display(
        "2026-09-15T10:36:37", "%H:%M", offset_hours=7, legacy_offset_hours=0
    )
    assert result == "17:36"


def test_format_display_naif_dengan_offset_warisan():
    """Baris lama yang ditulis pada UTC+8 dibaca benar saat offset warisan diisi."""
    result = timeutil.format_display(
        "2026-09-15T18:36:37", "%H:%M", offset_hours=7, legacy_offset_hours=8
    )
    # 18:36 UTC+8 = 10:36 UTC = 17:36 WIB
    assert result == "17:36"


def test_format_display_hasil_tidak_bergantung_zona_waktu_proses():
    """
    Inti perbaikan: hasil konversi identik meski jam dinding proses berbeda.

    Nilai yang sama harus menghasilkan jam tampilan yang sama, karena seluruh
    perhitungan memakai UTC sebagai titik awal.
    """
    stored = "2026-09-15T10:36:37+00:00"
    first = timeutil.format_display(stored, "%H:%M", offset_hours=7)
    for _ in range(3):
        assert timeutil.format_display(stored, "%H:%M", offset_hours=7) == first
    assert first == "17:36"


def test_format_display_offset_wita_dan_wit():
    stored = "2026-09-15T10:36:37+00:00"
    assert timeutil.format_display(stored, "%H:%M", offset_hours=8) == "18:36"
    assert timeutil.format_display(stored, "%H:%M", offset_hours=9) == "19:36"


def test_format_display_nilai_kosong_mengembalikan_string_kosong():
    assert timeutil.format_display("", "%H:%M", offset_hours=7) == ""
    assert timeutil.format_display(None, "%H:%M", offset_hours=7) == ""
    assert timeutil.format_display("rusak", "%H:%M", offset_hours=7) == ""


def test_selisih_waktu_benar_meski_representasi_berbeda():
    """
    Dua string dengan penanda berbeda tetap menunjuk instan yang benar.
    '10:00+07:00' adalah 7 jam LEBIH AWAL daripada '10:00 UTC'.
    """
    ten_utc = timeutil.parse_stored("2026-09-15T10:00:00+00:00")
    ten_jakarta = timeutil.parse_stored("2026-09-15T10:00:00+07:00")
    assert ten_utc is not None and ten_jakarta is not None
    assert (ten_utc - ten_jakarta) == timedelta(hours=7)


def test_instan_sama_dengan_representasi_berbeda_menghasilkan_selisih_nol():
    a = timeutil.parse_stored("2026-09-15T10:00:00+00:00")
    b = timeutil.parse_stored("2026-09-15T17:00:00+07:00")
    assert a is not None and b is not None
    assert (a - b) == timedelta(0)


def test_timezone_utc_bukan_waktu_lokal():
    """Penjaga regresi: default fungsi harus UTC, bukan astimezone() lokal."""
    assert timeutil.utc_now().utcoffset() == timedelta(0)
    assert timeutil.parse_stored("2026-01-01T00:00:00").utcoffset() == timedelta(0)  # type: ignore[union-attr]
    assert datetime.now(timezone.utc).utcoffset() == timedelta(0)
