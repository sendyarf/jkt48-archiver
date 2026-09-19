# MEMORY.md — Fakta & Keputusan yang Harus Selalu Diingat AI

Dokumen ini berisi fakta, keputusan desain, dan aturan yang WAJIB dipegang saat
mengerjakan proyek ini. Jangan melanggar tanpa alasan yang jelas dan disepakati.

## Fakta Platform (JANGAN LUPA)

1. **HLS URL STABIL per member, tidak pernah berubah.**
   - `playback_url` milik channel AWS IVS member, sama untuk setiap live
     (judul/slug sama ataupun beda). Kunci identitas member, bukan per-live.
2. **`slug` = `_live_id`** berubah per sesi live (judul/slug baru).
3. **`member_username`** (mis. `jkt48_lulu`) = kunci merge & pelacakan.
4. **Slug DAN judul IDN TIDAK bisa dipakai sebagai identitas sesi live.** Dua bukti
   lapangan yang berlawanan arah:
   a) `jkt48_daisy` (13 Sep 2026) — **reconnect dengan JUDUL SAMA tapi slug BARU**,
      jeda sampai ±51 menit:
          `haii-260913192155` -> `haii-260913201248` -> `haii-260913201503`  (satu live)
   b) `jkt48_carissa` (15 Sep 2026) — **JUDUL BERUBAH di tengah live** (member mengedit
      judul), jeda hanya 1-8 menit, 4 slug & 3 judul berbeda:
          `my-bestie-ku-sini-join-...164353`
          `my-bestie-ku-sini-join-...165114`
          `sini-my-bestieee-...165258`
          `sini-my-bestie-akuu-...165451`                                   (satu live)
   ⇒ **Jangan** menyimpulkan "live baru" dari slug maupun judul. Yang andal hanya
     **JEDA WAKTU** (`MERGE_WINDOW_SECONDS` sejak segmen terakhir). Karena itu
     `MERGE_SPLIT_ON_TITLE_CHANGE=false` dan `MERGE_IDLE_FINALIZE_SECONDS=0` (default).
     `live_key` (judul) hanya dipakai untuk caption/log, bukan untuk memutuskan split.
5. **"IDN bilang tidak live" BUKAN berarti live sudah selesai.** Record live IDN
   dibuat ulang setiap (re)connect, jadi saat jeda reconnect member bisa "tidak live"
   selama puluhan menit padahal live-nya belum berakhir. Karena itu bot **tidak**
   memfinalize hanya karena IDN tidak melihat live; keputusan tetap berbasis window.

## Endpoint IDN yang Terbukti Jalan (2026-09)

- `getPublicProfileByUsername(username)` → `{uuid, username, name}` (uuid permanen, di-cache).
- `getLivestreams(streamerID: <uuid>, category:"all", page:1)` → live aktif member tsb.
  (`getLivestreams` juga menerima argumen `streamerID` selain `category`/`page`.)
  Catatan: record live di IDN **dibuat ulang tiap (re)connect** dengan slug baru —
  jadi daftar kosong berarti "stream sedang mati", bukan "live sudah selesai".
- `searchLivestream` **jangan dipakai** — tipe hasilnya `LivestreamSearchResult` (field
  berbeda, mengembalikan HTTP 400 kalau query slug/title).
- **IDN WAJIB opsional**: saat banyak member live, server IDN pernah down (GraphQL
  tidak bisa diakses). Semua lookup harus best-effort: `None` = tidak diketahui,
  tidak pernah raise, log level DEBUG, dan bot kembali ke HLS-only.

## Keputusan Desain Kunci

1. **Merge di-key by `member_username`**, bukan oleh slug/hls. Ini yang membuat
   reconnect dan pergantian title tetap tergabung menjadi satu video.
2. **Reconnect tidak bergantung hanya pada status `failed`.** Kapan pun stream
   masih live di IDN dan tidak sedang direkam → lanjutkan rekaman sebagai segmen
   (`live_id_rcN`), termasuk saat status sebelumnya `download_complete`.
3. **YouTube Unlisted & Notifikasi Telegram.** Semua video hasil rekaman diupload ke
   YouTube sebagai Unlisted (dengan rotasi multi-channel pool). Telegram hanya
   menerima pesan notifikasi berupa link YouTube unlisted (tidak mengupload file mentah ke TG).
4. **File lokal dihapus hanya jika upload sukses penuh.** Kalau upload gagal atau kuota
   YouTube habis, file sumber dibiarkan di VPS dengan status `pending_upload` agar
   bisa di-retry otomatis setelah reset kuota harian.
5. **Inactivity timeout** pada `download_stream` (120s tanpa progres) mematikan
   yt-dlp → ini TIDAK dianggap error fatal; segmen parsial tetap masuk merge.
6. **Upload YouTube Sekuensial (Satu per Satu).** Upload video dijalankan berurutan
   via lock untuk menjaga stabilitas bandwidth server VPS, mencegah gangguan paket data
   pada rekaman live yang sedang berjalan (yt-dlp), dan menjaga akurasi kuota channel pool.

## Aturan Anti-Regresi (saat mengubah kode)

- **JANGAN membandingkan kolom waktu SQLite dengan `datetime.now()`.**
  `created_at`, `last_segment_at` diisi SQLite `datetime('now')` = **UTC**,
  sedangkan Python `datetime.now()` = waktu lokal (WIB = UTC+7) → selisih salah 7
  jam (pernah menyebabkan hard cap menyala instan di semua kasus). Gunakan
  `database.get_merge_group_timing()` yang menghitung selisihnya di SQL (UTC vs UTC).
  (Kolom yang ditulis Python seperti `download_ended_at`/`started_at` tetap waktu lokal.)
- **Window merge dihitung dari SEGMEN TERAKHIR**, bukan `created_at` grup
  (`get_active_merge_group` memakai `COALESCE(last_segment_at, created_at)`).
  Ini yang menjaga "satu live = satu video" untuk live panjang yang reconnect.
- **Finalize tidak boleh memotong rekaman aktif** — selalu defer selama
  `_active_downloads[member]` ada.
- **Split hanya boleh berbasis JEDA WAKTU.** Slug & judul IDN TIDAK boleh dijadikan
  penentu "live baru": member mengedit judul di tengah live (jkt48_carissa) dan
  reconnect mempertahankan judul dengan slug baru (jkt48_daisy). Biarkan
  `MERGE_SPLIT_ON_TITLE_CHANGE=false` dan `MERGE_IDLE_FINALIZE_SECONDS=0` sebagai
  default; jangan menurunkannya tanpa bukti baru.
- Jangan buat segmen baru di-upload terpisah saat member sedang reconnect.
- Jangan hapus file rekaman sebelum upload 100% sukses.
- Jangan andalkan slug untuk menentukan "member yang sama" — pakai
  `member_username` / `hls_url`.
- Status DB `download_complete` bisa berarti "menunggu merge", bukan selesai
  kirim. Jangan asumsikan `download_complete` = sudah terkirim.
- Jaga logika `_active_downloads` / `_active_hls_urls` agar tidak ada duplikasi
  download untuk URL yang sama.
- **Konten pra-rilis harus UTUH di grid home.** `getUpcomingVideos()` mengembalikan
  SEMUA rekaman yang belum lewat ambang (`LIMIT -1`, urut terbaru lebih dulu) dan
  mengikuti filter `member`/`platform`; hanya pencarian kata kunci (`q`) yang murni.
  Batas kecil + urut `publish_at ASC` pernah membuat rekaman pra-rilis TERBARU tidak
  pernah muncul di grid. Kartu "Segera" dipasang di halaman 1 saja (anti-duplikat).
- **Ambang per platform dibaca NILAI-nya, bukan hanya tandanya.**
  `AUTO_PUBLISH_AFTER_HOURS_SHOWROOM`: 0 = langsung tampil (default, tanpa jeda),
  positif = tunggu N jam (muncul sebagai pra-rilis berjadwal), negatif = wajib
  persetujuan admin. Jangan kembali ke pola `>= 0 ? '1' : '0'`.
- **Label platform di pesan Telegram harus mengikuti kolom `platform`, jangan
  di-hardcode.** `build_telegram_video_caption()` dan `build_youtube_notification()`
  memakai `_platform_label(platform)` → header "IDN LIVE REPLAY" / "SHOWROOM LIVE
  REPLAY". Pemanggil wajib meneruskan platform (`plat` di `bot/main.py`,
  `item["platform"]` di `bot/upload_pending.py`); tanpa itu rekaman Showroom
  dilabeli IDN (regresi 19 Sep 2026: Heidi JKT48).
- **Istilah UI publik = "replay", bukan "rekaman"/"siaran ulang".** Kartu, filter,
  empty state, metadata SEO, dan halaman Tentang memakai kata "replay" agar
  seragam dengan nama situs (JKT48 Replay). Kata "arsip" tetap dipakai untuk
  menyebut koleksi secara keseluruhan. Perubahan teks ini ikut memengaruhi assert
  di `web/verify/home-upcoming-grid.mjs` (mis. "replay tersedia",
  "Replay tidak ditemukan").
- **Status HTTP rute dinamis Next 16.3.5 tidak bisa dipakai untuk menguji 404/redirect.**
  `notFound()` dan `redirect()` di rute `[id]` mengembalikan **200** (isi halaman
  404/login-nya benar dan tidak membocorkan data); hanya rute statis yang benar 404.
  Skrip `web/verify/*.mjs` harus menilai **konten** (mis. `countdown-panel`,
  `video-player-container`), bukan `res.status === 404`. Karena ini:
  `public-private-check.mjs` (masih memakai `location`/`status 404` untuk
  `/watch/...`) sudah **stale** dan belum diperbarui.

## Sumber Kebenaran Pemantauan Channel (WAJIB DIINGAT)

1. **`member_hls.enabled` = satu-satunya saklar channel.** Loop utama memanggil
   `database.get_all_member_hls(include_disabled=False)`, jadi:
   - `enabled = 1` → di-probe & direkam
   - `enabled = 0` → dilewati total (tidak di-probe, tidak di-discovery ulang)
2. **`members.txt` BUKAN daftar aktif-monitor.** Fungsinya hanya (a) daftar
   username yang di-`INSERT` saat sync, dan (b) whitelist untuk tool diagnostik
   (`idn_scraper` / `test_connections`). Menghapus baris di `members.txt`
   **tidak** menghentikan rekaman channel tersebut.
3. **`sync_members_whitelist()` hanya menambah** (`INSERT ... ON CONFLICT DO
   NOTHING`), sehingga member `enabled = 0` tidak akan hidup kembali setelah
   restart. Jangan mengubahnya menjadi UPSERT yang men-set `enabled = 1`.
4. **Jangan menghapus baris `member_hls` untuk sekadar stop.** Cukup
   `enabled = 0`; HLS URL tetap tersimpan sehingga resume instan. Menghapus baris
   membuat HLS di-discovery ulang (dan berisiko muncul kembali).
5. **Marker `# STOPPED: <username>`** di `members.txt` ditulis otomatis oleh
   `member_manager` saat stop. Hanya baris polos/marker ini yang dikenali sebagai
   entry member — komentar bebas milik manusia tidak pernah disentuh.
6. **Kelola channel lewat `bot/member_manager.py`** (dipakai `member_cli.py` dan
   `admin_bot.py`). Jangan menduplikasi logika add/stop di tempat lain.

## Kesalahan masa lalu yang sudah diperbaiki

1. **Merge di-upload sebelum live selesai** — timer merge tidak ter-reset saat
   rekaman baru dimulai. Diperbaiki dengan `download_started()` yang membatalkan
   timer & menunda merge selama masih ada rekaman berjalan.
2. **Reconnect tidak terpicu setelah download parsial** — karena hanya mengecek
   `status == "failed"`, padahal lag menghasilkan `download_complete`. Diperbaiki
   dengan memicu reconnect selama stream masih live di IDN.
3. **Pesan "Uploading to YouTube" menyesatkan** — ganti menjadi pesan split-to-
   Telegram yang akurat (`uploading_split`).
4. **Data hilang saat upload part gagal** — sekarang part/sumber gagal disimpan
   dan di-retry otomatis saat restart.
5. **Segmen reconnect gagal "Output file not found"** — reconnect mulai merekam
   saat stream member belum mengirim segmen (baru lepas dari lag), yt-dlp keluar
   tanpa file. Diperbaiki dengan retry+backoff (5x, delay 10s) di `download_stream`
   dan `--wait-for-video 15`, sehingga segmen tertangkap begitu stream kembali.
6. **Merge timer HILANG saat reconnect gagal (bug paling kritis)** —
   `download_started()` sebelumnya membatalkan timer merge. Jika rekaman reconnect
   lalu GAGAL, `add_segment()` tak pernah dipanggil → tidak ada timer baru → grup
   `waiting` selamanya → segmen tak pernah di-merge/dikirim. Diperbaiki dengan
   TIDAK membatalkan timer di `download_started()`; deferral di `_finalize_group`
   sudah mencegah upload tengah-rekaman. Ditambah `recover_stuck_groups()` di boot
   untuk menyelesaikan grup `waiting` yang macet akibat bug lama.
7. **Digabung saat segmen berikutnya masih direkam (race tersisa)** — `download_started`
   sebelumnya dijalankan async di dalam task, sehingga ada jendela rawan timer merge
   menyala sebelum rekaman baru terdaftar. Diperbaiki: `download_started()` kini
   dipanggil **sinkron saat spawn** di `_main_loop` (bukan di dalam task).
8. **Cleanup tidak jalan saat download gagal** — `finally` hanya menutup bagian
   upload, jadi pada kegagalan download, `_active_hls_urls`/`_active_downloads`
   tidak dilepas dan `download_ended` tak dipanggil → deteksi reconnect macet.
   Diperbaiki: `finally` kini menutup seluruh fungsi (sukses/gagal/upload/cancel),
   sehingga registrasi aktif selalu dilepas.
9. **Double-spawn reconnect saat duplikat di satu poll** — `_active_hls_urls.add`
   dulu dilakukan async di dalam task, sedangkan guard `_active_downloads` memakai
   live_id dasar (bukan `_rcN`). Jika IDN mengembalikan member yang sama dua kali
   dalam satu daftar, bot bisa membuat 2 download reconnect paralel. Diperbaiki:
   `_active_hls_urls.add(hls_url)` dilakukan **sinkron saat spawn**, plus dedup
   oleh slug di `_collect_jkt48`.
10. **GraphQL API IDN flaky (500 / network error)** — `api.idn.app/graphql` sering
    gagal sementara. `get_all_lives` kini melakukan **retry+backoff** (3x) pada
    error transient, sehingga satu kegagalan tidak membuat poll kosong dan
    melewatkan/menghentikan rekaman. Catatan: homepage HTML TIDAK dipakai sebagai
    fallback karena datanya dimuat via JS (SSR kosong pada request polos).
11. **Query merge_segments mengabaikan status segment_done** — `get_merge_segments`
    sebelumnya mencari `WHERE status = 'download_complete'`, sedangkan sesi baru
    disimpan dengan status `segment_done`. Akibatnya merge group dianggap kosong dan
    tidak pernah di-upload ke YouTube. Diperbaiki: query diubah ke
    `status IN ('segment_done', 'download_complete')`, ditambah auto-adoption untuk
    segmen tanpa grup (*orphaned segments*), dan timer recovery cerdas berbasis sisa waktu asli.
