# TASKS.md — Status & Pekerjaan Berikutnya

Status live proyek. Perbarui bagian ini setiap ada perubahan penting.

## Status Terakhir (berdasarkan kode saat ini)

- [x] Deteksi live JKT48 via GraphQL publik IDN (tanpa login).
- [x] Rekam HLS langsung via **ffmpeg** (fragmented MP4; yt-dlp hanya dipakai
      untuk media TikTok).
- [x] Auto-merge segmen saat member reconnect (MergeManager,
      `MERGE_WINDOW_SECONDS` default **3600s** dihitung dari jeda liputan antar segmen).
- [x] Merge ditunda selama masih ada rekaman berjalan (`download_started`).
- [x] Reconnect dipicu selama stream masih live di IDN (bukan hanya status failed).
- [x] Upload ke Telegram; file > ~1.9 GB dipecah jadi part.
- [x] **YouTube adalah tahap WAJIB pipeline**: setiap live diupload Telegram
      (arsip/download) → YouTube Unlisted (playback website). Kuota habis → file
      tetap di VPS, hanya tahap YouTube masuk antrean retry.
- [x] File sumber disimpan & di-retry saat restart bila upload gagal.
- [x] Pesan laporan akurat (split-to-Telegram, bukan "Uploading to YouTube").
- [x] Retry download bila yt-dlp tidak menghasilkan file (mis. reconnect saat
      stream belum mengalir) agar segmen tetap tertangkap & masuk merge.
- [x] Penomoran segmen reconnect (`_rcN`) benar; hapus pesan live_detected ganda.
- [x] Fix bug kritis: `download_started()` tidak lagi membatalkan timer merge,
      sehingga timer tidak hilang saat reconnect gagal → segmen pasti di-merge.
- [x] `recover_stuck_groups()` di boot untuk menyelesaikan grup `waiting` macet.
- [x] `download_started()` dipanggil sinkron saat spawn → tidak ada race timer
      merge menyala saat segmen berikutnya masih direkam.
- [x] Cleanup (`download_ended`, `_active_hls_urls`, `_active_downloads`) kini
      selalu jalan (try/finally seluruh fungsi) termasuk saat download gagal.
- [x] Dedup live by slug di `_collect_jkt48` + `_active_hls_urls.add` sinkron
      saat spawn → tidak ada double-spawn download untuk member yang sama.
- [x] Retry+backoff (3x) pada GraphQL scrape saat error transient (500/network),
      agar poll yang gagal tidak membuat rekaman live terlewat/terhenti.
- [x] **Kelola channel (add/stop/resume/remove/list)**: kolom `member_hls.enabled`
      + `bot/member_manager.py` (core), `bot/member_cli.py` (CLI),
      `bot/admin_bot.py` (Telegram admin bot) — listener otomatis jalan di
      `bot/main.py` dan hanya menerima ADMIN_CHAT_ID/TELEGRAM_ADMIN_IDS.
- [x] **Stop rekam seketika**: `/stop` di Telegram → `cancel_download()` (SIGTERM
      graceful ke yt-dlp) sehingga segmen yang sudah terekam tetap ikut merge & upload.
- [x] **Notifikasi progres live ke admin**: `ADMIN_LIVE_NOTIFY_ENABLED` (default
      true) → record (mulai/segmen selesai/resume/gagal, IDN & Showroom), merge
      (mulai/selesai/gagal via `MergeManager(on_notify=...)`), dan upload
      (Telegram/YouTube per tahap, kuota habis, file hilang). Dikirim lewat admin
      bot (`notify_admins`) atau fallback userbot ke `ADMIN_CHAT_ID`; semua
      best-effort sehingga tak pernah menahan pipeline. Tes:
      `tests/test_live_admin_notifications.py` (16 tes).
- [x] `members.txt` disinkronkan otomatis (entry baru / marker `# STOPPED:`), dan
      `get_members_without_hls()` kini mengabaikan member `enabled = 0` agar channel
      yang di-stop tidak di-discovery ulang.
- [x] Unit + integration test: `tests/test_member_manager.py`,
      `tests/test_admin_bot_commands.py`, `tests/test_main_integration.py`
      (45 test, jalankan: `python -m unittest discover -s tests -t .`).
- [x] **Fix bug kritis merge window**: `get_active_merge_group()` dulu menghitung
      window dari `created_at` (pembuatan grup) sehingga live panjang (> 1 jam) dengan
      reconnect pecah jadi beberapa video. Sekarang dihitung dari **segmen terakhir**
      (`COALESCE(last_segment_at, created_at)`) + test regresi.
- [x] **Fix bug zona waktu**: kolom SQLite (`created_at`/`last_segment_at`) memakai UTC
      sedangkan perbandingan memakai waktu lokal → selisih 7 jam membuat hard cap
      menyala instan. Kini dihitung di SQL via `get_merge_group_timing()`.
- [x] **Percepatan upload**: finalize lebih cepat bila IDN bilang tidak live + HLS
      offline, atau idle ≥ `MERGE_IDLE_FINALIZE_SECONDS` (default 30 mnt) — upload
      rata-rata ±10–30 mnt setelah live selesai, bukan menunggu 1 jam penuh.
- [x] **Live baru vs reconnect**: slug sesi IDN (`haii-260915223455`) disimpan di
      `live_sessions.live_slug` & `merge_groups.live_slug`. Slug beda → live baru =
      video terpisah. IDN bersifat OPSIONAL (best-effort, `idn_lookup.py`).
- [x] **Hard cap** `MERGE_MAX_GROUP_HOURS` (6 jam) untuk stream "hang" tanpa disconnect.
- [x] **Merge lebih tahan gangguan**: segmen file hilang/0 byte dilewati (ditandai
      `failed`), concat ffmpeg 2 varian (`-c copy` → remux `+genpts`).
- [x] **`pm2 restart` aman**: graceful stop rekaman (SIGTERM ke yt-dlp + tunggu ±25s)
      sehingga segmen parsial masuk merge group, plus `try/finally` anti yt-dlp orphan.
- [x] Test tambahan: `tests/test_merge_reconnect.py` (18) & `tests/test_idn_lookup.py` (7)
      → total **70 test**, semuanya hijau.
- [x] **KOREKSI KEDUA (temuan lapangan paling penting)**: judul juga TIDAK andal.
      `jkt48_carissa` (15 Sep 2026) **mengedit judul 3x di tengah satu live** —
      4 slug & 3 judul berbeda dalam 11 menit, jeda 1-8 menit → tetap SATU live.
      Bersama kasus daisy (judul sama, slug baru, jeda 51 menit) dapat disimpulkan:
      **slug maupun judul TIDAK bisa dipakai untuk menyimpulkan "live baru".**
      Satu-satunya penentu andal = **jeda waktu** (`MERGE_WINDOW_SECONDS`).
- [x] `MERGE_SPLIT_ON_TITLE_CHANGE` **default false** (opsional, tidak direkomendasikan):
      kalau aktif, live Carissa akan terpecah 3 video. `live_key` hanya untuk caption/log.
      Regresi: `tests/test_merge_reconnect.py::TestCarissaTitleEditRegression`.
- [x] Judul & slug tetap dikumpulkan hanya untuk caption/audit, bukan keputusan split.
- [x] **`MERGE_IDLE_FINALIZE_SECONDS` default 0 (nonaktif)**: percepatan idle 30 menit
      akan memecah kasus daisy (jeda 51 menit). Nyalakan hanya bila rela menerima risiko itu.
- [x] "IDN bilang tidak live" **tidak** lagi memicu finalize (record IDN dibuat ulang
      tiap reconnect, jadi itu bukan bukti live berakhir).
- [x] Regresi Daisy: `tests/test_merge_reconnect.py::TestDaisyReconnectRegression`
      (3 slug judul sama ⇒ SATU grup; judul beda ⇒ dua video; split bisa dimatikan).
      Total **83 test** hijau.
- [x] **Fitur "Download via Bot Telegram"**: dual-upload YouTube + channel arsip
      Telegram privat (`TELEGRAM_ARCHIVE_CHANNEL_ID`), simpan semua message_id ke
      kolom baru `live_sessions.telegram_message_ids`. Bot publik `bot/replay_bot.py`
      (long polling httpx, pola AdminBot) menjawab deep-link `/start <youtube_video_id>`
      lalu `copyMessage` dari channel arsip (tanpa re-upload). Tombol Download + modal
      di `web/app/watch/[id]/page.tsx` (hanya tampil bila `telegram_archived`), link
      `t.me/<REPLAY_BOT_USERNAME>?start=<youtube_video_id>`. File lokal hanya dihapus
      setelah YouTube DAN arsip Telegram sukses; gagal salah satu → `pending_upload`.
      Test: `tests/test_replay_bot.py` → total **202 test** hijau.
- [x] **Upgrade UI/UX web (compact + istilah seragam)**: navbar 72→58px, hero
      dipadatkan dan panel hiasan diganti **kartu ringkasan** (jumlah replay terbit
      & member), grid kartu 300→215px (5 kolom di 1440px), kartu lebih pendek (meta
      1 baris, badge lebih kecil), filter/member/footer/watch/sidebar dipadatkan,
      tombol full-width di mobile. Istilah diseragamkan: **"rekaman"/"siaran ulang"
      → "replay"** di kartu, filter, empty state, metadata SEO, halaman Tentang,
      serta klaim privasi font diperbarui (self-hosted, ui-avatars dihapus).
      Verifikasi: `portal-browser-check.mjs` hijau di 360/390/768/1024/1440px
      (tidak ada overflow), kedua skrip verify lain hijau.
- [x] **Pencarian tanggal menyesuaikan tanggal yang tampil di kartu**: kondisi SQL
      kini mencocokkan `date(...)` apa adanya (server WIB) **dan** `date(..., '+7 hours')`
      (server UTC) — sebelumnya replay yang tampil "20 Sep" tidak ketemu saat dicari
      "20 September" di sekitar tengah malam WIB.
- [x] **Label platform pesan Telegram diperbaiki**: caption arsip Telegram &
      notifikasi YouTube dulu **hardcode** `🔴 IDN LIVE REPLAY`, sehingga rekaman
      Showroom (mis. Heidi JKT48, 19 Sep 2026) dilaporkan sebagai IDN.
      Sekarang header mengikuti kolom `platform` lewat `_platform_label()`
      (IDN/SHOWROOM), dan `platform` diteruskan di semua jalur upload: `main.py`
      (UPLOAD_TARGET=telegram, arsip Telegram, notifikasi YouTube) serta
      `upload_pending.py` (retry; fallback `get_platform_for_live()`).
      Test: `tests/test_telegram_upload.py` (+3 kasus) → total **206 test** hijau.
- [x] **Kartu pra-rilis ("Segera") masuk grid home secara UTUH**: `getUpcomingVideos()`
      dulu `ORDER BY publish_at ASC LIMIT 6`, sehingga rekaman pra-rilis TERBARU tidak
      pernah muncul di grid (jam kerja nyata: hanya 6 item terdekat yang tampil,
      sisanya seolah hilang). Sekarang **tanpa batas** (`LIMIT -1`), urut terbaru
      dulu, plus saringan `member`/`platform` supaya pra-rilis tetap tampil saat
      pengunjung memfilter (pencarian kata kunci tetap murni, dan kartu hanya
      dipasang di halaman 1 agar tidak digandakan).
      Sekaligus diperbaiki: `publicVisibilitySql()` memperlakukan **setiap** nilai
      non-negatif `AUTO_PUBLISH_AFTER_HOURS_SHOWROOM` sebagai "langsung tampil",
      sehingga ambang positif (mis. 12 jam) tidak pernah berlaku dan Showroom
      berjadwal tampil sebagai pra-rilis di grid tapi pemutarnya ikut terbuka.
      Verifikasi: `web/verify/home-upcoming-grid.mjs` (baru, port 3113/3114) →
      `npm run verify:home-upcoming`; `auto-publish-check.mjs` diperbarui
      (skema fixture + ekspektasi countdown/pra-rilis) → `npm run verify:auto-publish`.
      Keduanya **PASS**.
- [x] **Thumbnail kolase 3x2 untuk video BARU (video lama tak disentuh)**:
      `bot/thumbnail_collage.py` (baru, ffmpeg saja — tanpa Pillow) mengambil 6
      frame tersebar merata, tiap frame cover-crop ke 426x360 lalu digabung
      xstack jadi 1280x720 sehingga tiap sel penuh tanpa pilar hitam (sumber live
      vertikal 9:16 vs kartu web 16:9). `YouTubeChannelPool.set_thumbnail()` baru
      memasangnya ke video hasil upload (~50 unit kuota, ke channel pengupload).
      Dipanggil di `bot/main.py` tepat setelah upload sukses, SEBELUM arsip
      Telegram & hapus file lokal; file kolase sementara dihapus setelahnya.
      Best-effort + saklar `THUMBNAIL_COLLAGE_ENABLED` (default true, contoh di
      `.env.example`): gagal → warning, upload tetap sukses. Perlu ffmpeg +
      ffprobe di VPS. Test: `tests/test_youtube_title.py::TestThumbnailCollage`
      (+9 kasus) → total **215 test** hijau.
- [x] **Kolase thumbnail dijamin PERSIS 1280x720**: 1280 tidak habis dibagi 3, jadi
      lebar sel kini per kolom `COLUMN_WIDTHS = [426, 426, 428]` (kolom kanan
      menyerap sisa 2 px) — sebelumnya 426x3 = 1278 sehingga ada pilar hitam di
      kanan (ditolak YouTube). `pick_sample_times()` ditulis ulang agar tidak
      "infinite loop" pada video < ±8 s (jarak sampel dihitung dari durasi nyata,
      bukan `count` tetap). Diverifikasi nyata dengan ffmpeg: `tmp/check-collage.py`
      (gitignored) mengubah sumber 1080x1920 → output **1280x720**, plus uji
      rotasi hue untuk membuktikan 6 sel berbeda. Test diperbarui
      (`test_grid_dimensions_fill_720p`, `test_xstack_filter_cover_no_bars`).
- [x] **Hero band + spotlight replay terbaru (menggantikan panel ringkasan arsip; band dua kolom ini digantikan lagi oleh entri berikutnya)**:
      hero kini satu panel `hero-band` (glow maroon) berisi teks di kiri
      (`hero-intro`: eyebrow, H1, copy, tombol "Jelajahi replay" jadi sekunder
      saat ada spotlight, link "Lihat member") dan **kartu spotlight** di kanan
      (`HeroSpotlight.tsx` → `.hero-card`): poster 16:9 (thumbnail kolase bot)
      + badge platform + durasi + overlay play, kicker BARU TERBIT/REPLAY
      TERBARU/SEGERA HADIR, judul, meta member·tanggal, satu CTA "Tonton replay".
      Sumber = `result.videos[0]` halaman 1 tanpa filter (nol query tambahan);
      saat pengunjung memfilter/mencari/buka halaman 2+, band otomatis jadi satu
      kolom (`.hero-band.is-solo`). CSS mati dibuang (`.hero-cinema*`,
      `.hero-note*`, `.hero-stats-inline*`, `.hero-spotlight*`), breakpoint baru
      900px (kartu turun ke bawah teks, `max-width: 640px`) dan 720px (tombol
      hero + CTA kartu full-width). `verify/portal-browser-check.mjs` kini juga
      menguji `hero-band`/`hero-card` (tidak overflow + hero 2 kolom ≥1024px).
      Verifikasi: `tsc`, `eslint`, `npm run build`, `verify:home-upcoming`,
      `verify:auto-publish`, dan browser check **20/20 PASS** (360/390/768/1024/1440).
- [x] **Hero portal diubah ke gaya hero film (full-bleed) + kalimat semboyan dibuang**:
      `HeroSpotlight.tsx` tidak lagi kartu 16:9 di kolom kanan, tapi satu panel
      `.hero-feature` — thumbnail replay terbaru menutupi SELURUH panel
      (`.hero-feature-img`, `next/image` fill, tetap tajam tanpa blur) dengan scrim
      gelap (`.hero-feature-scrim`), lalu isi ditumpuk di atasnya
      (`.hero-feature-body`): `JKT48 REPLAY · <BARU TERBIT|REPLAY TERBARU|SEGERA
      HADIR>` → H1 = judul replay asli → meta platform·durasi → "Tonton sekarang"
      (primer) + "Jelajahi arsip" (sekunder, anchor `#catalog`).
      Blok teks pemasaran di hero DIHAPUS (eyebrow ARSIP REPLAY · KOMUNITAS, copy
      "Momen favorit. Bisa ditonton lagi.", paragraf "Semua replay live JKT48 di
      satu tempat...", link "Lihat member") berikut markup `.hero-band`/`.hero-intro`
      di `PublicCatalog.tsx`. CSS `.hero-band*`/`.hero-intro*`/`.hero-card*`/
      `.hero-eyebrow`/`.hero-note*` dibuang, diganti `.hero-feature*`; breakpoint
      900px (min-height dilepas + scrim tegak bawah→atas) dan 720px (tombol hero
      full-width). `verify/portal-browser-check.mjs` kini menguji `.hero-feature`
      (tidak overflow), `.hero-feature-body` (tetap di dalam panel), dan
      `.hero-feature-img` (gambar benar-benar termuat), serta h1 = judul replay asli.
      Verifikasi: `tsc`, `eslint`, `npm run build`, browser check **20/20 PASS**
      (360/390/768/1024/1440), `verify:home-upcoming`, `verify:auto-publish` PASS.
- [x] **Penyegaran bahasa UI: dari kaku-formal ke santai khas fandom**: seluruh teks
      yang dilihat pengunjung ditulis ulang — sapaan "kamu" (kata "Anda" dihapus,
      termasuk dari halaman Tentang & form login), istilah "tayang"/"rilis"
      menggantikan "terbit"/"unggah", dan istilah Inggris natural (replay/live/
      download) dicampur seperlunya. Contoh: navbar & footer "Jelajahi" → "Replay";
      ringkasan "N replay tersedia" → "N replay siap ditonton"; empty state "Replay
      tidak ditemukan. Coba kata kunci lain, atau reset filter." → "Belum ada replay
      yang cocok. Coba kata kunci lain atau ubah filternya."; tombol filter
      "Terapkan" → "Cari"; hero "BARU TERBIT"/"Jelajahi arsip"/"Lihat hitung mundur"
      → "BARU RILIS"/"Lihat semua replay"/"Lihat jadwal tayang"; countdown "Replay
      sudah tersedia. Pemutar akan terbuka otomatis…" → "Replay-nya sudah tayang.
      Pemutar kebuka otomatis…"; modal Telegram ditulis ulang; halaman Tentang
      ("Arsip untuk komunitas" → "Arsip yang dibuat fans, untuk fans") dan watch
      ("Lainnya dari X" → "Replay lain dari X", og:description "Arsip siaran ulang"
      → "Replay live JKT48 — IDN & Showroom"). Admin diseragamkan juga: "rekaman"
      → "replay", "Ujicoba pemutar" → "Uji pemutar", pesan-pesan notice lebih
      manusiawi. Metadata SEO ikut disegarkan (title + description).
      `verify/home-upcoming-grid.mjs` (assert "replay siap ditonton", "Belum ada
      replay yang cocok") dan `verify/portal-browser-check.mjs` (regex ringkasan baru
      + penjaga anti-regresi `/\bAnda\b/` & `/siaran ulang/i` di `/`, `/members`,
      `/about`) diperbarui; uji hero kini menunggu `.hero-feature-img` termuat
      (maks 5 detik) supaya tidak flake. Verifikasi: `tsc`, `eslint`, `npm run build`,
      browser check **20/20 PASS** (2×), `verify:home-upcoming`, `verify:auto-publish`
      PASS.
- [x] **Arsip TikTok: bot + halaman publik `/tiktok`** (fitur baru, default
      NONAKTIF). Bot memantau 51 akun TikTok member (`tiktok_accounts.json`,
      di-seed lewat `bot/seed_tiktok.py` — 45 akun otomatis ketemu member-nya di
      `member_hls`). Modul baru: `bot/tiktok_client.py` (penyedia `tikwm` →
      `yt-dlp` → `fixture` + RateLimiter 1 req/detik + penanda penyedia "tidak
      sehat" 15 menit), `bot/tiktok_media.py` (unduh video/foto, slide show
      ffmpeg 1080x1920, pemecahan foto per 10 album), `bot/tiktok_monitor.py`
      (round-robin 1 akun/siklus, unduh → kirim channel arsip Telegram → unggah
      YouTube → notifikasi, plus `--once/--account/--dry-run`), tabel
      `tiktok_accounts` + `tiktok_posts` di `bot/database.py`, caption
      `build_tiktok_caption/notification`, judul/deskripsi YouTube
      `TIKTOK VIDEO/FOTO/STORY …`, dan deep-link bot publik `tt_<post_id>`
      (foto dikirim sebagai album sehingga user menerima FOTONYA). Web:
      `web/lib/tiktok.ts` + `web/app/tiktok/page.tsx` +
      `web/components/TikTokArchive.tsx` (tata letak 3 kolom: akun · pemutar ·
      daftar arsip; ≤1100px 2 kolom, ≤820px 1 kolom), API `/api/tiktok/posts`,
      tombol download payload TikTok, tautan navbar, dan tabel TikTok ikut dibuat
      di `web/lib/db.ts`. Fakta lapangan (uji 21 Sep 2026): tikwm gratis ±1
      req/detik **dan** dijawab Cloudflare 403 dari IP datacenter; yt-dlp bisa
      video per-URL tetapi listing profil butuh secUid (diisi otomatis dari
      `channel_id` postingan pertama) dan tidak mendukung story — karena itu
      `TIKTOK_PROVIDER=auto` + fallback otomatis. Verifikasi: **326 test Python
      lulus** (237 lama + **89 test TikTok baru**: client/media/database/captions/
      monitor, termasuk uji kontrak penyedia yang menangkap blok kode nyasar),
      `tsc` 0, `eslint` 0 error, `npm run build` OK, **`verify:tiktok` PASS**,
      browser check **25/25 PASS** (5 lebar × 5 rute, kini termasuk `/tiktok`
      dengan cek jumlah kolom), `verify:home-upcoming` & `verify:auto-publish`
      PASS.
- [x] **Arsip TikTok: story & listing (metode dari proyek `JKT48_TIKTOK`)**.
      Temuan + perbaikan:
      1. **Endpoint story yang benar = `/api/user/story` (TUNGGAL)** —
         `/api/user/stories` menjawab 404, itulah sebabnya story tidak pernah
         terambil. Paginasi memakai `hasMore` (camel) → `_next_cursor()` menerima
         `hasMore` maupun `has_more`.
      2. **`curl_cffi` + `impersonate=chrome131` = kunci tembus Cloudflare.**
         Tanpa itu tikwm dan halaman embed TikTok sama-sama 403; dengan itu
         keduanya 200 dari mesin yang sama (`httpx` tetap jadi fallback).
         Lapisan HTTP baru: `browser_headers()`, `_sync_request()` via
         `asyncio.to_thread`, `http_get_text_retry()` (retry 3× untuk 503).
      3. **Penyedia baru `EmbedProvider`** dari metode proyek referensi:
         `tiktok.com/embed/@user` → `videoList` 10 post terbaru (foto dikenali
         dari **ketiadaan `playAddr`**), dan `tiktok.com/embed/v2/<id>` →
         `itemInfos.createTime` + `imagePostInfo.displayImages`. Parser HTML
         murni (`parse_embed_profile` / `parse_embed_post` / `extract_post_id`)
         supaya bisa diuji tanpa jaringan. Urutan `auto` kini
         **tikwm → embed → ytdlp**.
      4. **Kesehatan penyedia per kapabilitas** (`is_healthy("posts")` /
         `is_healthy("stories")`): 403 di `/user/posts` tidak lagi mematikan
         jalur story, dan satu siklus bisa memakai listing dari embed + story
         dari tikwm. `mark_capability_missing()` untuk penyedia tanpa story.
      5. **Detail postingan baru** (`_enrich_new_items`) melengkapi tanggal,
         durasi, dan daftar foto hanya untuk postingan yang baru ditemukan.
      6. Deteksi foto tambahan (`looks_like_photo`: `duration == 0 && size == 0`),
         kunci video alternatif (`video_url`/`download_url`/`wmplay`), dan
         fallback media: URL CDN tikwm kedaluwarsa → URL segar dari embed, serta
         jumlah foto 0 → daftar foto dari embed.
      7. **Bug diperbaiki: daftar `ffconcat` harus absolut.** Path relatif
         diselesaikan relatif terhadap lokasi berkas daftar (bukan CWD) sehingga
         `DOWNLOAD_DIR` relatif membuat semua slide show gagal
         ("No such file or directory"); `_ffconcat_escape()` kini
         `resolve().as_posix()`.
      8. **Bug diperbaiki: perbandingan metode stub.** `getattr(provider, nama)
         is getattr(BaseProvider, nama)` selalu False (metode terikat), sehingga
         penyedia tanpa story tetap dipanggil dan mengembalikan `[]` yang
         menghentikan pencarian penyedia berikutnya. Kini dibandingkan
         `__func__`.
      Hasil uji nyata 21 Sep 2026: listing embed 10 post/akun (foto 9 & 13 gambar
      terdeteksi); **scan story 51 akun dalam 62 detik, 0 gagal → 4 akun punya
      story aktif** (`fionyjkt48`, `jkt48.erine_`, `jkt48.intan` 2, `jkt48.maira`);
      unduh story nyata 1,33 MB/15 detik via yt-dlp; unduh postingan foto nyata
      9 foto → 1 album dan **13 foto → 2 album (10+3)** + slide show 13 detik.
      Verifikasi: **356 test Python lulus** (95 test TikTok), pyflakes bersih,
      `tsc`/`eslint`/`build` OK, `verify:tiktok` PASS, browser check 25/25 PASS.

- [x] **Deploy: interpreter bot (PEP 668 / venv)** — dipicu oleh kegagalan nyata di
      VPS: `pip install -r requirements.txt` ditolak
      `error: externally-managed-environment`. Tiga cacat diperbaiki:
      1. `deploy/ecosystem.config.js` kini memilih interpreter dengan urutan
         `BOT_PYTHON` → `.venv/bin/python` → `venv/bin/python` →
         `.venv|venv/Scripts/python.exe` → `python3`. Sebelumnya **hanya**
         `.venv`, padahal README menyuruh membuat `venv`, sehingga pengikut
         README menjalankan bot dengan `python3` sistem yang dependensinya tidak
         lengkap (`ModuleNotFoundError`).
      2. `deploy/README.md` **tidak punya langkah `pip install`** untuk bot sama
         sekali — ditambahkan bagian "Dependensi bot (venv)" + baris prasyarat +
         3 baris troubleshooting (PEP 668, `ModuleNotFoundError` setelah restart,
         TikTok selalu 403 karena `curl_cffi` hilang di interpreter PM2).
      3. README TikTok menyuruh `pm2 restart jkt48-bot`, padahal nama proses PM2
         adalah `jkt48-archiver-bot` — perintah lama tidak berefek apa pun.
         Sekaligus dicatat bahwa `pm2 restart` **tidak** memuat ulang `script`
         dari `ecosystem.config.js` (issue PM2 #3742), sehingga venv baru hanya
         terpakai setelah `pm2 delete` + `pm2 start ... --only jkt48-archiver-bot`
         (dijelaskan di README, deploy/README.md, dan MEMORY.md).
      Verifikasi: `node --check` OK; logika prioritas diuji nyata (6 kasus di
      direktori bersih) — tanpa venv → `python3`, `venv/` → `venv/bin/python`,
      keduanya → `.venv` menang, venv Windows → `Scripts/python.exe`,
      `BOT_PYTHON` → menang atas semuanya; `apps[0]` (web) tidak berubah.

- [x] **Pencocokan akun TikTok → member diperbaiki (lapis kedua: nama inti)** —
      dipicu seed nyata di VPS: 6 akun tak berpasangan
      (`jkt48.aurellia_`, `kathrinjkt48`, `jkt48.ella.a`, `jkt48.lyn.s`,
      `jkt48.raisha.s`, `jkt48.u16`), padahal member-nya ADA di `member_hls`
      dengan nama berbeda (`jkt48_kathrina`, `jkt48_ella`, `jkt48_raisha`,
      `jkt48_lyn`) — username TikTok memakai titik + inisial dan bentuk lain.
      1. `core_name()` baru: buang penanda `jkt48`, angka, dan inisial satu huruf
         (`jkt48.lyn.s` → `lyn`, `kathrinjkt48` → `kathrin`, `jkt48.u16` → ``).
      2. `build_member_core_index()` + `_match_by_core()`: inti persis, lalu
         awalan (`kathrin` ⊂ `kathrina`). Pengaman: inti ≥3 huruf, awalan ≥5
         huruf, hasil harus TUNGGAL; inti persis yang ambigu langsung berhenti
         (tidak jatuh ke aturan awalan) — ditemukan sendiri oleh test
         `test_ambiguous_core_is_skipped` saat awalan sempat cocok ke member
         `Raisha Kedua`.
      3. `is_backup_account()`: akun cadangan `u16` tidak pernah dipetakan;
         dilaporkan terpisah saat seed.
      4. Seed kini menandai barisnya `(via nama inti)` dan meringkas jumlahnya,
         supaya pemetaan otomatis mudah diaudit dari log.
      Hasil: VPS 45 → **49 dari 51** terpetakan (sisa `jkt48.aurellia_` dan
      `jkt48.u16` yang memang akun cadangan). Verifikasi: `tests/test_seed_tiktok.py`
      baru (**18 test**, termasuk idempotensi, `--dry-run` tidak menulis, dan
      pengisian `member_username` yang masih NULL saat seed diulang);
      **374 test Python lulus**; dry-run nyata memakai 51 akun asli.

- [x] **Audit dependensi + bersihkan BOM** — umpan balik dari VPS: perintah
      verifikasi yang saya sarankan memuat `aiohttp` yang **tidak pernah** dipakai
      kode (perintah itu salah, bukan dependensinya). Diaudit dengan `ast` +
      `sys.stdlib_module_names`: pihak ketiga yang benar-benar terpakai =
      `telethon`, `httpx`, `curl_cffi`, `dotenv`, `googleapiclient`,
      `google_auth_oauthlib`, `colorlog` (+ `yt_dlp` via `sys.executable -m`;
      `requests` hanya transitif `google-auth-oauthlib`). `requirements.txt`
      ternyata sudah lengkap dan benar. Sekaligus `tests/test_youtube_title.py`
      dibersihkan dari BOM UTF-8 yang membuat skrip audit (dan alat lain)
      error — 16 test lamanya tetap lulus.

- [x] **Roster resmi jkt48.com: peta akun TikTok + foto member** (fitur baru,
      dipicu data API resmi yang diberikan pengguna). Modul baru
      `bot/jkt48_members.py`:
      1. Parser murni `parse_member_list` / `parse_member_detail` /
         `merge_members` / `photo_url`; hanya `KEPT_FIELDS` yang disimpan (data
         pribadi seperti tanggal lahir & golongan darah TIDAK disimpan).
      2. Lapisan HTTP memakai ulang `bot/tiktok_client` (`browser_headers`,
         `http_request`, `RateLimiter`) — jkt48.com dijawab 403 Cloudflare untuk
         request biasa, 200 JSON dengan `curl_cffi` + impersonate (diuji 21 Sep
         2026). 1 request daftar + 58 request detail, jeda
         `JKT48_MEMBERS_INTERVAL_SECONDS=0.35`.
      3. Cache `jkt48_members.json` (di-commit, seperti `showroom_rooms.json`)
         + CLI `--update` / `--print`, gagal → exit 1, satu detail gagal tidak
         menggagalkan seluruh proses.
      `seed_tiktok` kini memakai roster sebagai sumber **otoritatif**:
      `jkt48.aurellia_` → `jkt48_lia` (sebelumnya mustahil ditebak dari nama),
      `handle_index` menang atas pencocokan nama (perbedaan dilaporkan di log),
      peta balik mengisi nama + foto untuk akun yang tidak dilaporkan API
      (`jkt48.heidi__`, `jkt48.rara_`), dan `member_username` manual tetap
      menang. Kolom `avatar_url` baru di `tiktok_accounts` (bot + migrasi
      `web/lib/db.ts`), ditulis `COALESCE` agar koreksi manual tidak tertimpa.
      Sisi web: foto member di sidebar kiri `/tiktok` via `next/image`
      (+ pola gambar `jkt48.com`), fallback inisial bila foto kosong. Hasil
      dry-run nyata: roster 58 member/48 akun TikTok, 47 akun terhubung,
      **50 akun dapat foto**, sisa tak berpasangan tinggal `anindyajkt48`
      (baris `jkt48_anindya` tidak ada di DB dev) + `jkt48.u16`.
      Verifikasi: **419 test Python lulus** (+43: `tests/test_jkt48_members.py`
      baru 35 test + 12 test integrasi roster di `tests/test_seed_tiktok.py`,
      yang kini juga mengisolasi `Config.JKT48_MEMBERS_FILE`), pyflakes bersih,
      `tsc` 0, `eslint` 0 (sekaligus menghapus impor `isRawYoutubeId` yang tak
      terpakai di `web/lib/db.ts`), `build` OK, **`verify:tiktok` PASS**
      (kini juga memeriksa foto member + fallback inisial), browser check
      **25/25 PASS**, `verify:home-upcoming` & `verify:auto-publish` PASS.

- [x] **Hardening batch (22 Sep 2026)** — perubahan kecil yang menutup lubang
      operasional & keamanan, diverifikasi penuh sebelum commit:
      1. **Guard ruang disk sebelum rekaman baru**: `MIN_FREE_DISK_MB`
         (default 2048) + `has_enough_disk_space()` di `bot/downloader.py`,
         dipanggil di loop utama IDN **dan** `_check_showroom` sebelum spawn
         task. Di bawah ambang → live terdeteksi tapi TIDAK direkam (warning
         log), rekaman yang sudah jalan dibiarkan; cek fail-open bila
         `disk_usage` error. Test: `tests/test_disk_space.py` (+6).
      2. **SQLite WAL + busy_timeout + index**: bot (`database.py`) dan web
         (`lib/db.ts`) kini memakai `journal_mode=WAL`, `busy_timeout=5000`,
         `synchronous=NORMAL` agar akses bersamaan bot(writer)/web(reader)
         tidak "database is locked". Index query berat dibuat **best-effort
         per-statement** — fixture uji & DB lama yang skemanya minimal (mis.
         `merge_groups` tanpa kolom `status`) tidak boleh membuat `init_db` /
         `getDb` gagal (regresi yang sempat mematahkan `verify:auto-publish`
         & `verify:tiktok`).
      3. **Rate-limit login per-IP** (`web/lib/auth.ts`): bucket di-key
         `login:<ip>` (x-forwarded-for bila ada) supaya satu brute-force tidak
         mengunci semua admin 15 menit.
      4. **`APP_ORIGIN` fail-closed di production**: tanpa origin yang diset,
         Origin check jadi no-op; kini menolak permintaan (dev tetap memakai
         origin request). `.env.example` mewajibkan `APP_ORIGIN`.
      5. **Upload YouTube error non-kuota → `pending_upload`** (bukan
         `failed` permanen) — `get_pending_uploads_youtube` hanya memilih
         `pending_upload`, jadi error network/OAuth kini bisa di-retry.
      6. **Ref task retry upload disimpan** (`self._retry_task`) agar tidak
         di-GC di tengah jalan; task admin bot diberi `name="admin-bot"`.
      7. **Halaman 404 kustom** (`web/app/not-found.tsx` + CSS
         `.not-found*`) — nada bahasa mengikuti UI publik ("kamu"/"replay").
      8. **Dependensi**: `yt-dlp` di-pin longgar (`>=2025.1.15`) karena
         extractor IDN/Showroom/TikTok sering berubah; `requests` naik patch;
         `tqdm`/`asyncio-throttle` dibuang (tak terpakai kode).
      Verifikasi: **420 test Python lulus** (+6), `py_compile` OK, pyflakes
      bersih untuk berkas yang disentuh, `tsc` 0, `eslint` 0, `build` OK,
      `verify:home-upcoming` PASS, `verify:auto-publish` PASS,
      `verify:tiktok` PASS. (`portal-browser-check.mjs` butuh Chrome CDP +
      server :3101 yang tidak tersedia di sesi ini — jalankan terpisah bila
      perlu.)

- [x] **Thumbnail kolase 3x2: perbaiki kegagalan intermiten (22 Sep 2026)** —
      sebagian video menampilkan thumbnail otomatis YouTube (pilar hitam)
      padahal kolase seharusnya terpasang. Dua akar: (1) 6 seek dalam satu
      perintah ffmpeg gagal total bila SATU titik kosong di segmen merge;
      (2) pixel format beda antar segmen → xstack tolak. Perbaikan:
      ekstrak frame per-intan + retry offset, `format=yuv420p`, folder kerja
      temp, dan **fallback wajib 1 frame cover 1280x720** sehingga YouTube
      tidak pernah lagi memakai auto-thumb berpilar hitam bila file lokal
      ada. `set_thumbnail` kini membuat `MediaFileUpload` per channel
      (stream tidak dikonsumsi silang). Verifikasi: nyata ffmpeg kolase +
      fallback + video 2 dtk semuanya **1280x720**; **424 test Python** hijau
      (+4 pada `test_youtube_title.py`); pyflakes bersih untuk berkas yang
      disentuh.

- [x] **Hardening batch (25 Sep 2026)** — perbaikan jalur produksi:
      1. **Siklus TikTok jadi background task** di `main.py` (pola sama dengan
         HLS refresh) — `tiktok.run_once()` tidak lagi memblokir polling
         HLS/Showroom.
      2. **Cooldown flood Telegram persisten**: `_flood_cooldown` disimpan ke
         sidecar JSON `<DB_PATH>.flood_cooldown.json` (atomic write, diprun
         otomatis) sehingga `pm2 restart` tidak menggiling file 1 GB dari
         byte 0; `bot/upload_pending.py` menghormati cooldown yang sama.
      3. **`shutdown()` idempoten** (`_shutdown_task` single-flight) — handler
         sinyal dan blok `finally` tidak bisa menjalankannya dua kali.
      4. **Precheck disk sebelum concat** di `merger.py`: butuh
         ~SUM(segmen)+256 MB; tidak cukup → finalize ditunda (segmen tetap
         disimpan), bukan gagal di tengah ffmpeg.
      5. **Retry resumable upload YouTube**: 429/5xx dan ConnectionError/
         TimeoutError di-backoff 5→10→20→40s (maks 5 percobaan); hanya reason
         kuota sungguhan (`quotaExceeded`/`dailyLimitExceeded`/
         `uploadLimitExceeded`/`userRateLimitExceeded`) yang merotasi channel —
         403 polos (`insufficientPermissions`) tidak lagi membakar kuota rotasi.

## Kandidat Pekerjaan Berikutnya (belum dikerjakan)

- [ ] **Arsip TikTok — verifikasi di VPS**: metode sudah terbukti di mesin
      pengembangan (embed listing + tikwm story, lihat catatan di atas), tetapi
      jalur produksi tetap perlu dijalankan sekali di VPS. Urutan yang benar:
      `python3 -m venv .venv && source .venv/bin/activate && pip install -r
      requirements.txt` (PEP 668 melarang pip sistem), lanjutkan dengan
      `TIKTOK_ENABLED=true python3 -m bot.tiktok_monitor --account jkt48.maira`,
      lalu pastikan listing, story, unduhan, dan upload berjalan. Periksa juga
      `pm2 describe jkt48-archiver-bot` → `script path` harus menunjuk
      `.venv/bin/python`, supaya `curl_cffi` benar-benar ada di interpreter bot.
- [ ] **Jalankan roster + reseed di VPS** (langkah baru, wajib agar foto member
      dan `jkt48.aurellia_` muncul di produksi): `python3 -m bot.jkt48_members
      --update` → `python3 -m bot.seed_tiktok` → `TIKTOK_ENABLED=true
      pm2 restart jkt48-archiver-bot --update-env`. Periksa log seed:
      `jkt48.aurellia_` harus menunjuk `jkt48_lia` dan 50 akun harus punya foto.
      Bila cache sudah ter-commit, `--update` boleh dilewati (data tetap dipakai).
- [ ] Segarkan roster berkala (mis. sekali sebulan atau saat ada member baru):
      `python3 -m bot.jkt48_members --update` lalu commit `jkt48_members.json`.
      Belum ada otomatisasi/jadwal.
- [ ] Saklar publik per-postingan TikTok dari halaman admin (kolom `visible`
      sudah ada di DB, tetapi belum ada UI-nya).
- [ ] Pantau kuota tikwm: story satu-satunya sumber story, jadi bila kuota harian
      habis story berhenti sampai jeda `TIKWM_QUOTA_COOLDOWN_SECONDS`. Bila sering
      terjadi, pertimbangkan menggilir lebih sedikit akun per siklus.
- [ ] Fallback re-encode (`libx264 -preset veryfast`) untuk concat yang tetap gagal
      karena parameter codec/resolusi antar segmen berbeda di tengah live.
- [ ] Memverifikasi perilaku saat `slug` berubah di tengah live (title baru)
      tetap masuk grup merge yang sama (harusnya sudah tertangani by member).
- [ ] Uji lapangan: live panjang (> 2 jam) dengan beberapa lag untuk memastikan
      hasilnya satu video dan upload ±10–30 menit setelah selesai.

## Untuk Diingat Saat Melanjutkan

- Baca `SEOUL.md` dan `MEMORY.md` sebelum menyentuh kode.
- Selalu jalankan `python -m py_compile bot\*.py` setelah mengubah Python.
- Kalau bisa, jalankan juga `python -m pyflakes bot\tiktok_*.py` — pyflakes
  menangkap "metode ketimpa stub" dan `NameError` laten yang tidak terlihat
  oleh unit test berbasis fixture (insiden 21 Sep 2026 di `tiktok_client.py`).
- Jalankan seluruh test: `python -m unittest discover -s tests -t .`
- Saat selesai, update bagian status di atas.
