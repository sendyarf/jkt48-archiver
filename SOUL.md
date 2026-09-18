# SEOUL.md — Sistem / Arsitektur Bot JKT48 Live

Dokumen ini menjelaskan arsitektur sistem **JKT48 Live Bot**. Baca ini sebelum
mengubah kode apa pun agar tidak merusak asumsi inti sistem.

## 1. Tujuan

Bot memantau live stream anggota **JKT48** di platform **IDN APP**, merekamnya,
lalu mengirim hasil rekaman ke **channel Telegram** sebagai satu video utuh —
termasuk saat live mengalami **lag / terputus / reconnect** (harus tetap digabung
menjadi satu video, bukan beberapa video terpisah).

## 2. Fakta Inti Platform IDN (PALING PENTING)

> **HLS URL (`playback_url`) bersifat STABIL per member, TIDAK PERNAH berubah.**
> Setiap kali seorang member live — dengan judul sama maupun berbeda, dengan
> slug berbeda — **HLS URL-nya tetap sama** (channel AWS IVS milik member itu).

Implikasi penting:
- `hls_url` = kunci identitas **member/channel**, bukan per-live.
- `slug` (→ `_live_id`) = kunci identitas **satu sesi live**. Berubah saat member
  memulai live baru (judul/slug baru), tetapi HLS URL tetap sama.
- `member_username` (contoh: `jkt48_lulu`) = kunci identitas **member** untuk
  pengelompokan merge.

Konsekuensi desain:
- **Deteksi reconnect** dapat memakai `hls_url` (stabil) sebagai sinyal bahwa
  stream member masih hidup.
- **Merge** di-key by `member_username`, sehingga pergantian slug (title baru)
  maupun reconnect (slug sama) tetap masuk ke grup merge yang sama.

## 3. Arsitektur Modul

```
bot/
├── main.py             # Orkestrator utama: loop polling, download, upload, reconnect
├── config.py           # Baca konfigurasi dari env / .env
├── idn_scraper.py      # Scrape live IDN via GraphQL publik (tools diagnostik)
├── hls_monitor.py      # HTTP health-check HLS member (deteksi live tiap 15 detik)
├── hls_discovery.py    # Auto-discovery HLS URL baru via IDN GraphQL
├── idn_lookup.py       # Lookup slug/judul live IDN (OPSIONAL, best-effort, never raises)
├── seed_hls.py         # Seed HLS URL + display name dari riwayat
├── downloader.py       # Rekam HLS via yt-dlp + ffmpeg; split_video; cancel_download
├── merger.py           # MergeManager: gabungkan segmen saat reconnect
├── database.py         # SQLite (live_sessions, merge_groups, member_hls, youtube_channels)
├── telegram_sender.py  # Upload video ke channel Telegram (Telethon userbot)
├── youtube_uploader.py # (tidak dipakai lagi) upload ke YouTube
├── status.py           # CLI status viewer + cleanup
├── member_manager.py   # Core kelola channel: add / stop / resume / remove / list
├── member_cli.py       # CLI kelola channel (python -m bot.member_cli ...)
└── admin_bot.py        # Telegram admin bot (BotFather): /list /stop /add /resume ...
```

## 4. Alur Utama

### 4.1 Polling & Deteksi
1. `_main_loop` memanggil `IDNScraper.get_jkt48_lives()` tiap `POLL_INTERVAL` detik.
2. Setiap live yang dikembalikan IDN:
   - `_live_id` = `slug`
   - `_stream_url` = `playback_url` (HLS, stabil)
   - `_member_username` = username creator
3. Jika `live_id` **baru** (Case C) → `insert_live` + `_download_and_upload`.

### 4.2 Reconnect (stream masih live, bot sempat berhenti merekam)
- Jika `live_id` **dikenal**, status **bukan** `done_telegram`/`done_youtube`, dan
  `hls_url` **tidak sedang** di-download → bot memicu segmen lanjutan
  `{live_id}_rc{N}` (terlepas dari status sebelumnya `failed` atau
  `download_complete`).
- Segmen lanjutan dikirim ke `MergeManager.add_segment` → masuk grup merge
  member tersebut.

### 4.3 Merge (kasus lag → reconnect → lag → reconnect)
1. `_download_and_upload` selesai merekam satu segmen → `MergeManager.add_segment`.
2. `add_segment` mencari grup merge aktif untuk `member_username` dalam window
   `MERGE_WINDOW_SECONDS` (dihitung dari **segmen terakhir**, `last_segment_at`).
   - Ada grup aktif → tambah segmen, **reset timer**.
   - Tidak ada → buat grup baru, mulai timer.
   - **Jeda antar segmen adalah satu-satunya penentu "masih live yang sama"**. Slug &
     judul IDN TIDAK dipakai untuk memutuskan split: member bisa mengedit judul di
     tengah live (jkt48_carissa), dan reconnect mempertahankan judul dengan slug baru
     (jkt48_daisy). `live_key`/`live_slug` hanya untuk caption & audit.
3. Saat timer menyala → `_decide` menentukan **finalize** atau **defer** dengan urutan:
   hard cap → rekaman aktif (defer) → IDN: member masih live? (defer; finalize hanya
   bila `MERGE_SPLIT_ON_TITLE_CHANGE=true` dan judul berubah) → HLS probe aktif
   (defer) → idle ≥ `MERGE_IDLE_FINALIZE_SECONDS` bila diaktifkan (default 0 =
   nonaktif) → tunggu sisa window. Catatan: "IDN tidak melihat live" TIDAK memicu
   finalize, karena jeda reconnect bisa puluhan menit.
4. Finalize → `_merge_and_upload`: concat segmen via ffmpeg (`-c copy`, fallback remux
   `+genpts`), lalu upload ke Telegram.

### 4.4 Upload
- File ≤ `TELEGRAM_SIZE_LIMIT_BYTES` (default ~1.9 GB) → kirim langsung ke Telegram.
- File > limit → `split_video` (ffmpeg segment muxer) jadi part, semua part di
  kirim ke Telegram. YouTube **tidak dipakai**.

## 5. Kelola Channel (Stop / Tambah)

Saklar pemantauan channel = **`member_hls.enabled`** (bukan `members.txt`):

- Loop utama memakai `get_all_member_hls(include_disabled=False)` → member
  `enabled = 0` tidak pernah di-probe maupun di-discovery ulang.
- `members.txt` hanya sumber "auto-register" (`INSERT ... DO NOTHING`), sehingga
  member yang di-stop tidak aktif kembali setelah restart.
- Semua operasi lewat `member_manager.py`:
  - `stop` → `enabled = 0` + marker `# STOPPED: <username>` di `members.txt`
  - `resume` → `enabled = 1` + baris aktif ditulis kembali
  - `add` → UPSERT + `enabled = 1` (+ HLS manual bila diberikan)
  - `remove` → hapus baris DB + hapus entri `members.txt`
- Antarmuka: `python -m bot.member_cli ...` dan Telegram admin bot
  (`python -m bot.admin_bot`, juga otomatis jalan di dalam `bot.main`).
- Stop rekaman **yang sedang berjalan** hanya bisa seketika dari Telegram admin
  bot: `cancel_download(live_id)` → SIGTERM graceful ke yt-dlp → segmen parsial
  tetap masuk merge group dan diupload.

## 6. Transisi Status DB (`live_sessions`)
`detected → downloading → download_complete → uploading_telegram → done_telegram`
(dan `failed` pada error; `download_complete` juga menandakan segmen menunggu merge).

## 7. Catatan Operasional
- Jalankan: `python -m bot.main` atau via PM2 `pm2 start "python -m bot.main" --name jkt48-archiver-bot`.
- DB & file rekaman disimpan di `DOWNLOAD_DIR` (default `/tmp/jkt48-lives`).
- File lokal dihapus hanya setelah upload **sukses penuh** (`uploaded_ok`).
- Test: `python -m unittest discover -s tests -t .`
