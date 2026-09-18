# Portal publik & Admin Studio

## Menjalankan
Dari `c:\Sendy\jkt48-live\web`, jalankan `npm run dev` untuk development, atau `npm run build` lalu `npm start` untuk produksi (Node 24 digunakan saat validasi).

Konfigurasi environment server:
- `DB_PATH`: path absolut database bot yang sudah diinisialisasi.
- `ADMIN_SECRET`: secret acak minimal 32 karakter. Tidak ada password fallback; login dinonaktifkan bila konfigurasi tidak memenuhi syarat.
- `APP_ORIGIN`: origin situs yang tepat (scheme + host + port, tanpa trailing slash), terutama ketika di belakang reverse proxy.
- `AUTO_PUBLISH_AFTER_HOURS`: berapa jam setelah live **selesai** sebuah rekaman otomatis tampil publik. Default `72`. Isi `0` untuk mematikan dan mewajibkan persetujuan admin untuk setiap rekaman.

Gunakan HTTPS pada produksi: cookie admin memakai Secure pada NODE_ENV=production. Jangan gunakan prefix NEXT_PUBLIC untuk secret. Jangan commit environment/kredensial. Rotasi ADMIN_SECRET membatalkan semua sesi lama.

## Publikasi
Aturan visibilitas berlapis, dievaluasi pada katalog, pencarian, direktori member, rekomendasi, metadata, dan akses watch:
1. Admin memilih **Terbitkan** → publik (menang atas aturan otomatis).
2. Admin memilih **Tarik publikasi** → tersembunyi (menang atas aturan otomatis).
3. Belum ada keputusan admin dan sudah lewat `AUTO_PUBLISH_AFTER_HOURS` sejak live selesai → otomatis publik.
4. Belum ada keputusan admin dan belum lewat ambang → tersembunyi.

Waktu acuan adalah `download_ended_at` (kapan rekaman selesai) dan jatuh ke `created_at` bila kosong; keduanya dibandingkan sebagai UTC sehingga hasilnya sama di server zona waktu mana pun. Halaman `/admin/publications` menampilkan apakah sebuah rekaman publik karena keputusan manual atau karena aturan otomatis, beserta umur rekaman.

Masuk melalui `/login`, buka `/admin/publications`, tinjau rekaman lalu pilih Terbitkan. Pastikan hak distribusinya sebelum menerbitkan. Tarik publikasi untuk menghapus dari katalog, direktori, rekomendasi, metadata dan akses watch berikutnya. Konten yang sudah diunduh/terbuka, cache eksternal, serta video pada YouTube tidak dapat dicabut oleh situs ini.

Persetujuan disimpan pada tabel tambahan `web_publications`, terpisah dari status bot. Tidak ada perubahan file Python atau pengaturan upload YouTube. Sumber katalog tetap `live_sessions`, dan kolom `platform` bot dipakai apa adanya: baris lama serta database pra-migrasi otomatis dibaca sebagai `idn` (bukan ditebak dari judul). Filter `?platform=showroom` menampilkan rekaman Showroom, dan `VideoPlayer` otomatis memakai tata letak landscape untuknya. Catatan: rekaman Showroom hanya bisa muncul di katalog bila `UPLOAD_TARGET=youtube`, karena katalog dibangun dari `youtube_video_id`.

`/api/members` hanya mengembalikan username, display_name, video_count untuk member dengan arsip publik. API perubahan pindah ke `/api/admin/members`; kredensial dalam body API lama tidak lagi didukung. `/status` mengarah ke `/admin/status` yang memerlukan sesi.

## Sesi dan deployment
Sesi acak 256-bit disimpan sebagai hash di SQLite dengan expiry 8 jam; cookie HttpOnly + SameSite=Strict, logout mencabut token. Login dibatasi global 20 percobaan per 15 menit melalui SQLite (tidak mempercayai header IP). Tambahkan rate limit per IP pada reverse proxy tepercaya untuk mengurangi risiko lockout bersama. Pastikan DB dan file environment tidak disajikan sebagai static files; jangan cache HTML/API admin pada CDN. Backup database sebelum deployment. Tabel web_publications, web_admin_sessions, web_login_limits dibuat otomatis.

Halaman publik saat ini menganggap situs sebagai arsip komunitas non-resmi. Halaman Tentang menjelaskan layanan pihak ketiga; kontak pemilik/pengajuan penghapusan langsung belum tersedia karena alamat kontak belum dikonfigurasi.

## Verifikasi
- `npm run build`
- `npx tsc --noEmit` dan `npx eslint .`: keduanya tanpa temuan.
- `node c:\Sendy\jkt48-live\web\verify\public-private-check.mjs` (setelah build): DB fixture di direktori temp, server produksi pada port 3107, dibersihkan otomatis. Menguji default privat, DTO publik, gerbang watch/metadata, publikasi/penarikan, filter member/platform, auth admin, pemeriksaan origin, flag cookie, dan logout.
- `node c:\Sendy\jkt48-live\web\verify\auto-publish-check.mjs` (setelah build): dua server (port 3111 & 3112) pada satu fixture DB. Menguji rilis otomatis 72 jam, rekaman belum lewat ambang tetap tersembunyi, keputusan admin (terbit/tahan) menang atas aturan otomatis, jumlah arsip publik hanya menghitung rekaman yang tampil, serta `AUTO_PUBLISH_AFTER_HOURS=0` mematikan rilis otomatis.
- `node c:\Sendy\jkt48-live\web\verify\portal-browser-check.mjs`: halaman publik 1440px dan 390px, tanpa overflow, tanpa data operasional, admin dialihkan ke login (memerlukan dev/prod server di port 3000 dan Chrome dengan `--remote-debugging-port=9222`).
- `node c:\Sendy\jkt48-live\web\verify\player-trial-check.mjs` (setelah build): ujicoba pemutar di `/admin/trial`. Menguji landscape Showroom (16/9, tanpa tombol rasio vertikal) berbeda dari vertikal IDN (3/4, dengan tombol rasio), **fullscreen elemen landscape** (wrapper mengisi viewport, container 16/9, tanpa theater satu halaman, scroll tidak dikunci, pemutar tetap ter-mount) beserta pemulihan status tombol saat keluar, **geometri bilah progres tetap 4 px tanpa min-height/border/padding bocor dari gaya formulir admin**, pemutar ter-render di keduanya, dan akses tanpa sesi dialihkan ke `/login`. Server port 3110. Screenshot: `verify/trial-showroom-fullscreen.png`.
- `node c:\Sendy\jkt48-live\web\verify\player-check.mjs` (setelah build): Fixture DB dengan video yang dipublikasikan, server pada port 3108. Menguji player render, theater + fullscreen, 9 kombinasi rasio/responsif, identitas instance player, urutan Escape volume → theater, pemulihan fokus, persistensi mode fit setelah reload, dan tidak ada error hidrasi.
- Jalankan kedua skrip browser satu per satu, jangan bersamaan dengan `npm run build` (build menimpa `.next` yang sedang dipakai server tes).
- `verify/theater-check.cjs` dan `volume-check.cjs` lama tetap ada; keduanya memakai URL video tetap dan karena itu hanya berjalan bila video tersebut dipublikasikan.
- Rincian temuan lint lama dan penyelesaiannya ada di `PROBLEMS-REPORT.md`.
