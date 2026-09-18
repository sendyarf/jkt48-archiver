"""
seed_hls.py - Populate member_hls table with known HLS URLs & display names.

HLS URLs untuk AWS IVS channel JKT48 bersifat PERMANEN per member
(channel ID tidak berubah antar sesi live), sehingga cukup di-seed
sekali dan bot tidak perlu probe IDN GraphQL untuk member ini lagi.

Jalankan sekali di server:
    python3 -m bot.seed_hls
"""

import sys
import logging
from bot import database

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE = "https://4b964ca68cf1.us-east-1.playback.live-video.net/api/video/v1/us-east-1.050891932989.channel"

# username → channel_id (dari history Telegram & log PM2)
KNOWN_HLS: dict[str, str] = {
    "jkt48_aralie":   "oxXRFBYUK9HN",
    "jkt48_bella":    "2C3t5GoDLzUG",
    "jkt48_carissa":  "dUnDQVccbExB",
    "jkt48_christy":  "R4bPsdzRonL3",
    "jkt48_cynthia":  "UVu6FmzsZAuY",
    "jkt48_daisy":    "tk9WxdsYj8NE",
    "jkt48_danella":  "1TQqlwWPwce5",
    "jkt48_delynn":   "5j4KMiUb4eXE",
    "jkt48_ekin":     "I9niN5y7NTZF",
    "jkt48_eli":      "809zi18MGuB1",
    "jkt48_elin":     "OHdxDVm8kOqf",
    "jkt48_ella":     "BDGTjOuDMTDH",
    "jkt48_erine":    "0nMPtjEVo1Ds",
    "jkt48_fahira":   "s8h5WJSJjmj6",
    "jkt48_fera":     "zH8hKGmlbbxC",
    "jkt48_freya":    "eCTljQvrMYaf",
    "jkt48_fritzy":   "K9fM2uTS2hX3",
    "jkt48_giaa":     "3dNPEzieJMP6",
    "jkt48_gita":     "k4fAFOYXENlG",
    "jkt48_gracie":   "ztIO0UisaA3P",
    "jkt48_greesel":  "z3nXSqOvE9Fs",
    "jkt48_heidi":    "1BSvFH9hwEuZ",
    "jkt48_indah":    "oKpAUHJSe6RM",
    "jkt48_intan":    "GEcz70BKlk0o",
    "jkt48_jazzy":    "3IswtNd98GcQ",
    "jkt48_jemima":   "pYGgcUgAi8pI",
    "jkt48_jessi":    "726SnUjvlfRJ",
    "jkt48_kathrina": "a4Aq6zWq9xBO",
    "jkt48_lana":     "XcUYof208I6e",
    "jkt48_levi":     "Lci1odh0sCZU",
    "jkt48_lia":      "ZEhAbERTyvxy",
    "jkt48_lily":     "4hPWCxNDO8aY",
    "jkt48_lulu":     "LuflGrysVdSz",
    "jkt48_lyn":      "YMhzlmrLe7tw",
    "jkt48_maira":    "e39rcA4LMxwI",
    "jkt48_marsha":   "sSKNwImoM3zD",
    "jkt48_maxine":   "e24XJUgQfCvY",
    "jkt48_michie":   "uE0Cu8MD1aMc",
    "jkt48_mikaela":  "948DSG1qwIJH",
    "jkt48_muthe":    "sm9AmQPkGDGS",
    "jkt48_nachia":   "zVh5JKAAtDUC",
    "jkt48_nala":     "nCPKiODqPEOs",
    "jkt48_nayla":    "s29ur0XkIpE2",
    "jkt48_oline":    "gYN6fQPMCqs0",
    "jkt48_olla":     "QGp4Op9dfxOA",
    "jkt48_oniel":    "KGbEli9xlInK",
    "jkt48_ralyne":   "QC3wCAqzZ0v9",
    "jkt48_rara":     "MzJvAVsJnOdF",
    "jkt48_ribka":    "m6srSRt8fTY6",
    "jkt48_rilly":    "pehOrZyTt2j4",
    "jkt48_sona":     "wb9FCylf9CC6",
    "jkt48_virgi":    "9DxiaFf6lSom",
}

# Display name asli tiap member (dari notifikasi Telegram)
DISPLAY_NAMES: dict[str, str] = {
    "jkt48_aralie":   "Aralie JKT48",
    "jkt48_bella":    "Bella JKT48",
    "jkt48_carissa":  "Carissa JKT48",
    "jkt48_christy":  "Christy JKT48",
    "jkt48_cynthia":  "Cynthia JKT48",
    "jkt48_daisy":    "Daisy JKT48",
    "jkt48_danella":  "Danella JKT48",
    "jkt48_delynn":   "Delynn JKT48",
    "jkt48_ekin":     "Ekin JKT48",
    "jkt48_eli":      "Eli JKT48",
    "jkt48_elin":     "Elin JKT48",
    "jkt48_ella":     "Ella JKT48",
    "jkt48_erine":    "Erine JKT48",
    "jkt48_fahira":   "Fahira JKT48",
    "jkt48_fera":     "Fera JKT48",
    "jkt48_freya":    "Freya JKT48",
    "jkt48_fritzy":   "Fritzy JKT48",
    "jkt48_giaa":     "Giaa JKT48",
    "jkt48_gita":     "Gita JKT48",
    "jkt48_gracie":   "Gracie JKT48",
    "jkt48_greesel":  "Greesel JKT48",
    "jkt48_heidi":    "Heidi JKT48",
    "jkt48_indah":    "Indah JKT48",
    "jkt48_intan":    "Intan JKT48",
    "jkt48_jazzy":    "Jazzy JKT48",
    "jkt48_jemima":   "Jemima JKT48",
    "jkt48_jessi":    "Jessi JKT48",
    "jkt48_kathrina": "Kathrina JKT48",
    "jkt48_lana":     "Lana JKT48",
    "jkt48_levi":     "Levi JKT48",
    "jkt48_lia":      "Lia JKT48",
    "jkt48_lily":     "Lily JKT48",
    "jkt48_lulu":     "Lulu JKT48",
    "jkt48_lyn":      "Lyn JKT48",
    "jkt48_maira":    "Maira JKT48",
    "jkt48_marsha":   "Marsha JKT48",
    "jkt48_maxine":   "Maxine JKT48",
    "jkt48_michie":   "Michie JKT48",
    "jkt48_mikaela":  "Mikaela JKT48",
    "jkt48_muthe":    "Muthe JKT48",
    "jkt48_nachia":   "Nachia JKT48",
    "jkt48_nala":     "Nala JKT48",
    "jkt48_nayla":    "Nayla JKT48",
    "jkt48_oline":    "Oline JKT48",
    "jkt48_olla":     "Olla JKT48",
    "jkt48_oniel":    "Oniel JKT48",
    "jkt48_ralyne":   "Ralyne JKT48",
    "jkt48_rara":     "Rara JKT48",
    "jkt48_ribka":    "Ribka JKT48",
    "jkt48_rilly":    "Rilly JKT48",
    "jkt48_sona":     "Sona JKT48",
    "jkt48_virgi":    "Virgi JKT48",
}

# Member yang belum diketahui HLS-nya (perlu live dulu untuk ditemukan)
UNKNOWN_HLS = [
    "jkt48_feni",
    "jkt48_fiony",
    "jkt48_kimmy",
    "jkt48_raisha",
    "jkt48_trisha",
    "jkt48-official",
]


def main() -> None:
    database.init_db()

    seeded = 0
    for username, channel_id in KNOWN_HLS.items():
        hls_url = f"{BASE}.{channel_id}.m3u8"
        display_name = DISPLAY_NAMES.get(username, username)
        database.upsert_member_hls(
            username=username,
            display_name=display_name,
            hls_url=hls_url,
            confirmed=True,      # ← tidak akan di-probe IDN GraphQL lagi
        )
        logger.info("✅ %s (%s) → %s", username, display_name, channel_id)
        seeded += 1

    logger.info("")
    logger.info("Seeded %d member HLS URLs.", seeded)
    logger.info(
        "%d member masih belum diketahui HLS-nya: %s",
        len(UNKNOWN_HLS),
        ", ".join(UNKNOWN_HLS),
    )
    logger.info("Bot akan probe IDN GraphQL hanya untuk member di atas.")


if __name__ == "__main__":
    main()
