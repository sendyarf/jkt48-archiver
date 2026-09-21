# Fixture arsip TikTok (untuk uji tanpa jaringan)

Folder ini dipakai `TIKTOK_PROVIDER=fixture` (`bot/tiktok_client.py::FixtureProvider`)
dan unit test, supaya seluruh alur bisa diuji tanpa jaringan dan tanpa
bergantung pada anti-bot TikTok / batas request tikwm.com.

## Format berkas

Nama berkas = `<unique_id>.json` (mis. `indahjkt48.json`):

```json
{
  "account": { "unique_id": "indahjkt48", "nickname": "Indah JKT48", "sec_uid": "MS4wLjAB..." },
  "videos":  [ <item> ],
  "stories": [ <item> ]
}
```

`<item>` boleh memakai bentuk **tikwm** (`video_id`, `create_time`, `play`,
`images`, `content_desc`) atau bentuk **yt-dlp** (`id`, `timestamp`,
`webpage_url`, `thumbnail`) — `normalize_any()` memilih normalizer yang tepat
otomatis.

Item dengan kunci `images` (array) dianggap **postingan foto**: bot mengunduh
semua foto, mengirimnya ke channel Telegram sebagai album maksimum 10 foto per
part, lalu merangkainya menjadi `slide.mp4` untuk YouTube.

## Media lokal (opsional, untuk uji unduh/slide show)

Supaya uji media tidak butuh jaringan, tambahkan `fixture_media`:

```json
{
  "id": "uji-video-1",
  "kind": "video",
  "title": "video uji",
  "fixture_media": { "video": "C:/path/ke/sample.mp4" }
}
```

Untuk postingan foto, isi `images` dengan **path berkas lokal** (bukan URL) —
`bot/tiktok_media.py::download_images` menyalin berkas lokal dan mengunduh
hanya entri yang berupa URL. Test `tests/test_tiktok_media.py` dan
`tests/test_tiktok_monitor.py` membuat fixture seperti ini sementara di
direktori `tempfile` (dengan gambar/video sintetis dari ffmpeg).

## Catatan data

`indahjkt48.json` memakai ID, judul, dan tanggal **nyata** hasil pemeriksaan
21 Sep 2026 (mis. video `7685965837307596052` = "twinnie"), tetapi URL medianya
memakai domain `media.invalid` karena berkas aslinya tidak diunduh ke repo.
