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
- **Thumbnail YouTube video BARU = kolase 3x2 via ffmpeg, bukan frame otomatis.**
  `bot/thumbnail_collage.py::build_collage()` mengambil 6 frame tersebar merata
  (margin 5% tiap ujung), tiap frame di-scale+crop "cover" ke lebar kolomnya lalu
  xstack jadi **PERSIS 1280x720** (syarat thumbnail YouTube). 1280 tidak habis
  dibagi 3 → lebar kolom `COLUMN_WIDTHS = [426, 426, 428]` (kolom kanan menyerap
  sisa 2 px); jangan kembalikan satu `CELL_WIDTH` untuk semua kolom (dulu 426x3 =
  1278 → pilar hitam di kanan, insiden 21 Sep 2026);
  `YouTubeChannelPool.set_thumbnail()` memasangnya (~50 kuota).
  Best-effort: gagal (ffmpeg hilang / API error) → warning saja, upload tetap
  sukses. Saklar `THUMBNAIL_COLLAGE_ENABLED` (default true). Video LAMA tak
  disentuh. JANGAN kembalikan logika `pick_sample_times` ke while-loop mundur
  1 detik — itu infinite-loop untuk video < ~8 detik (insiden 21 Sep 2026).
- **Hero portal = poster film full-bleed satu panel (`hero-feature`), BUKAN dua
  kolom dan BUKAN panel teks + kalimat pemasaran.**
  Markup `web/components/PublicCatalog.tsx`: `.catalog-hero-block` > `<HeroSpotlight>`
  → `.hero-feature`. Thumbnail replay terbaru mengisi SELURUH panel sebagai latar
  (`.hero-feature-img`, `next/image` fill, TAJAM tanpa blur) + scrim gelap
  (`.hero-feature-scrim`); isi ditumpuk di atasnya (`.hero-feature-body`): kicker
  `JKT48 REPLAY · <BARU RILIS|REPLAY TERBARU|SEGERA HADIR>`, H1 = judul replay apa
  adanya (`buildDisplayTitle` → "LIVE IDN <MEMBER> - <tanggal> | <jam> WIB"), meta
  platform·durasi, lalu dua tombol — "Tonton sekarang" (`.primary-button`) dan
  "Lihat semua replay" (`.secondary-button` → `#catalog`). Hero hanya tampil di halaman
  depan tanpa filter; sumber = `result.videos[0]` halaman 1 tanpa filter → nol query
  tambahan, dan URL-nya harus sama persis dengan `VideoCard`
  (`watch_id || youtube_video_id || id`). Judul hero sekaligus `h1` halaman; saat
  hero tidak tampil, `h1` jatuh ke judul section katalog.
  Breakpoint: 900px (min-height dilepas, scrim tegak bawah→atas), 720px (tombol hero
  full-width). JANGAN hidupkan lagi: hero dua kolom `.hero-band`/`.hero-card`,
  panel ringkasan arsip (`.hero-note`), atau kalimat semboyan di hero
  ("Momen favorit. Bisa ditonton lagi.") — pengguna minta hero mengikuti gaya hero
  film dan hanya memuat informasi nyata dari replay.
- **Bahasa UI = santai khas fandom, sapaan "kamu", istilah publik "replay".**
  Kartu, filter, empty state, metadata SEO, dan halaman Tentang memakai kata "replay"
  agar seragam dengan nama situs (JKT48 Replay); "rekaman"/"siaran ulang" TIDAK
  dipakai di UI publik (admin juga sudah diseragamkan ke "replay"). Kata "arsip"
  tetap untuk koleksi secara keseluruhan. Panggil pengunjung dengan "kamu" — kata
  "Anda" dilarang di UI publik — dan istilah Inggris yang natural
  (replay/live/update/download) boleh dicampur. Penanda status: "tayang"/"rilis",
  bukan "terbit"/"unggah". Kalimat contoh yang dipakai sekarang: "N replay siap
  ditonton", "Belum ada replay yang cocok", "Terbaru dulu", tombol filter "Cari".
  Assert yang mengikuti teks ini: `web/verify/home-upcoming-grid.mjs`
  (`replay siap ditonton`, `Belum ada replay yang cocok`) dan
  `web/verify/portal-browser-check.mjs` (regex `(\d+)\s+replay siap ditonton`, plus
  penjaga anti-regresi `/\bAnda\b/` dan `/siaran ulang/i` di `/`, `/members`,
  `/about`). Setiap kali teks UI diubah, cari dulu string lama di `web/verify/*`.
- **Latar hero diambil dari YouTube, jadi uji browser menunggu gambar termuat.**
  `web/verify/portal-browser-check.mjs` menunggu `.hero-feature-img` `complete`
  maks 5 detik sebelum assert; tanpa itu uji ini sempat gagal acak
  ("Latar hero gagal dimuat") saat jaringan lambat.
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

## Arsip TikTok (fitur 21 Sep 2026)

Saklar utama: **`TIKTOK_ENABLED` (default `false`)**. Selama false, bot berjalan
persis seperti sebelumnya — jangan pernah membuat jalur TikTok aktif tanpa flag
ini, dan jangan menaruh kode TikTok di jalur IDN/Showroom.

1. **Tabel & bentuk data.** `tiktok_accounts` (`unique_id` kanonik =
   huruf kecil tanpa `@`, `sec_uid`, `enabled`, `member_username`) dan
   `tiktok_posts` (`kind` = `video`/`photo`, `is_story`, `image_count`,
   `images_json`, `telegram_message_ids` untuk SEMUA pesan media di channel
   arsip, `youtube_video_id`, `visible`, `status`). Bot yang menulis; web hanya
   membaca (`web/lib/tiktok.ts`), dan `web/lib/db.ts` juga membuat tabelnya agar
   halaman tidak 500 pada database baru.
2. **Aturan tampil di `/tiktok`:** `visible = 1` **DAN** (punya
   `youtube_video_id` ATAU `telegram_message_ids`). Arsip yang masih diproses
   tidak boleh muncul (mencegah kartu/pemutar kosong). Akun `enabled = 0` tidak
   muncul di sidebar.
3. **Foto > 10 = beberapa part.** Album Telegram maksimum 10 media, jadi
   `TIKTOK_PHOTOS_PER_PART` dipotong ke 10 oleh `split_image_paths()`. Foto
   dikirim APA ADANYA ke channel arsip (agar user bisa mengunduh **fotonya**);
   `slide.mp4` (1080x1920, H.264) hanya untuk YouTube + thumbnail kolase.
   Demuxer `concat` ffmpeg **mengabaikan `duration` entri terakhir**, karena itu
   foto terakhir ditulis dua kali di daftar concat — jangan dihapus.
4. **Retry tidak mengunduh ulang.** Saat status `pending_upload`,
   `tiktok_media.media_from_disk()` dipakai lebih dulu; media baru dihapus setelah
   semua upload sukses (`AUTO_DELETE_AFTER_UPLOAD`).
5. **Urutan status:** `detected → downloading → uploading_telegram →
   uploading_youtube → done`, dengan `pending_upload` (bisa di-retry) atau
   `failed` (mis. media hilang di TikTok). Telegram WAJIB lebih dulu daripada
   YouTube: channel arsip adalah sumber unduhan publik.
6. **Penyedia data `auto` = tikwm → embed → yt-dlp.** Fakta uji 21 Sep 2026:
   - **`curl_cffi` wajib** (`impersonate=chrome131`); tanpa itu tikwm & halaman
     embed TikTok sama-sama 403 dari IP datacenter.
   - tikwm gratis ±1 request/detik & kuota harian ±10.000; `/user/posts` bisa
     403 sementara `/user/story` 200 → kesehatan dicatat PER KAPABILITAS.
   - **Story hanya dari tikwm** (`/api/user/story`, path TUNGGAL); halaman embed
     tidak punya story, dan yt-dlp juga tidak.
   - Halaman **embed** = sumber listing paling andal (10 post terbaru; foto =
     tanpa `playAddr`), dan `/embed/v2/<id>` memberi tanggal, durasi, dan daftar
     foto carousel (sekalian jadi URL segar bila CDN tikwm kedaluwarsa).
   - yt-dlp tetap dipakai sebagai cadangan; listing profilnya butuh secUid.
   Kegagalan penyedia = tandai "tidak sehat" 15 menit per kapabilitas lalu pindah
   penyedia; tidak boleh melempar exception ke loop utama.
7. **Deep-link bot publik:** arsip TikTok = `tt_<post_id>` (replay tetap YouTube
   ID). Tombol download mengekspos payload lewat atribut
   `data-download-payload` supaya bisa diverifikasi tanpa membuka modal.
8. **Jangan menguji payload lewat `start=` di HTML** — deep-link dibentuk di
   dalam modal (state klien) sehingga tidak ada di hasil SSR. Uji lewat
   `data-download-payload` (HTML) atau `download_payload` (API).
9. **Thumbnail web diutamakan dari YouTube**
   (`https://img.youtube.com/vi/<id>/hqdefault.jpg`) karena URL cover TikTok CDN
   bertanda tangan dan kedaluwarsa; `cover_url` hanya fallback (dan
   `next.config.ts` perlu `**.tiktokcdn.com` di `images.remotePatterns`).
10. **Layout `/tiktok` = 3 kolom** (kiri daftar akun · tengah pemutar · kanan
    daftar arsip) lewat `.tiktok-shell`; ≤1100px menjadi 2 kolom dan ≤820px
    menjadi 1 kolom. `web/verify/portal-browser-check.mjs` menguji JUMLAH kolom
    per lebar, sedangkan `web/verify/tiktok-page.mjs` menguji isinya (akun aktif
    saja, arsip siap saja, urut terbaru, payload `tt_`, label foto/story).
11. **Insiden 21 Sep 2026 — metode penyedia tertimpa stub.** Saat menyunting
    `bot/tiktok_client.py`, blok stub `BaseProvider` (`fetch_user_posts` /
    `fetch_user_stories` / `close`) dan badan `mark_unhealthy` ikut tersisip ke
    dalam `TikwmProvider`; Python memakai definisi TERAKHIR sehingga implementasi
    asli tertimpa stub (`NotImplementedError`) dan `mark_unhealthy` kehilangan
    log-nya. Semua unit test LOLOS karena hanya memakai `FixtureProvider`.
    Pelajaran: setelah menyunting berkas besar dengan beberapa penyisipan,
    jalankan `python -m pyflakes bot\tiktok_*.py` (menangkap
    `redefinition of unused` + `undefined name`) dan pastikan
    `tests/test_tiktok_client.py::TestProviderContract` tetap hijau — test itu
    membandingkan tiap kelas turunan dengan stub `BaseProvider`.
12. **Story TikTok = `/api/user/story` (TUNGGAL).** `/api/user/stories` menjawab
    **404** dan itu kesalahan awal yang membuat story tidak pernah terambil.
    Respons memakai `hasMore` (camel) untuk paginasi → `_next_cursor()` menerima
    `hasMore` maupun `has_more`. Story diambil sebagai permintaan TERPISAH dari
    listing, dan kesehatan penyedia dicatat **per kapabilitas**
    (`is_healthy("posts")` / `is_healthy("stories")`) karena Cloudflare memblokir
    per-path: 21 Sep 2026 tikwm 403 di `/user/posts` tetapi 200 di `/user/story`.
    Jangan pernah kembali ke satu flag `is_healthy()` global untuk semua
    kapabilitas — story akan hilang tiap kali listing diblokir.
13. **`curl_cffi` + `impersonate=chrome131` WAJIB untuk arsip TikTok.** Tanpa
    itu, tikwm.com **dan** halaman embed TikTok menjawab 403 Cloudflare dari IP
    datacenter; dengan itu keduanya 200 (dibuktikan dari mesin yang sama).
    `bot/tiktok_client.py` memakai lapisan `_sync_request` (curl_cffi) yang
    dipanggil lewat `asyncio.to_thread`, dengan httpx hanya sebagai fallback bila
    curl_cffi tidak terpasang. curl_cffi terdaftar di `requirements.txt`.
14. **Metode dari proyek `JKT48_TIKTOK` yang diadopsi** (dan alasannya):
    - **halaman embed profil** `tiktok.com/embed/@user` → `videoList` (10 post
      terbaru) sebagai sumber LISTING paling andal; halaman profil biasa
      `tiktok.com/@user` hanya stub sehingga yt-dlp gagal ("Unable to extract
      secondary user ID"). Postingan FOTO dikenali dari **ketiadaan `playAddr`**.
    - **halaman embed per-post** `tiktok.com/embed/v2/<id>` → `itemInfos`
      (createTime, video.urls, videoMeta.duration) + `imagePostInfo.displayImages`
      (daftar foto carousel). Dipakai untuk (a) melengkapi tanggal/jumlah foto
      postingan baru, (b) URL video segar saat URL CDN tikwm kedaluwarsa (403).
      Retry 3× backoff karena halaman ini sering membalas **503** sementara.
    - **deteksi foto** `duration == 0 && size == 0` sebagai pelengkap (bukti
      terkuat tetap keberadaan `images`) → `looks_like_photo()`.
15. **Daftar `ffconcat` WAJIB memakai path ABSOLUT.** Demuxer `concat` ffmpeg
    menyelesaikan path relatif terhadap **lokasi berkas daftar**, bukan CWD;
    `DOWNLOAD_DIR` relatif (mis. `tmp/...`) membuat semua slide show gagal
    dengan "No such file or directory" (insiden nyata 21 Sep 2026).
    `_ffconcat_escape()` memakai `resolve().as_posix()`; jangan kembalikan
    `as_posix()` tanpa `resolve()`.

16. **Deploy: interpreter bot & PEP 668 (insiden 21 Sep 2026).**
    - `pip` sistem Ubuntu 23.04+/Debian 12+ dikunci PEP 668
      (`error: externally-managed-environment`). Dependensi bot **harus** di venv;
      jangan pakai `--break-system-packages` (merusak paket Python milik apt).
    - **Nama venv kanonik `.venv`**, tetapi `deploy/ecosystem.config.js` menerima
      urutan: `BOT_PYTHON` (env) → `.venv/bin/python` → `venv/bin/python` →
      `.venv|venv/Scripts/python.exe` → `python3` sistem. Sebelum perbaikan,
      berkas itu **hanya** mencari `.venv` sementara README menyuruh membuat
      `venv`, sehingga pengikut README menjalankan bot dengan `python3` sistem dan
      bot mati dengan `ModuleNotFoundError`.
    - Verifikasi interpreter: `pm2 describe jkt48-archiver-bot` → `script path`.
      Venv di luar repo bisa dipaksa: `BOT_PYTHON=/path/python pm2 restart
      jkt48-archiver-bot --update-env`.
    - `curl_cffi` harus ada di interpreter **yang dipakai PM2**, bukan hanya di
      python yang dipakai menguji manual — kalau tidak, fitur TikTok selalu 403.

17. **PM2: `restart` tidak membaca ulang `ecosystem.config.js`.** Nilai `script`,
    `interpreter`, dan `cwd` disimpan saat proses **dibuat**; `pm2 restart`
    (termasuk `--update-env`) hanya menyegarkan environment, bukan path. Untuk
    perubahan `script`/interpreter: `pm2 delete <app>` → `pm2 start
    deploy/ecosystem.config.js --only <app>` → `pm2 save`. (Issue PM2 #3742.)
    Ini penting untuk arsip TikTok: kalau `.venv` baru dibuat setelah bot
    berjalan, `curl_cffi` tidak akan terlihat walau `restart` sudah dilakukan.

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
12. **Kolase thumbnail keluar 1278x720 (pilar hitam di kanan)** — `CELL_WIDTH` tunggal
    (426) dikali 3 kolom = 1278, jadi `xstack` menyisakan 2 px kosong; YouTube menolak
    thumbnail yang tidak persis 1280x720. Diperbaiki dengan lebar per kolom
    `[426, 426, 428]` (kolom kanan menyerap sisa) dan diverifikasi nyata lewat ffmpeg
    (`tmp/check-collage.py`, sumber 1080x1920 → output 1280x720).
