# Deploy — jkt48.vidx.download

Runbook untuk menjalankan arsip JKT48 di VPS Linux dengan domain
`https://jkt48.vidx.download`.

> **Status:** konfigurasi nginx dan PM2 di folder ini **belum diuji** dengan
> nginx/PM2 sungguhan (keduanya tidak tersedia di lingkungan pengembangan
> proyek). Selalu jalankan `nginx -t` sebelum reload, dan periksa `pm2 logs`
> setelah start. Bagian yang sudah diuji adalah kode aplikasi (build, gerbang
> publik/privat, pemutar, dan perilaku cookie/origin).

## Arsitektur

```
Internet
   │  https://jkt48.vidx.download
   ▼
nginx (443, TLS)  ──►  127.0.0.1:3000  ─►  Next.js (PM2: jkt48-web)
                                              │
                                              ▼
                                     jkt48_live.db (SQLite, ditulis bersama)
                                              ▲
                                              │
                                     Bot Python (PM2: jkt48-archiver-bot)
```

Port 3000 **sengaja hanya mendengarkan loopback** (`-H 127.0.0.1`), jadi tidak
bisa diakses langsung dari internet. Semua trafik wajib lewat nginx.

## Prasyarat

| Kebutuhan | Catatan |
|---|---|
| VPS Linux + IP publik | |
| Node.js ≥ 20 | Untuk Next.js 16 |
| Python 3 + venv | Untuk bot |
| ffmpeg, yt-dlp | Untuk perekaman & penggabungan |
| PM2 | `sudo npm i -g pm2` |
| nginx + certbot | `sudo apt install nginx certbot python3-certbot-nginx` |

## 1. DNS

Tambahkan satu record di pengelola DNS domain `vidx.download`:

```
Tipe : A
Nama : jkt48
Nilai: <IP_PUBLIK_VPS>
```

> **Bila memakai Cloudflare:** simpan record ini dengan **Proxy status OFF
> (awan abu-abu, "DNS only")** untuk sementara. Proxy dinyalakan **setelah**
> sertifikat terbit di langkah 6 — lihat [Cloudflare](#cloudflare-disarankan).
> TTL `Auto` sudah benar.

Untuk IPv6 tambahkan record `AAAA`. Tunggu propagasi, lalu pastikan:

```bash
dig +short jkt48.vidx.download
```

Harus mengembalikan IP VPS Anda. **Jangan lanjut sebelum ini benar** — certbot
akan gagal bila DNS belum mengarah ke server.

## 2. Ambil kode

```bash
git clone https://github.com/sendyarf/jkt48-live.git
cd jkt48-live
```

## 3. Konfigurasi environment

### `.env` (root, untuk bot)

```bash
cp .env.example .env
nano .env
```

Wajib diperhatikan:

- **`DB_PATH`** — jadikan **absolut**, mis. `/home/USER/jkt48-live/jkt48_live.db`.
  Bot dan web harus menunjuk berkas yang sama; nilai relatif bisa menghasilkan
  dua database berbeda tanpa pesan error apa pun.
- **`UPLOAD_TARGET`** — katalog website dibangun **hanya** dari
  `youtube_video_id`. Selama nilainya `telegram`, rekaman baru **tidak akan
  pernah muncul di website**.
- **`YOUTUBE_PLAYER_BASE_URL`** — bila `UPLOAD_TARGET=youtube`, isi dengan
  `https://jkt48.vidx.download` (tanpa `/play`, lihat bagian Catatan).

### `web/.env` (untuk website)

```bash
cp web/.env.example web/.env
nano web/.env
```

Isi ketiganya:

```dotenv
DB_PATH=/home/USER/jkt48-live/jkt48_live.db
ADMIN_SECRET=<hasil openssl rand -hex 32>
APP_ORIGIN=https://jkt48.vidx.download
```

`ADMIN_SECRET` minimal 32 karakter. Bila kurang, login admin **dinonaktifkan**
dan isi situs tidak bisa dipublikasikan.

### Pengembangan lokal

`web/.env.development.local` menimpa `APP_ORIGIN` menjadi
`http://localhost:3000` **hanya saat `npm run dev`**, supaya login lokal tetap
bisa dipakai. Berkas itu tidak dimuat oleh `npm start`, jadi aman berada di
repo (dan diabaikan git).

## 4. Build website

```bash
cd web
npm ci
npm run build
cd ..
```

`npm ci` (bukan `npm install`) agar versi paket persis seperti `package-lock.json`.

## 5. Jalankan dengan PM2

```bash
mkdir -p logs
pm2 start deploy/ecosystem.config.js
pm2 save
pm2 startup        # ikuti perintah yang dicetak agar hidup lagi setelah reboot
pm2 logs
```

Konfigurasi PM2 memuat satu detail penting: bot memerlukan `kill_timeout` 35 detik
karena ia menunggu segmen aktif tersimpan (default `GRACEFUL_SHUTDOWN_SECONDS`
adalah 25 detik). `kill_timeout` bawaan PM2 hanya 1600 ms, sehingga tanpa
pengaturan ini `pm2 restart` akan SIGKILL bot lebih dulu dan **segmen parsial
hilang** — kebalikan dari jaminan di README.

## 6. nginx + HTTPS

> **Urutan ini penting.** Konfigurasi nginx di repo menunjuk berkas sertifikat di
> `/etc/letsencrypt/live/jkt48.vidx.download/`. Selama sertifikat itu belum ada,
> `nginx -t` akan **gagal** dan nginx tidak mau start. Jadi sertifikat diterbitkan
> **lebih dulu**, baru konfigurasi dipasang.

**Langkah 1 — terbitkan sertifikat tanpa nginx.** certbot memakai port 80 sendiri,
dan ini bekerja karena DNS sudah mengarah ke server serta proxy Cloudflare masih
OFF:

```bash
sudo systemctl stop nginx
sudo certbot certonly --standalone -d jkt48.vidx.download
sudo systemctl start nginx
```

**Langkah 2 — pasang konfigurasi** (sekarang berkas sertifikat sudah ada):

```bash
sudo cp deploy/nginx/jkt48.vidx.download.conf /etc/nginx/sites-available/jkt48.vidx.download
sudo ln -s /etc/nginx/sites-available/jkt48.vidx.download /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Bila situs bawaan nginx masih aktif dan mengganggu, nonaktifkan dengan
`sudo rm -f /etc/nginx/sites-enabled/default` lalu `sudo nginx -t` lagi.

Perpanjangan otomatis biasanya sudah dipasang oleh paket certbot. Verifikasi:

```bash
sudo certbot renew --dry-run
```

Setelah sertifikat ada, perpanjangan otomatis akan memakai plugin `standalone`
yang mencoba menghentikan nginx sementara. Bila Anda ingin perpanjangan tanpa
downtime, tambahkan hook berikut setelah instalasi berhasil:

```bash
sudo certbot certonly --webroot -w /var/www/html -d jkt48.vidx.download   # ganti metode ke webroot
```

Metode `--webroot` bisa dipakai karena blok `location /.well-known/acme-challenge/`
sudah disediakan di konfigurasi nginx (jalur itu sengaja dikecualikan dari redirect).


### Cloudflare (disarankan)

Proxy Cloudflare **boleh dan disarankan diaktifkan**, tetapi urutannya penting.
Mengaktifkannya terlalu dini adalah penyebab kegagalan paling umum.

**Urutan yang benar:**

1. Record `A` tetap **DNS only** (awan abu-abu) saat menjalankan certbot di langkah 6.
2. Pastikan origin sudah melayani HTTPS dengan sertifikat valid:
   ```bash
   curl -sI --resolve jkt48.vidx.download:443:<IP_PUBLIK_VPS> https://jkt48.vidx.download | head -1
   ```
   Harus `200`/`301`, **bukan** error sertifikat.
3. Baru ubah Proxy status menjadi **Proxied** (awan oranye) di Cloudflare.
4. Di **SSL/TLS → Overview**, set mode ke **Full (strict)**.

> ⛔ **Jangan pernah memakai mode SSL/TLS "Flexible".** Konfigurasi nginx di repo
> ini mengalihkan seluruh HTTP ke HTTPS (kecuali jalur ACME). Dengan mode
> Flexible, Cloudflare menghubungi origin lewat HTTP, menerima `301` itu, lalu
> mengulanginya terus — browser menampilkan `ERR_TOO_MANY_REDIRECTS` dan situs
> tidak bisa dibuka sama sekali. Mode **Full (strict)** juga wajib agar Cloudflare
> memverifikasi sertifikat origin, sehingga tidak ada celah penyadapan di antara
> Cloudflare dan server Anda.

**Setelan yang perlu dimatikan:**

| Setelan | Lokasi | Alasan |
|---|---|---|
| **Rocket Loader** | Speed → Optimization | Menunda eksekusi JS dan dapat merusak hidrasi Next.js |
| **Bot Fight Mode / JS Challenge** pada `/login` | Security → Bots | Tantangan JS bisa mengunci Anda sendiri dari panel admin |

**Setelan yang perlu dibiarkan apa adanya:**

- **Jangan** membuat Cache Rule / Page Rule "Cache Everything" untuk HTML.
  Halaman `/admin/*` memuat data operasional; menyimpannya di edge berisiko
  menyajikan halaman admin basi, dan cache tidak boleh menyentuh area itu.
  Cloudflare secara default **tidak** men-cache HTML, jadi tidak perlu diubah.
- Aset `/_next/static/*` tetap aman di-cache Cloudflare karena namanya ber-hash.

**Manfaat nyata untuk proyek ini:**

1. **Melindungi `/api/auth`.** Pembatasan percobaan login di
   `web/lib/auth.ts` memakai **satu ember global** (`bucket = 'admin'`,
   20 percobaan per 15 menit) dan tidak melihat IP pengirim. Artinya siapa pun
   bisa mengunci panel admin Anda selama 15 menit hanya dengan 20 permintaan
   gagal. WAF + Rate Limiting Cloudflare di depan `/api/auth` menutup celah itu.
2. **Menyembunyikan IP origin.** Tanpa proxy, siapa pun yang tahu
   `18.202.196.37` bisa menembus nginx dan WAF langsung ke server Anda.
3. **Latensi lebih rendah** bagi penonton di Indonesia: aset statis disajikan
   dari PoP Cloudflare terdekat, bukan dari lokasi VPS.

Kode aplikasi **tidak perlu diubah** untuk proxy: satu-satunya hal yang
diperiksa saat login adalah header `Origin` dari browser, dan nilainya tetap
`https://jkt48.vidx.download` baik proxy aktif maupun tidak. Tidak ada bagian kode
yang membaca IP klien atau header `X-Forwarded-*`.

## 7. Verifikasi setelah go-live

```bash
# 1. HTTPS aktif dan mengalihkan dari HTTP
curl -sI http://jkt48.vidx.download | head -1          # harapan: 301
curl -sI https://jkt48.vidx.download | head -1         # harapan: 200

# 2. Halaman publik tampil
curl -s https://jkt48.vidx.download | grep -c "JKT48"

# 3. Halaman admin terlindungi (harapan: 307 ke /login)
curl -sI https://jkt48.vidx.download/admin/status | grep -i location

# 4. Port 3000 TIDAK boleh terbuka dari luar
curl -sI --max-time 5 http://<IP_PUBLIK>:3000 | head -1   # harapan: gagal/timeout
```

Terakhir, uji **login sungguhan di browser** ke
`https://jkt48.vidx.download/login`. Ini wajib: pemeriksaan origin hanya bisa
dibuktikan lewat browser dengan domain sebenarnya.

## 8. Publikasikan rekaman

Semua rekaman **tersembunyi secara default**. Setelah login:

1. Buka `/admin/publications`
2. Tinjau setiap rekaman, lalu pilih **Terbitkan**

Halaman publik tetap kosong sampai langkah ini dilakukan. Ini disengaja, bukan
kerusakan.

## Catatan yang mudah terlewat

- **HTTPS bukan pilihan.** Cookie sesi memakai flag `Secure`. Diuji di browser:
  lewat HTTP pada domain asli, API login membalas 200 **tetapi browser membuang
  cookie-nya**, sehingga pengguna terus terlempar kembali ke halaman login.
  Perilaku ini tidak muncul di `localhost` karena browser mengecualikannya
  sebagai origin tepercaya — itulah sebabnya masalah ini hanya terlihat di
  domain sungguhan.
- **`APP_ORIGIN` harus sama persis** dengan yang diketik pengunjung
  (`https://jkt48.vidx.download`, tanpa trailing slash). Bila berbeda, login
  ditolak **403**. Tidak ada fallback yang bisa diandalkan di belakang proxy:
  Next.js mengabaikan header `Host` saat menyusun `request.url`.
- **Bila domain berubah**, ubah `APP_ORIGIN` di `web/.env`, `server_name` di
  konfigurasi nginx, dan path sertifikat. Mengganti `ADMIN_SECRET` otomatis
  membatalkan semua sesi admin yang sedang aktif.
- **Rute `/play` tidak ada.** `bot/telegram_sender.py` menyusun
  `{YOUTUBE_PLAYER_BASE_URL}?replay=<id>`. Jadi isi
  `YOUTUBE_PLAYER_BASE_URL=https://jkt48.vidx.download/play` akan menghasilkan
  tautan **404**. Saat ini tidak aktif karena `UPLOAD_TARGET=telegram`, tetapi
  akan terpicu begitu target diubah ke YouTube.
- **SQLite dipakai bersama** oleh bot dan web. Mode jurnal saat ini `delete`
  dengan `busy_timeout = 0`, sehingga penulisan bersamaan berpotensi memunculkan
  `database is locked`. Pertimbangkan mengaktifkan WAL.
- **Jangan menjalankan `npm run build` bersamaan dengan web yang sedang
  melayani**: build menimpa folder `.next` yang sedang dipakai.

## Troubleshooting

| Gejala | Penyebab yang paling mungkin |
|---|---|
| Login selalu 403 | `APP_ORIGIN` tidak sama persis dengan origin di browser |
| `ERR_TOO_MANY_REDIRECTS` | Mode SSL/TLS Cloudflare masih **Flexible**; ubah ke Full (strict) |
| Situs berhenti saat proxy dinyalakan | Mode Full tapi origin belum punya sertifikat valid |
| Login 200 tapi tetap di halaman login | Diakses lewat HTTP, bukan HTTPS |
| Login 503 | `ADMIN_SECRET` kosong atau kurang dari 32 karakter |
| Halaman publik kosong | Belum ada rekaman yang dipublikasikan di `/admin/publications` |
| Rekaman tidak bertambah di web | `UPLOAD_TARGET=telegram` (web hanya membaca `youtube_video_id`) |
| Web menampilkan data berbeda dari bot | `DB_PATH` di `web/.env` menunjuk berkas lain |
| `database is locked` | Bot dan web menulis bersamaan; aktifkan WAL + `busy_timeout` |