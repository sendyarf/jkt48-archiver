# Portal publik & Admin Studio

## Menjalankan
Dari `c:\Sendy\jkt48-live\web`, jalankan `npm run dev` untuk development, atau `npm run build` lalu `npm start` untuk produksi (Node 24 digunakan saat validasi).

Konfigurasi environment server:
- `DB_PATH`: path absolut database bot yang sudah diinisialisasi.
- `ADMIN_SECRET`: secret acak minimal 32 karakter. Tidak ada password fallback; login dinonaktifkan bila konfigurasi tidak memenuhi syarat.
- `APP_ORIGIN`: origin situs yang tepat (scheme + host + port, tanpa trailing slash), terutama ketika di belakang reverse proxy.
- `AUTO_PUBLISH_AFTER_HOURS`: berapa jam setelah live **selesai** sebuah rekaman **IDN** otomatis tampil publik. Default `72`. Isi `0` untuk mematikan dan mewajibkan persetujuan admin untuk setiap rekaman IDN.
- `AUTO_PUBLISH_AFTER_HOURS_SHOWROOM`: ambang khusus rekaman **Showroom**. Default `0` = rekaman Showroom **langsung tampil** segera setelah tersimpan, tanpa menunggu ambang IDN. Isi nilai negatif untuk mematikan rilis otomatis Showroom (wajib persetujuan admin). Keputusan admin (terbit/tahan) tetap selalu menang atas kedua aturan otomatis.
- `NEXT_PUBLIC_REPLAY_BOT_USERNAME`: username bot Telegram publik (tanpa `@`) yang dipakai tombol **Download via Bot Telegram** di `/watch` dan `/tiktok`. Kosong = tombol masih tampil tetapi modal menyatakan bot belum dikonfigurasi.

Gunakan HTTPS pada produksi: cookie admin memakai Secure pada NODE_ENV=production. Jangan gunakan prefix NEXT_PUBLIC untuk secret. Jangan commit environment/kredensial. Rotasi ADMIN_SECRET membatalkan semua sesi lama.

## Publikasi
Aturan visibilitas berlapis, dievaluasi pada katalog, pencarian, direktori member, rekomendasi, metadata, dan akses watch:
1. Admin memilih **Terbitkan** → publik (menang atas aturan otomatis).
2. Admin memilih **Tarik publikasi** → tersembunyi (menang atas aturan otomatis).
3. Belum ada keputusan admin, bukan rekaman Showroom, dan sudah lewat `AUTO_PUBLISH_AFTER_HOURS` sejak live selesai → otomatis publik.
4. Belum ada keputusan admin dan rekaman **Showroom** → langsung otomatis publik (kecuali `AUTO_PUBLISH_AFTER_HOURS_SHOWROOM` diisi negatif).
5. Belum ada keputusan admin dan belum lewat ambang → tersembunyi.

Waktu acuan adalah `download_ended_at` (kapan rekaman selesai) dan jatuh ke `created_at` bila kosong; keduanya dibandingkan sebagai UTC sehingga hasilnya sama di server zona waktu mana pun. Halaman `/admin/publications` menampilkan apakah sebuah rekaman publik karena keputusan manual atau karena aturan otomatis, beserta umur rekaman.

Masuk melalui `/login`, buka `/admin/publications`, tinjau rekaman lalu pilih Terbitkan. Pastikan hak distribusinya sebelum menerbitkan. Tarik publikasi untuk menghapus dari katalog, direktori, rekomendasi, metadata dan akses watch berikutnya. Konten yang sudah diunduh/terbuka, cache eksternal, serta video pada YouTube tidak dapat dicabut oleh situs ini.

Persetujuan disimpan pada tabel tambahan `web_publications`, terpisah dari status bot. Tidak ada perubahan file Python atau pengaturan upload YouTube. Sumber katalog tetap `live_sessions`, dan kolom `platform` bot dipakai apa adanya: baris lama serta database pra-migrasi otomatis dibaca sebagai `idn` (bukan ditebak dari judul). Filter `?platform=showroom` menampilkan rekaman Showroom, dan `VideoPlayer` otomatis memakai tata letak landscape untuknya. Catatan: rekaman Showroom hanya bisa muncul di katalog bila `UPLOAD_TARGET=youtube`, karena katalog dibangun dari `youtube_video_id`.

`/api/members` hanya mengembalikan username, display_name, video_count untuk member dengan arsip publik. API perubahan pindah ke `/api/admin/members`; kredensial dalam body API lama tidak lagi didukung. `/status` mengarah ke `/admin/status` yang memerlukan sesi.

## Arsip TikTok (`/tiktok`)
Halaman ini membaca dua tabel tambahan: `tiktok_accounts` (akun yang dipantau) dan `tiktok_posts` (arsip video/foto/story). Keduanya **ditulis bot** (`bot/tiktok_monitor.py`, aktif hanya bila `TIKTOK_ENABLED=true`); web hanya membaca lewat `web/lib/tiktok.ts` dan `web/lib/db.ts` juga membuat tabelnya agar halaman tidak 500 pada database baru.

Tata letak: **3 kolom di desktop** — daftar akun (kiri), pemutar + detail (tengah), daftar arsip (kanan) — menjadi 2 kolom pada ≤1100px dan 1 kolom pada ≤820px. Daftar akun hanya memuat akun `enabled = 1`; daftar arsip hanya memuat arsip yang **siap tayang** (`visible = 1` dan sudah punya video YouTube atau media di channel arsip Telegram) sehingga tidak pernah muncul kartu atau pemutar kosong.

Tiap arsip menampilkan jenisnya (Video/Foto/Story), jumlah foto untuk postingan foto, dan tombol **Download** yang mengarahkan ke bot Telegram dengan payload `tt_<post_id>` (arsip replay tetap memakai YouTube ID). Bot mengirim videonya, atau **album foto** bila itu postingan foto. Thumbnail diutamakan dari YouTube (`img.youtube.com`) karena URL cover TikTok CDN bertanda tangan dan cepat kedaluwarsa; `next.config.ts` hanya mengizinkan host CDN TikTok sebagai fallback.

`/api/tiktok/posts` mengembalikan daftar arsip publik (`?account=<unique_id>&limit=&offset=`) untuk pergantian akun dari sisi klien. Pengunjung tidak perlu login. Kata "Anda" dan istilah "siaran ulang" tidak dipakai di halaman ini (mengikuti aturan bahasa UI publik).

## Sesi dan deployment
Sesi acak 256-bit disimpan sebagai hash di SQLite dengan expiry 8 jam; cookie HttpOnly + SameSite=Strict, logout mencabut token. Login dibatasi global 20 percobaan per 15 menit melalui SQLite (tidak mempercayai header IP). Tambahkan rate limit per IP pada reverse proxy tepercaya untuk mengurangi risiko lockout bersama. Pastikan DB dan file environment tidak disajikan sebagai static files; jangan cache HTML/API admin pada CDN. Backup database sebelum deployment. Tabel web_publications, web_admin_sessions, web_login_limits dibuat otomatis.

Halaman publik saat ini menganggap situs sebagai arsip komunitas non-resmi. Halaman Tentang menjelaskan layanan pihak ketiga; kontak pemilik/pengajuan penghapusan langsung belum tersedia karena alamat kontak belum dikonfigurasi.

## Verifikasi
- `npm run build`
- `npx tsc --noEmit` dan `npx eslint .`: keduanya tanpa temuan.
- `node c:\Sendy\jkt48-live\web\verify\public-private-check.mjs` (setelah build): DB fixture di direktori temp, server produksi pada port 3107, dibersihkan otomatis. Menguji default privat, DTO publik, gerbang watch/metadata, publikasi/penarikan, filter member/platform, auth admin, pemeriksaan origin, flag cookie, dan logout.
- `node c:\Sendy\jkt48-live\web\verify\auto-publish-check.mjs` (setelah build): dua server (port 3111 & 3112) pada satu fixture DB. Menguji rilis otomatis 72 jam untuk IDN, rekaman belum lewat ambang tetap tersembunyi, rekaman Showroom **langsung tampil** (dan tetap tunduk keputusan admin), keputusan admin (terbit/tahan) menang atas aturan otomatis, jumlah arsip publik hanya menghitung rekaman yang tampil, serta `AUTO_PUBLISH_AFTER_HOURS=0` mematikan rilis otomatis IDN.
- `node c:\Sendy\jkt48-live\web\verify\portal-browser-check.mjs`: halaman publik (5 lebar: 360/390/768/1024/1440) tanpa overflow, tanpa data operasional, admin dialihkan ke login. Sejak arsip TikTok ada, rute yang diuji mencakup `/tiktok` — termasuk memastikan `.tiktok-shell` benar-benar **3 kolom** di ≥1101px, 2 kolom di 821–1100px, 1 kolom di ≤820px, dan tidak ada panel yang meluber (memerlukan dev/prod server di port 3000 dan Chrome dengan `--remote-debugging-port=9222`).
- `node c:\Sendy\jkt48-live\web\verify\tiktok-page.mjs` (atau `npm run verify:tiktok`, setelah build): DB fixture di direktori temp, server produksi pada port 3115. Menguji halaman `/tiktok`: tata letak 3 kolom, sidebar hanya memuat akun `enabled = 1`, daftar hanya memuat arsip siap tayang (urut terbaru dulu, arsip yang masih diproses dan `visible = 0` disembunyikan), postingan foto menampilkan jumlah foto, story berlabel Story, tombol download memakai payload `tt_<post_id>`, `/api/tiktok/posts` bisa memfilter per akun, tautan `/tiktok` ada di navbar, dan tidak ada kata "Anda"/"siaran ulang".
- `node c:\Sendy\jkt48-live\web\verify\player-trial-check.mjs` (setelah build): ujicoba pemutar di `/admin/trial`. Menguji landscape Showroom (16/9, tanpa tombol rasio vertikal) berbeda dari vertikal IDN (3/4, dengan tombol rasio), **fullscreen elemen landscape** (wrapper mengisi viewport, container 16/9, tanpa theater satu halaman, scroll tidak dikunci, pemutar tetap ter-mount) beserta pemulihan status tombol saat keluar, **geometri bilah progres tetap 4 px tanpa min-height/border/padding bocor dari gaya formulir admin**, pemutar ter-render di keduanya, dan akses tanpa sesi dialihkan ke `/login`. Server port 3110. Screenshot: `verify/trial-showroom-fullscreen.png`.
- `node c:\Sendy\jkt48-live\web\verify\player-check.mjs` (setelah build): Fixture DB dengan video yang dipublikasikan, server pada port 3108. Menguji player render, theater + fullscreen, 9 kombinasi rasio/responsif, identitas instance player, urutan Escape volume → theater, pemulihan fokus, persistensi mode fit setelah reload, dan tidak ada error hidrasi.
- Jalankan kedua skrip browser satu per satu, jangan bersamaan dengan `npm run build` (build menimpa `.next` yang sedang dipakai server tes).
- `verify/theater-check.cjs` dan `volume-check.cjs` lama tetap ada; keduanya memakai URL video tetap dan karena itu hanya berjalan bila video tersebut dipublikasikan.
- Rincian temuan lint lama dan penyelesaiannya ada di `PROBLEMS-REPORT.md`.
