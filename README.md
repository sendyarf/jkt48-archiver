# 🎀 JKT48 Live Downloader Bot

Bot Python otomatis untuk memantau, merekam, dan mendistribusikan siaran langsung (live) member JKT48 dari **IDN Live** ke **YouTube Unlisted** dan memberikan notifikasi ke channel **Telegram**.

---

## 🚀 Fitur & Keunggulan

- **Direct HLS Recording**: Merekam langsung dari URL HLS permanen member (AWS IVS), tidak bergantung pada polling endpoint website IDN.
- **HLS Seed Database**: URL HLS channel tiap member bersifat permanen — disimpan sekali via `seed_hls.py`, bot tidak perlu query IDN API untuk member yang sudah diketahui.
- **Smart HLS Discovery**: Untuk member baru yang belum diketahui HLS-nya, bot otomatis mendeteksi URL via IDN GraphQL saat member tersebut live, lalu menyimpannya permanen ke DB.
- **Telegram Direct Video Upload**: Mengirim file video rekaman langsung ke channel Telegram via Telegram API / Telethon dengan dukungan video streaming dan preview thumbnail otomatis.
- **Auto-Splitting (>2GB)**: Untuk mengatasi batas 2GB akun Telegram, video yang berukuran > 2GB (default: 1950 MB) otomatis di-split menjadi beberapa part (`[Part 1/N]`) secara lossless dan cepat menggunakan FFmpeg stream copy (`-c copy`).
- **YouTube Unlisted (Opsional)**: Opsi upload ke YouTube unlisted dengan rotasi multi-channel pool tetap tersedia jika sewaktu-waktu ingin digunakan kembali (`UPLOAD_TARGET=youtube`).
- **Pending Video Uploader**: Tersedia alat CLI `python3 -m bot.upload_pending` untuk mengunggah antrean video lama yang menumpuk di VPS.
- **Auto-Merge Reconnect (Satu Live = Satu Video)**: Segmen akibat lag/reconnect digabung ke satu video. Selesai dideteksi dari 4 pemicu (window sejak segmen terakhir, IDN "tidak live", idle HLS, hard cap) sehingga upload rata-rata hanya ±10–30 menit setelah live berakhir. Live **baru** dibedakan dari reconnect lewat slug sesi IDN (opsional) agar tidak tercampur.
- **IDN Lookup Opsional (HLS-first)**: Bot tetap berfungsi penuh tanpa IDN. Lookup IDN (best-effort, timeout 8s, cache 30s, tidak pernah melempar error) hanya dipakai untuk membedakan reconnect vs live baru dan mengambil judul live.
- **Merge Tahan Gangguan**: Segmen rusak dilewati, concat ffmpeg 2 varian, anti stream-hang (hard cap), dan `pm2 restart` tidak membuang segmen parsial (graceful SIGTERM + tunggu ±25s).
- **Status Viewer**: Pantau status bot real-time (recording aktif, antrian upload, HLS coverage) via CLI.
- **Kelola Channel via Telegram/CLI**: Tambah–stop–resume–hapus channel/member tanpa edit DB manual (`/stop jkt48-official`, `python3 -m bot.member_cli list`), lengkap dengan daftar member + HLS dan penghentian rekaman yang sedang berjalan.
- **Cleanup Tool**: Bersihkan sesi stale dan file lama dari disk & database via satu perintah.

---

## 🔄 Alur Kerja Bot

```
members.txt
     ↓ (Hot-Reload tiap siklus)
SQLite (member_hls)
     ↓
[Member sudah diketahui HLS-nya? (hls_confirmed)]
  → Ya : Langsung HTTP Health Check ke HLS URL (tanpa IDN API)
  → Tidak: IDN GraphQL query → simpan HLS ke DB jika member sedang live
     ↓
HTTP Health Check ke HLS URL tiap 15 detik (concurrent, max 20 paralel)
     ↓
Member Live Terdeteksi (HTTP 200 + #EXTM3U)
     ↓
Rekam dengan yt-dlp secara concurrent
     ↓
Stream Selesai → Merge Manager (Window 1 Jam)
     ↓
[Member reconnect dalam 1 jam?]
  → Ya : Lanjut rekam segmen berikutnya & reset timer
  → Tidak: Gabung segmen (FFmpeg Concat) jika > 1 part
     ↓
Upload ke YouTube Unlisted (pilih channel dengan upload paling sedikit)
  → Kuota Habis: Simpan di VPS, masuk antrian pending_upload
  → Berhasil: Kirim notifikasi link ke Telegram & hapus file lokal
```

---

## 🛠️ Instalasi di VPS Ubuntu

### 1. Update & Install Dependensi Sistem

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ffmpeg python3 python3-pip python3-venv git

# Install yt-dlp terbaru
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
sudo chmod +x /usr/local/bin/yt-dlp
```

Verifikasi:
```bash
yt-dlp --version
ffmpeg -version
```

### 2. Clone Repository & Setup Virtualenv

```bash
git clone https://github.com/sendyarf/jkt48-live.git
cd jkt48-live

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Konfigurasi `.env`

```bash
cp .env.example .env
nano .env
```

---

```dotenv
# Pilih satu: "telegram" atau "youtube". Berlaku GLOBAL untuk IDN dan Showroom.
UPLOAD_TARGET=youtube

# Zona waktu hanya untuk TAMPILAN. Waktu di database selalu UTC, sehingga
# VPS Seoul (UTC+9) menghasilkan angka yang sama dengan VPS Jakarta.
DISPLAY_TIMEZONE_OFFSET_HOURS=7
DISPLAY_TIMEZONE_LABEL=WIB
```

**Catatan zona waktu.** Sebelum perbaikan, kolom `started_at`/`download_ended_at` diisi
`datetime.now()` (jam dinding server) tanpa penanda zona waktu, sedangkan `created_at` diisi
SQLite sebagai UTC. Di server Seoul selisihnya 9 jam dan tidak ada cara mengetahui arti
angkanya. Sekarang semua waktu disimpan sebagai UTC dengan penanda `+00:00`; konversi ke WIB
hanya dilakukan saat menampilkan. Baris lama tanpa penanda tetap ambigu — bila offset server
lama diketahui, isi `LEGACY_NAIVE_TIME_OFFSET_HOURS` (mis. `8` atau `9`) agar nilai lama
dibaca benar.

**Catatan `UPLOAD_TARGET`.** Katalog website dibangun dari `live_sessions.youtube_video_id`.
Dengan `UPLOAD_TARGET=telegram`, rekaman baru **tidak akan pernah muncul di website**.
Rekaman YouTube diunggah sebagai **unlisted**, dan tetap tersembunyi dari situs sampai
diterbitkan admin atau lewat `AUTO_PUBLISH_AFTER_HOURS`. Rekaman **Showroom**
langsung tampil di situs tanpa menunggu ambang (aturan web
`AUTO_PUBLISH_AFTER_HOURS_SHOWROOM`, default `0`).

## ⚙️ Pengaturan Multi-Channel YouTube

1. Buka [Google Cloud Console](https://console.cloud.google.com/).
2. Buat Project baru atau gunakan project yang ada.
3. Aktifkan **YouTube Data API v3**.
4. Buat kredensial **OAuth 2.0 Client ID** (Desktop App).
5. Download JSON credentials dan simpan di project folder:
   - `client_secret.json` untuk channel 1
   - `client_secret_ch2.json` untuk channel 2, dst.
6. Jalankan autentikasi untuk masing-masing channel:
   ```bash
   # Autentikasi semua channel yang terkonfigurasi di .env:
   python3 -m bot.auth_youtube

   # Atau channel spesifik:
   python3 -m bot.auth_youtube --channel 1
   python3 -m bot.auth_youtube --channel 2
   ```

Contoh konfigurasi `.env`:
```env
# Channel 1
YT_CHANNEL_1_LABEL=Channel Utama
YT_CHANNEL_1_TOKEN=youtube_token.json
YT_CHANNEL_1_SECRET=client_secret.json

# Channel 2 (Opsional)
YT_CHANNEL_2_LABEL=Channel Backup
YT_CHANNEL_2_TOKEN=youtube_token_ch2.json
YT_CHANNEL_2_SECRET=client_secret_ch2.json
```

---

## 📋 Daftar Member (`members.txt`)

Isi `members.txt` dengan username member yang ingin dipantau (satu per baris):
```text
jkt48_freya
jkt48_christy
jkt48_delynn
```

Bot mendukung **Hot-Reload**: menambahkan member baru ke `members.txt` saat bot berjalan akan langsung dikenali tanpa perlu restart.

> ⚠️ **Cara stop/tambah channel yang benar** sekarang ada di bagian
> [🎛️ Kelola Channel](#️-kelola-channel-start--stop--tambah--cli--telegram) di bawah.
> Menghapus baris dari `members.txt` saja **tidak cukup** — status pemantauan
> disimpan di tabel `member_hls`.

---

## 🎛️ Kelola Channel (Start / Stop / Tambah) — CLI & Telegram

Sumber kebenaran pemantauan channel adalah tabel `member_hls` (kolom `enabled`):

| Nilai | Arti |
|---|---|
| `enabled = 1` | **AKTIF** — HLS di-probe tiap 15 detik, direkam saat member live |
| `enabled = 0` | **STOP** — dilewati monitor & tidak di-discovery ulang. HLS URL tetap tersimpan → resume instan |

### A. Via Telegram Admin Bot (paling praktis)

1. Buat bot di **@BotFather** → `/newbot` → copy token.
2. Isi `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456:AAE...   # token dari BotFather
   ADMIN_CHAT_ID=123456789            # chat ID Anda (wajib, untuk otorisasi)
   ADMIN_BOT_ENABLED=true             # listener jalan di dalam `python -m bot.main`
   ```
3. Buka chat bot Anda, kirim `/start` atau `/help`.

| Command | Fungsi |
|---|---|
| `/list [all\|active\|stopped\|live\|unknown]` | Daftar member + status + HLS |
| `/info <username>` | Detail 1 member (HLS URL, channel ID, status) |
| `/add <username> [Nama Tampilan]` | Tambah / aktifkan channel |
| `/stop <username>` | **Stop rekam** — rekaman yang sedang berjalan langsung dihentikan (graceful) |
| `/resume <username>` | Aktifkan kembali (HLS lama dipakai ulang) |
| `/remove <username>` | Hapus channel dari DB & `members.txt` |
| `/sethls <username> <url>` | Set HLS URL manual (bila channel ID sudah diketahui) |
| `/live` | Sesi yang sedang direkam / diupload |
| `/status` | Ringkasan + antrian upload |
| `/id` | Tampilkan chat ID Anda |

Nama pendek didukung: `/stop lulu` = `jkt48_lulu`.

Listener ini otomatis berjalan di dalam `python -m bot.main`. Untuk menjalankannya
terpisah: `python3 -m bot.admin_bot` (jangan jalankan bersamaan dengan bot utama,
karena Telegram hanya mengizinkan satu long-polling per token).

### B. Via CLI (tanpa Telegram)

```bash
python3 -m bot.member_cli list                  # semua member + status
python3 -m bot.member_cli list --mode stopped   # yang di-stop
python3 -m bot.member_cli list --hls            # sertakan URL HLS lengkap
python3 -m bot.member_cli info lulu             # nama pendek boleh
python3 -m bot.member_cli add jkt48_baru --name "Baru JKT48"
python3 -m bot.member_cli add jkt48_baru --hls "https://...channel.XXXX.m3u8"
python3 -m bot.member_cli stop jkt48-official   # stop 1 channel
python3 -m bot.member_cli resume jkt48-official
python3 -m bot.member_cli remove jkt48_lulu
python3 -m bot.member_cli set-hls jkt48_lulu "https://...channel.XXXX.m3u8"
```

### Cara kerja detail

- **STOP** → `enabled = 0` + baris `members.txt` ditandai `# STOPPED: <username>`.
  Berlaku pada siklus poll berikutnya (±15 detik) **tanpa restart**.
  Rekaman yang sedang berjalan dibatalkan seketika hanya lewat `/stop` Telegram
  (SIGTERM graceful ke yt-dlp; segmen yang sudah terekam tetap diupload setelah
  merge window). Dari CLI, rekaman aktif dibiarkan selesai sampai live berakhir.
- **TAMBAH** → baris `member_hls` dibuat dengan `enabled = 1`. Bila HLS belum
  diketahui, bot auto-discovery via IDN GraphQL saat member tersebut live
  (dicek tiap ~2 menit). Bila channel ID AWS IVS sudah diketahui, pakai
  `--hls` / `/sethls` agar langsung siap.
- `sync_members_whitelist()` **tidak** menghidupkan kembali member yang di-stop
  (hanya menambah member baru dengan `INSERT ... DO NOTHING`), jadi status STOP
  tetap aman walau bot restart.

---

## 🔀 Kebijakan Merge & Reconnect (Satu Live = Satu Video)

Live member sering **lag lalu terputus dan reconnect**. Bot menangani ini dengan
menggabungkan semua potongan (`segmen`) ke satu **merge group per member**, lalu
meng-upload setelah live benar-benar selesai.

### Kapan video dianggap selesai & siap upload?

| # | Pemicu | Default | Env |
|---|---|---|---|
| 1 | Habis `MERGE_WINDOW_SECONDS` sejak **segmen terakhir** — **satu-satunya penentu yang andal** | 3600s (1 jam) | `MERGE_WINDOW_SECONDS` |
| 2 | **Hard cap** umur grup (anti stream "hang" tanpa disconnect) | 6 jam | `MERGE_MAX_GROUP_HOURS` |
| 3 | (Opsional, **default NONAKTIF**) HLS offline & idle ≥ N detik | 0 = off | `MERGE_IDLE_FINALIZE_SECONDS` |
| 4 | (Opsional, **default NONAKTIF — tidak andal**) judul live berubah | false | `MERGE_SPLIT_ON_TITLE_CHANGE` |

Kapan sebuah **segmen baru** masuk ke grup yang sedang menunggu? Window diukur sebagai
**jeda liputan**:

```
(kapan segmen baru MULAI) − (kapan segmen terakhir grup SELESAI) ≤ MERGE_WINDOW_SECONDS
```

Bukan "sekarang − segmen terakhir". Perbedaannya menentukan: satu segmen bisa berdurasi
lebih panjang daripada window (live 2 jam tanpa reconnect sama sekali). Dengan pembanding
"sekarang", begitu segmen panjang itu selesai jaraknya menjadi > window, grup lama
dianggap kedaluwarsa, dan segmen panjang tadi membuka **grup baru** — satu live jadi dua
video. Itu persis insiden `jkt48_michie` (18 Sep 2026): dua video 22:16 & 22:18 WIB
padahal jeda live hanya ~1 menit. Dengan jeda liputan, yang diukur adalah lubang rekaman
sebenarnya, sehingga segmen tetap satu live selama lubangnya ≤ window.

Finalize **selalu ditunda** selama masih ada rekaman berjalan (tidak pernah memotong
video di tengah rekaman).

### Kenapa video dipisah? (Bukti lapangan)

Slug **maupun** judul IDN tidak bisa dipakai untuk menyimpulkan "live baru" — dua kasus
nyata justru berlawanan arah:

| Kasus | Slug | Judul | Jeda | Kenyataannya |
|---|---|---|---|---|
| `jkt48_daisy` (13 Sep 2026) | **berubah** tiap reconnect | **sama** (`haii`) | sampai **±51 menit** | **satu** live |
| `jkt48_carissa` (15 Sep 2026) | berubah | **berubah** (member mengedit judul) | 1–8 menit | **satu** live |

```
jkt48_daisy:    haii-260913192155 -> haii-260913201248 -> haii-260913201503
jkt48_carissa:  my-bestie-ku-sini-join-260915164353
                my-bestie-ku-sini-join-260915165114
                sini-my-bestieee-260915165258
                sini-my-bestie-akuu-260915165451        → semuanya SATU live
```

Karena itu **penentu yang dipakai hanya jeda waktu**: reconnect dengan jeda liputan
(mulai segmen berikutnya − akhir segmen sebelumnya) kurang dari `MERGE_WINDOW_SECONDS`
digabung menjadi satu video; kalau lubangnya lebih besar, dianggap live terpisah.
Judul live (`live_key`) tetap disimpan, tapi hanya untuk caption/log dan tampilan.

> ⚠️ Karena alasan di atas, `MERGE_SPLIT_ON_TITLE_CHANGE` sebaiknya **dibiarkan `false`**.
> Kalau fitur itu dinyalakan, live Carissa (judul diedit 3x dalam 11 menit) akan terpecah
> menjadi 3 video.

Kapan IDN dipakai? Hanya untuk (a) mengambil judul live untuk caption dan (b)
memberi sinyal tambahan saat `_decide` berjalan — kalau IDN tidak tersedia
(`IDN_LOOKUP_ENABLED=false` atau server IDN sedang down), bot tetap berfungsi penuh
dengan HLS + jeda waktu saja.

### Ketahanan (robustness)

- **Segmen rusak/hilang** (file 0 byte) dilewati saat merge, tidak membuat seluruh
  penggabungan gagal (ditandai `failed` agar tidak diadopsi ulang).
- **ffmpeg concat** dicoba 2 varian (`-c copy`, lalu remux `+genpts`) sebelum
  menyerah ke mode "kirim segmen terpisah".
- **Stream hang** (playlist masih HTTP 200 tapi tidak ada data baru) tidak menunda
  upload tanpa batas berkat hard cap.
- **`pm2 restart` aman**: bot menghentikan yt-dlp secara graceful (SIGTERM) dan
  menunggu segmen tersimpan (±25s) sebelum keluar, sehingga segmen parsial tidak
  hilang. Grup `waiting` dijadwalkan ulang otomatis saat boot.

---

## 📺 Bot Showroom (Fase 2)

Bot memantau room Showroom member **berdasarkan API resmi** (`/api/room/profile`),
bukan menebak dari ada/tidaknya HLS. Deteksi memakai field `is_onlive`, dan
identitas sesi memakai `live_id = sr_<room_url_key>_<epoch>` sehingga tidak bisa
bertabrakan dengan format IDN `<username>_<epoch>`.

**Default NONAKTIF.** Aktifkan hanya setelah room di-seed:

```bash
python3 -m bot.seed_showroom --dry-run   # lihat rencana
python3 -m bot.seed_showroom             # tulis 58 room
# lalu di .env:
# SHOWROOM_ENABLED=true
pm2 restart jkt48-archiver-bot
```

Selama `SHOWROOM_ENABLED=false`, bot berjalan **persis seperti sebelumnya** —
jalur Showroom tidak pernah dijalankan.

### Ketahanan rekaman (pelajaran insiden 18 Sep 2026)

Live Sona 18 Sep 20:18:39 WIB terekam hanya 1:16:17 padahal live berjalan
±1:27:32 — bot baru berhasil merekam ±8-10 menit **setelah** live dimulai,
karena HLS Showroom belum "feeding" saat deteksi: task gagal cepat, lalu
menunggu siklus deteksi berikutnya, berulang-ulang. Perekam VOD pembanding
tidak mengalami ini karena arsip Showroom selalu mulai dari detik pertama.

Sekarang task rekaman Showroom **tetap tinggal** sampai live benar-benar
berakhir:

* Kalau yt-dlp berhenti lebih awal (HLS belum feeding, token kadaluarsa,
  hiccup CDN), task resume dengan URL HLS segar dari API Showroom; setiap
  potongan resume jadi `live_id` `_r<N>` tersendiri dan tetap masuk **satu
  merge group** yang sama.
* Task hanya menyerah bila room terbukti offline (debounce) atau kuota
  resume (`SHOWROOM_MAX_RESUMES`) habis — jeda reconnect lebih panjang
  ditangani deteksi ulang di loop utama seperti IDN.
* Bot memindai Showroom **langsung saat start** (tidak menunggu satu siklus)
  dan mencatat di log berapa detik jeda antara live dimulai vs mulai merekam
  (`⚠️ ... mulai merekam Ns setelah live dimulai`) supaya kejadian seperti
  ini langsung terlihat.

### Kebijakan waktu (keputusan D4)

Kapan satu live Showroom dinyatakan selesai **sama dengan IDN**:
tunggu `MERGE_WINDOW_SECONDS` (1 jam) sejak segmen terakhir. Ditambah satu
pengaman khusus Showroom: sinyal "room offline" hanya sah setelah
`SHOWROOM_OFFLINE_CONFIRMATIONS` pembacaan offline **berturut-turut**.

Debounce ini **hanya memperlambat**, tidak pernah mempercepat — sebelum
terkonfirmasi hasilnya "tidak diketahui", sehingga satu hiccup API tidak pernah
memecah satu live menjadi beberapa video. Ini menyerang penyebab split yang
terukur pada data IDN (8 kasus, jeda hanya 5 detik–16 menit).

### Yang dipisahkan dari IDN

| Aspek | Perilaku |
|---|---|
| Probe status | IDN → cek URL HLS tetap; Showroom → API room ber-debounce |
| IDN lookup | **Tidak dipanggil sama sekali** untuk sesi Showroom (judul IDN tidak relevan dan bisa menahan finalisasi) |
| Merge group | Difilter per `platform`, jadi live IDN & Showroom orang yang sama **tidak bercampur** |
| Timer & status "sedang merekam" | Kunci per `(member, platform)` |
| Judul grup | Nama room Showroom (IDN memakai judul live IDN) |
| Thumbnail | Cover room Showroom |
| Interval polling | `SHOWROOM_CHECK_INTERVAL_SECONDS` (default 30s), terpisah dari interval IDN |

### Beban yang perlu diantisipasi

58 room pada interval IDN (5 detik) berarti **~11,6 request/detik** ke API
Showroom — berisiko rate-limit. Karena itu Showroom memakai interval sendiri.
`SHOWROOM_CONCURRENCY` membatasi request bersamaan per siklus.

### ⚠️ Yang belum terverifikasi

- **Rekaman Showroom nyata belum pernah dijalankan.** Saat dikerjakan, tidak ada
  satu pun room yang sedang live (58/58 offline), sehingga jalur `yt-dlp` →
  Showroom HLS **belum diuji terhadap stream sungguhan**. Semua logika lain
  (deteksi, debounce, identitas sesi, pemisahan grup) diuji dengan scraper palsu.
- Format/bitrate video Showroom (landscape) belum diukur, jadi perkiraan disk
  masih memakai laju IDN.
- Perilaku API Showroom **saat room sedang live** belum diamati.
- `MERGE_IDLE_FINALIZE_SECONDS` default `0` (nonaktif). Bila dinaikkan, untuk
  Showroom ia hanya bisa terpicu setelah offline benar-benar terkonfirmasi.

---

## 🌱 Seed HLS Database (Penting — Jalankan Sekali)

URL HLS channel AWS IVS tiap member bersifat **permanen** (tidak berubah antar sesi live). Jalankan seed sekali agar bot tidak perlu query IDN API untuk member yang sudah diketahui:

```bash
python3 -m bot.seed_hls
```

Member yang channel ID-nya belum diketahui akan tetap di-probe via IDN GraphQL secara otomatis saat mereka live pertama kali, lalu disimpan ke DB.

---

## 📺 Seed Room Showroom (Fase 1)

Daftar room Showroom member JKT48 ada di `showroom_rooms.json`. Salin pasangan
`username → room_id` ke tabel `member_hls` dengan:

```bash
python3 -m bot.seed_showroom              # tulis ke database
python3 -m bot.seed_showroom --dry-run    # lihat rencana saja, tanpa menulis
```

**Skrip ini idempoten dan tidak mengubah perilaku bot.** Yang ditulis hanya kolom
Showroom (`showroom_room_id`, `showroom_name`, `showroom_only`). Kolom IDN
(`hls_url`, `hls_confirmed`) dan `enabled` tidak disentuh — member yang sedang
di-stop tetap ter-stop.

Catatan:

- `display_name` hanya diisi bila masih kosong atau masih sama dengan username.
  Nama yang sudah diisi manusia (mis. `Raisha JKT48`) tidak ditimpa.
- Room official JKT48 (`official_rooms` di JSON) sengaja tidak dipantau.
- Rekaman Showroom aktif hanya bila `SHOWROOM_ENABLED=true` (lihat bagian **Bot Showroom (Fase 2)** di atas).

---

## 🛠️ Variabel Environment Showroom (opsional)

| Variabel | Default | Fungsi |
|---|---|---|
| `SHOWROOM_ENABLED` | `false` | Mengaktifkan jalur deteksi & rekam Showroom |
| `SHOWROOM_CHECK_INTERVAL_SECONDS` | `30` | Interval probe API Showroom (dipisah dari cek HLS IDN) |
| `SHOWROOM_CONCURRENCY` | `10` | Batas request Showroom bersamaan per siklus |
| `SHOWROOM_TIMEOUT_SECONDS` | `8` | Timeout satu panggilan API Showroom |
| `SHOWROOM_OFFLINE_CONFIRMATIONS` | `3` | Jumlah pembacaan offline berturut-turut sebelum offline dianggap sah (debounce) |
| `SHOWROOM_ROOMS_FILE` | `showroom_rooms.json` | Sumber daftar room untuk seed |
| `SHOWROOM_EMPTY_RETRIES` | `30` | Percobaan ulang yt-dlp saat HLS belum menghasilkan output (task tetap menunggu stream feeding) |
| `SHOWROOM_MAX_RESUMES` | `40` | Maksimal resume per sesi saat yt-dlp berhenti lebih awal padahal live masih jalan |
| `SHOWROOM_RESUME_DELAY_SECONDS` | `10` | Jeda sebelum tiap percobaan resume |
| `SHOWROOM_LATE_START_WARN_SECONDS` | `60` | Log peringatan bila mulai merekam N detik setelah live resmi dimulai |

---

## ▶️ Menjalankan Bot

### Jalankan Langsung:
```bash
python3 -m bot.main
```

### Jalankan di Background via PM2 (DISARANKAN):
```bash
pm2 start deploy/ecosystem.config.js   # bot + website sekaligus
pm2 save
pm2 logs
```

> **Penting:** konfigurasi di `deploy/ecosystem.config.js` menetapkan
> `kill_timeout: 35000` untuk bot. Perintah `pm2 start` biasa memakai
> `kill_timeout` bawaan 1600 ms, sehingga PM2 akan SIGKILL bot **sebelum** ia
> selesai menyimpan segmen (Graceful shutdown menunggu `GRACEFUL_SHUTDOWN_SECONDS`,
> default 25 detik) dan segmen parsial bisa hilang.

Menjalankan bot saja, tanpa website:
```bash
pm2 start "python3 -m bot.main" --name jkt48-archiver-bot
```

### Website (Next.js)

Panduan lengkap deploy domain, nginx, dan HTTPS ada di **[`deploy/README.md`](deploy/README.md)**.

---

## 📊 Memantau Status Bot

```bash
# Tampilkan status sekali (recording aktif, HLS coverage, YouTube quota)
python3 -m bot.status

# Auto-refresh setiap 10 detik
python3 -m bot.status --watch

# Custom interval (detik)
python3 -m bot.status --watch --interval 30
```

Contoh output:
```
=================================================================
  JKT48 Live Bot Status — 2026-09-13 21:00:00
=================================================================

📺 YouTube Channels (1)

  📺 Channel Utama
     Token  : ✅ Valid + refresh_token
     Upload : 3x hari ini  (quota ~4,800/10,000 units)
     Sisa   : [█████████░░░░░░░░░░░] ~5,200 units (3 upload lagi)

📡 HLS Coverage: 52/58 member (confirmed: 52)
   ❓ Belum diketahui HLS: jkt48_feni, jkt48_kimmy, ...

─────────────────────────────────────────────────────────────────
  🎙  Sesi Aktif (5)

  ⏺  Levi JKT48            downloading          45.2 MB  (3m 12s ago)
  ⏺  Daisy JKT48           downloading          22.1 MB  (3m 12s ago)
  ✅ Jazzy JKT48           segment_done         90.8 MB  (5m ago)
```

---

## 🗑️ Membersihkan File Lama

```bash
# Preview sesi stale yang akan dihapus (>24 jam) — tidak hapus apapun
python3 -m bot.status --cleanup --dry-run

# Hapus file & update DB (default: >24 jam)
python3 -m bot.status --cleanup

# Custom threshold
python3 -m bot.status --cleanup --hours 48
```

---

## 🗂️ Struktur Database (SQLite)

| Tabel | Fungsi |
|---|---|
| `live_sessions` | Semua sesi live yang pernah terdeteksi & statusnya |
| `merge_groups` | Grup rekaman yang perlu di-merge (reconnect window) |
| `member_hls` | URL HLS permanen tiap member + flag `hls_confirmed` & `enabled` (stop/aktif) |
| `youtube_channels` | Daftar channel YouTube, token, & counter upload harian |

### Status Sesi (`live_sessions.status`)

| Status | Keterangan |
|---|---|
| `detected` | Live terdeteksi, belum mulai download |
| `downloading` | Sedang direkam |
| `segment_done` | Segmen selesai, menunggu merge window |
| `download_complete` | Siap upload |
| `merging` | Sedang di-merge FFmpeg |
| `uploading_telegram` | Sedang diupload ke Telegram channel |
| `done_telegram` | Berhasil diupload ke Telegram channel |
| `uploading_youtube` | Sedang diupload ke YouTube |
| `done_youtube` | Berhasil diupload ke YouTube |
| `pending_upload` | Menunggu antrian retry upload |
| `failed` | Gagal permanen |
