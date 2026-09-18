# Rencana Teknis: Dukungan Showroom (JKT48 Live)

Status: **rencana saja — belum ada kode yang diubah.**
Tanggal: 18 Sep 2026. Cakupan: bot (`bot/`) dan website (`web/`).

## 1. Tujuan

Menambah Showroom sebagai platform kedua di samping IDN Live, dengan urutan aman:
database dulu → deteksi & rekam → penggabungan segmen → (opsional) YouTube + katalog publik.

## 2. Keputusan yang masih dibutuhkan dari pemilik proyek

| # | Pertanyaan | Status |
|---|---|---|
| D1 | Target upload Showroom | **Dijawab:** boleh Telegram atau YouTube |
| D1b | Target upload Showroom | **Dijawab:** bersifat **global** — bila `UPLOAD_TARGET=youtube`, maka IDN **dan** Showroom sama-sama ke YouTube. Tidak perlu target per platform |
| D2 | Username untuk member Showroom-only | **Tidak berlaku:** 58/58 room berpasangan dengan username IDN |
| D3 | Cakupan pemantauan | **Dijawab:** pantau semua room member JKT48 (kini **58 room**; official 332503 dikecualikan) |
| D4 | Kapan satu live Showroom dinyatakan selesai | **Dijawab:** sama seperti IDN — tunggu `MERGE_WINDOW_SECONDS` (1 jam) sejak segmen terakhir. Ditambah debounce sinyal offline Showroom (lihat §6.2) |

## 3. Fakta hasil verifikasi (bukan asumsi)

### 3.1 API Showroom (`GET /api/room/profile?room_id=...`)

Field yang tersedia dan relevan (contoh nyata room Feni `317738`):

- `is_onlive` → `false` (status live, boolean)
- `live_id` → `0` saat offline (ID sesi Showroom)
- `current_live_started_at` → `0` saat offline (epoch mulai live)
- `room_url_key` → `"JKT48_Feni"` (slug stabil, berguna untuk nama file & URL)
- `image` → cover `_m.jpeg` (**bisa dipakai sebagai thumbnail di web**)
- `image_square`, `main_name`, `room_name`, `follower_num`, `view_num`, `is_official`

`GET /api/live/streaming_url?room_id=...` mengembalikan `{"streaming_url_list": []}` saat offline.

Kesimpulan penting: `is_onlive` + `live_id` + `current_live_started_at` cukup untuk deteksi dan
identitas sesi. Catatan: saat room offline, `live_id` bernilai `0`, sehingga **identitas sesi tidak
bisa dibaca selama jeda reconnect** — ini memengaruhi strategi merge di §6.

### 3.2 Kode bot saat ini

- `bot/showroom_scraper.py` **ada** (`get_room_profile`, `get_live_streaming_url`, `is_room_live`) dan
  punya tes `tests/test_showroom_scraper.py`, tetapi **tidak diimpor** oleh `main.py`, `merger.py`,
  `member_manager.py`, atau `downloader.py`. Efektif: kode mati.
- `bot/downloader.py` → `download_stream(hls_url, member_username, live_id, ...)` memakai yt-dlp + ffmpeg
  dan **tidak mengandung logika IDN**. Bisa dipakai ulang untuk Showroom.
- `bot/main.py` → `live_id = f"{u}_{int(datetime.now().timestamp())}"` lalu
  `database.insert_live(live_id, member_username, member_name, started_at, hls_url)`.
- `bot/main.py` memilih kandidat dari `database.get_all_member_hls(include_disabled=False)` yang punya
  `hls_url`, lalu `hls_monitor.check_active_members()` (HTTP check ke URL HLS permanen).
- `bot/hls_discovery.py` mengisi `hls_url` member yang belum punya (khusus IDN, via GraphQL IDN).
- Identitas "live baru vs reconnect" (`merge_groups`) saat ini memakai **judul live IDN**
  (`idn_lookup.live_key_from(live_slug, live_title)`), karena slug IDN berubah tiap reconnect.
- `bot/config.py` sudah punya `IDN_LOOKUP_ENABLED` sebagai pola feature flag, dan
  `MERGE_IDLE_FINALIZE_SECONDS` **default 0 = selalu tunggu window penuh** (ada catatan kasus
  reconnect > 50 menit pada jkt48_daisy).

### 3.3 Skema database saat ini

`live_sessions(live_id UNIQUE, member_username, member_name, started_at, hls_url, ..., status,
youtube_video_id, merge_group_id, live_slug)` — belum ada kolom `platform`.
`merge_groups(..., thumbnail_url, live_title, status, merged_file_path, merged_live_id)`.
`member_hls(username PK, display_name, hls_url, hls_confirmed, enabled, added_at, last_live_at)`.
`youtube_channels(...)`. Migrasi dilakukan idempoten di `database.init_db()` dengan
`ALTER TABLE ... ADD COLUMN` bila kolom belum ada.

### 3.4 Website saat ini

`web/lib/db.ts` menulis `'idn' as platform` (hardcoded) dan mengosongkan hasil untuk platform selain
IDN (`baseQuery += ' AND 0 = 1'`). Thumbnail selalu `img.youtube.com/vi/<id>`. Katalog hanya
menampilkan baris yang punya `youtube_video_id`. `getPublicMembers()` meng-JOIN `member_hls` lewat
`username` untuk display name.

`components/VideoPlayer.tsx` menentukan orientasi dari `platform === 'idn'` (vertikal 9:16), selain itu
16:9. Artinya **Showroom sudah otomatis diperlakukan landscape, pemutar tidak perlu diubah**.

## 4. Perubahan per file

### Bot

| File | Perubahan | Risiko |
|---|---|---|
| `bot/config.py` | Tambah `SHOWROOM_ENABLED` (default **false**), `SHOWROOM_CHECK_INTERVAL_SECONDS` (usul 30), `SHOWROOM_CONCURRENCY` (usul 10) | Rendah |
| `bot/database.py` | Migrasi kolom (§5), `insert_live(..., platform=)`, `create_merge_group(..., platform=)`, `get_all_member_hls` mengembalikan kolom Showroom, `get_members_without_hls` **mengecualikan** member Showroom-only | Sedang |
| `bot/showroom_scraper.py` | Tambah `get_live_status(room_id)` → `{is_onlive, live_id, started_at, cover, room_url_key}` dari `/room/profile`; `is_room_live()` diubah memakai `is_onlive` (hemat 1 request); tambah retry/backoff + aturan "tidak pernah melempar error" seperti IDN lookup | Rendah |
| `bot/main.py` | Path deteksi Showroom terpisah dari IDN; `live_id = f"sr_{room_url_key}_{epoch}"`; `_record_member_task(member, live_id, platform)`; `_probe_member_active`/`_fetch_member_live` jadi platform-aware | **Tinggi** |
| `bot/merger.py` | `add_segment(..., platform=)`, `create_merge_group(..., platform=)`, `_safe_probe`/`_safe_fetch_live` platform-aware (Showroom: `is_onlive`, tanpa judul) | **Tinggi** |
| `bot/downloader.py` | Diharapkan **tidak berubah** — verifikasi pemilihan format yt-dlp untuk video landscape | Rendah |
| `bot/member_manager.py` | `add_member(..., showroom_room_id=)`, `normalize_username` tidak boleh merusak angka room, tampilkan kolom Showroom di list/detail | Rendah |
| `bot/youtube_uploader.py` | `build_title`/`build_description` menerima `platform` agar judul & deskripsi menyebut Showroom | Rendah |
| `bot/seed_showroom.py` **(baru)** | Baca `showroom_rooms.json` → upsert `showroom_room_id` ke `member_hls`; buat baris untuk 7 member Showroom-only | Sedang |
| `bot/admin_bot.py`, `bot/status.py` | Opsional: tampilkan cakupan Showroom | Rendah |

### Tes

| File | Perubahan |
|---|---|
| `tests/test_showroom_scraper.py` | Perluas: parsing `is_onlive`, `live_id`, `current_live_started_at`, cover, saat offline |
| `tests/test_showroom_detection.py` **(baru)** | `live_id` ber-namespace `sr_`, tidak ada perekaman ganda saat deteksi diulang |
| `tests/test_merge_reconnect.py` | Tambah kasus platform Showroom (finalize vs defer) |
| `tests/test_main_integration.py` | Tambah dispatch platform IDN vs Showroom |
| `tests/test_member_manager.py` | Tambah `showroom_room_id` di add/list |

### Website (hanya bila D1 = youtube)

| File | Perubahan |
|---|---|
| `web/lib/db.ts` | Pakai `ls.platform` (hapus `'idn' as platform` dan guard `AND 0 = 1`); thumbnail fallback ke `merge_groups.thumbnail_url` untuk Showroom |
| Tidak berubah | `VideoCard` sudah menangani label `'showroom'`; `VideoPlayer` sudah 16:9 |

## 5. Migrasi database

Idempoten, ditambahkan di `database.init_db()` mengikuti pola yang sudah ada:

```sql
ALTER TABLE live_sessions ADD COLUMN platform TEXT NOT NULL DEFAULT 'idn';
ALTER TABLE merge_groups  ADD COLUMN platform TEXT NOT NULL DEFAULT 'idn';
ALTER TABLE member_hls    ADD COLUMN showroom_room_id TEXT;
ALTER TABLE member_hls    ADD COLUMN showroom_name    TEXT;
ALTER TABLE member_hls    ADD COLUMN showroom_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE member_hls    ADD COLUMN showroom_only    INTEGER NOT NULL DEFAULT 0;
```

`showroom_only` = 1 menandai member yang hanya ada di Showroom, supaya barisnya dilewati IDN GraphQL
discovery (kalau tidak, bot akan mencoba selamanya untuk username yang tidak ada di IDN).

Catatan:

- Baris lama otomatis menjadi `platform='idn'` — sesuai kenyataan, jadi **tidak ada data yang salah tafsir**.
- Semua penambahan kolom bersifat aman di SQLite. SQLite versi lama tidak mendukung `DROP COLUMN`,
  jadi **rollback = pulihkan file database dari backup**, bukan migrasi balik.
- Urutan wajib: backup DB → hentikan bot → jalankan
  `python3 -c "from bot.database import init_db; init_db()"` (atau lewat `member_cli` yang memanggil
  `_ensure_schema`) → verifikasi `PRAGMA table_info` → start bot.
- Disarankan bersamaan: aktifkan WAL + `busy_timeout`, karena penulis DB bertambah (bot + web) dan
  `journal_mode` saat ini masih `delete`.

## 6. Skema identitas (kunci desain)

- **Satu baris `member_hls` per orang**, menampung IDN (`hls_url`) dan Showroom (`showroom_room_id`).
  Dengan begitu `enabled`, `display_name`, CLI member, dan halaman member di web tetap satu sumber.
- **`live_id` Showroom**: `sr_<room_url_key>_<epoch>`. Kolom `live_id` UNIQUE, jadi prefix mencegah
  tabrakan dengan format IDN `<username>_<epoch>`.
- **Reconnect vs live baru**: karena `live_id` Showroom bernilai `0` selama offline, penanda sesi tidak
  bisa dibandingkan di jeda. Strategi default (aman, meniru perilaku IDN sekarang):
  **kembali live dalam `MERGE_WINDOW_SECONDS` → perlakukan sebagai reconnect**.
  Catatan jujur: konsekuensinya dua live Showroom berbeda dalam < 1 jam akan tergabung jadi satu video.
  Perlu spike dengan reconnect Showroom nyata sebelum menaikkan agresivitas finalisasi.
- **Thumbnail Showroom**: simpan cover (`image`) ke `merge_groups.thumbnail_url` yang sudah ada.
  Ini mengisi celah thumbnail web yang saat ini hanya dari YouTube.
- **Member Showroom-only** (7 nama, §8): baris `member_hls` tanpa `hls_url`, dengan `showroom_room_id`.
  Wajib dikecualikan dari `get_members_without_hls()` agar discovery IDN tidak mencoba selamanya.

## 6.1 D3 — konsekuensi dari "pantau semua room member"

Cakupan **setelah kurasi 18 Sep**: 58 room Showroom + 58 username IDN = **58 orang**, semuanya
berpasangan dua platform (tidak ada lagi member IDN tanpa room, maupun sebaliknya). Angka nyata yang
perlu diantisipasi:

| Aspek | Angka | Catatan |
|---|---|---|
| Interval polling saat ini | `HLS_CHECK_INTERVAL_SECONDS=5` | Bukan 15 seperti default README |
| Bila Showroom ikut interval 5 s | 58/5 = **11,6 request/detik** ≈ 1.002.000/hari | Risiko rate-limit / blokir IP |
| Bila interval terpisah 30 s | 1,9 req/detik ≈ 167.000/hari | Latensi deteksi 30 s, masih jauh di bawah durasi live |
| Bila interval terpisah 60 s | 1,0 req/detik ≈ 84.000/hari | Paling hemat |
| Laju penyimpanan terukur (IDN) | median **626 MB/jam**, p90 1.389 MB/jam, n=293 | Showroom landscape **belum diukur** |
| Estimasi 3 live bersamaan × 2 jam | ≈ 3,8 GB (IDN) s.d. ~9 GB (bila Showroom 1,5 GB/jam) | Perlu pemantauan disk |

Konsekuensi teknis wajib:

- **Aturan tetap perlu meski saat ini tidak ada member Showroom-only**: baris `member_hls` tanpa
  `hls_url` (hanya Showroom) **harus dikecualikan** dari `get_members_without_hls()`. Kalau tidak,
  discovery IDN akan mencoba terus tiap 2 menit untuk orang yang memang tidak ada di IDN. Ini akan
  relevan lagi begitu Anda menambahkan member baru yang belum punya akun IDN.
- Room official `332503` tetap tidak dipantau. Bila nanti ingin dipantau, itu keputusan terpisah.
- Room member baru di masa depan: cukup jalankan ulang `seed_showroom.py`.

## 6.2 D4 — kapan satu live dinyatakan selesai

Saat stream berhenti, bot harus memutuskan: **"live sudah berakhir"** atau **"sedang reconnect"**.
Keputusan ini menentukan apakah potongan-potongan digabung menjadi satu video atau menjadi beberapa
video, dan menentukan kapan upload terjadi.

Perilaku IDN sekarang: tunggu `MERGE_WINDOW_SECONDS` (1 jam) sejak segmen terakhir, **plus** pemicu
finalisasi lain (HLS offline, IDN melaporkan tidak live, hard cap).

Dua jenis kesalahan yang mungkin:

- **A — satu live terpecah** menjadi beberapa video (finalisasi terlalu cepat).
- **B — dua live berbeda tergabung** menjadi satu video (finalisasi terlalu lambat).

Bukti dari database produksi Anda:

| Metrik | Hasil |
|---|---|
| Total `merge_group` | 261 |
| `merge_group` dengan >1 segmen | 35 (13%) |
| Jeda reconnect **dalam grup yang sama** | 55 sampel: median 0 m, p75 1 m, p90 2 m, **maks 6 m**, tidak ada > 15 m |
| Jeda antar `merge_group` berurutan per member | 176 pasangan: **95% > 60 m**, **8 pasangan (5%) < 17 m** |
| 8 jeda pendek (dugaan kuat live terpecah) | delynn 5 s · intan 18 s · cynthia 22 s · rilly 22 s · daisy 43 s · jessi 3,4 m · michie 7,9 m · levi 16,5 m |

Kesimpulan dari data:

- Kesalahan **A nyata teramati** (8 dari 176 transisi, ±5%), dan penyebabnya **bukan panjang window**
  melainkan pemicu finalisasi lain yang menyala terlalu cepat — jeda 5–43 detik saja sudah cukup menutup
  grup.
- Kesalahan **B tidak teramati** dalam data (95% transisi antar grup berjarak lebih dari 1 jam).
- Jeda reconnect terukur dalam grup sangat pendek (maks 6 menit pada 55 sampel), **tetapi angka ini
  berasal dari subset riwayat** (lihat koreksi di bawah) sehingga **tidak boleh** dipakai untuk
  mempersingkat window.

### Koreksi penting atas analisis awal (18 Sep, setelah pemeriksaan lanjutan)

Pemeriksaan lanjutan membuktikan **analisis gap saya sebelumnya tidak dapat dipakai** untuk menyanggah
klaim reconnect panjang:

- **0 baris** di database memiliki `live_slug` mengandung `260913` → slug sesi yang disebut komentar
  `config.py` (`haii-260913192155` dst.) memang **tidak pernah tersimpan**.
- Pada 13 Sep, baris paling awal adalah **20:53:19**, dan `jkt48_daisy`, `jkt48_aralie`, `jkt48_levi`
  semuanya mulai pada **detik yang sama persis** — indikasi kuat bot di-restart saat itu. Sesi 19:21 dan
  20:12 yang dirujuk komentar **tidak ada** di database.
- Karena itu, angka "jeda dalam grup maks 6 menit" diukur pada **subset riwayat**, bukan bukti bahwa
  jeda reconnect panjang tidak pernah terjadi.

**Kesimpulan yang berlaku:** klaim reconnect sampai ±50 menit dari pemilik proyek + komentar di
`config.py` tetap menjadi dasar yang sah, dan **window 1 jam dipertahankan**.

### Keputusan D4

Sesuai arahan pemilik proyek: **Showroom memakai aturan finalisasi yang sama dengan IDN** —
`MERGE_WINDOW_SECONDS` (1 jam) sejak segmen terakhir. Tambahan yang diusulkan hanya **debounce pada
sinyal offline** (beberapa pembacaan berturut-turut), yang sifatnya **memperlambat**, bukan mempercepat
finalisasi, sehingga tidak menambah risiko video terpecah.

Sinyal offline Showroom TIDAK boleh langsung memfinalisasi karena API bisa gagal sesaat — pada IDN
sinyalnya adalah HTTP check ke URL HLS permanen yang jauh lebih stabil daripada satu panggilan API.

Komplikasi khusus Showroom:

- `live_id` bernilai `0` saat offline → identitas sesi **tidak bisa dibandingkan** selama jeda.
- URL HLS Showroom bersifat **per sesi** (diambil dari API), bukan URL permanen seperti IDN, sehingga
  status "offline" disimpulkan dari API yang bisa gagal sesaat.

Opsi untuk D4:

| Opsi | Perilaku | Konsekuensi |
|---|---|---|
| **A** | Samakan dengan IDN: window 1 jam + sinyal offline | Konsisten & sederhana; mewarisi ~5% risiko terpecah |
| **B** (rekomendasi) | A + debounce: butuh N pembacaan offline berturut-turut (mis. 3 × interval 30 s = 90 s) sebelum finalisasi | Menekan split akibat API hiccup; menambah ~1,5 menit latensi |
| **C** | Hanya window penuh, abaikan sinyal offline | Split paling sedikit; video muncul hingga 1 jam lebih lambat, dan dua live berbeda < 1 jam akan tergabung |

Keputusan yang saya butuhkan: pilih **A / B / C untuk Showroom**, dan apakah debounce (B) juga
diterapkan ke jalur IDN. Catatan: opsi C menumpuk finalisasi di akhir window, sehingga upload bisa
menumpuk bersamaan — relevan dengan kuota harian YouTube dan batas 2 GB Telegram.

## 6.3 Kesiapan pemutar — hasil ujicoba 18 Sep 2026

Halaman ujicoba **`/admin/trial`** (di dalam area admin, terlindungi login) sudah dibuat untuk
membuktikan pemutar mana yang sebenarnya dibutuhkan Showroom.

### Temuan: pemutar SUDAH punya cabang landscape

`components/VideoPlayer.tsx` memilih orientasi dari `platform`:

```ts
const isVertical = platform === 'idn' || platform === 'idn_live';
```

Artinya `platform='showroom'` otomatis memakai jalur landscape:

| | IDN | Showroom |
|---|---|---|
| Kelas wrapper | `vertical-player` | `horizontal-player` |
| Kelas container | `vertical-player-container ratio-<mode>` | `horizontal-player-container` |
| Rasio | 3/4 (fit) atau 9/16 | **16/9 dipaksa** (`aspect-ratio: 16 / 9 !important`) |
| Tombol FIT/FILL/9:16 | ada | **tidak ada** (memang khusus vertikal) |
| Tombol Theater | ada | ada |
| **Fullscreen** | theater satu halaman | **fullscreen elemen 16:9** |
| `--theater-ratio` | 0.75 atau 0.5625 | 0.5625 → 16/9 |

**Jadi bagian yang benar-benar khusus IDN hanyalah tombol rasio vertikal**, bukan seluruh pemutar.
Dibuktikan lewat uji browser: landscape terukur `16 / 9`, vertikal `3 / 4`, keduanya render pemutar.

### Satu-satunya celah nyata: jenis sumber

`<MediaPlayer src=...>` saat ini **hanya** menerima sumber YouTube
(`https://www.youtube.com/watch?v=<id>`). Jadi:

- Bila Showroom diunggah ke YouTube (target upload global), **pemutar tidak perlu diubah sama sekali**.
- Untuk memutar rekaman mentah **sebelum** diunggah (.mp4/.m3u8), pemutar butuh sumber langsung.
  Prop opsional `directSrc` sudah ditambahkan untuk ini (default kosong → perilaku IDN tidak berubah).
- **HLS `.m3u8` belum bisa diputar** karena `hls.js` **tidak terpasang** di proyek ini
  (Vidstack memerlukan `hls.js` sebagai dependensi untuk HLS di browser). Ini keputusan terpisah:
  menambah dependensi, atau selalu lewat YouTube.

### Fullscreen: perilaku BERBEDA per orientasi (19 Sep 2026)

IDN dan Showroom kini memakai mekanisme layar penuh yang berbeda, karena rasio
keduanya memang berbeda:

| | IDN (vertikal) | Showroom (landscape) |
|---|---|---|
| Mekanisme | **Theater satu halaman** (`.theater-mode`) | **Fullscreen elemen** (`requestFullscreen()`) |
| Rasio layar penuh | `--theater-ratio` (3/4 atau 9/16) | **16/9 dipaksa**, dipusatkan |
| Scroll halaman | dikunci (`body overflow: hidden`) | **tidak dikunci** (browser mengurus) |
| Latar belakang | diset `inert` + fokus dikurung | tidak perlu (elemen mengisi viewport) |
| Peran ARIA | `role="dialog" aria-modal="true"` | tanpa dialog (bukan overlay halaman) |
| Tombol keluar | tombol sendiri + Esc | tombol *keluar fullscreen* bawaan browser + Esc |
| Ikon tombol | ikon theater | ikon sudut fullscreen |

Alasan: video 16:9 mengisi layar secara natural, sehingga fullscreen elemen native
lebih tepat. Sedangkan video 9:16 butuh tata letak khusus agar tidak menyisakan
ruang kosong besar; itu alasan "theater" satu halaman tetap dipakai untuk IDN.

Implementasi ada di `components/VideoPlayer.tsx`:

```ts
const isElementFullscreen = !isVertical;   // Showroom = true
```

State tombol disinkronkan lewat event `fullscreenchange`, jadi keluar dengan Esc
atau tombol bawaan browser tetap membuat ikon kembali ke keadaan semula, dan fokus
dikembalikan ke tombol fullscreen.

### Perbaikan bilah progres (19 Sep 2026)

Ditemukan bahwa bilah progres pemutar **membengkak menjadi kotak 44px** saat pemutar
ditanam di halaman admin. Penyebabnya bukan orientasi, melainkan **kebocoran CSS
dari stylesheet formulir admin**:

```css
/* app/portal.css, sebelum diperbaiki */
.admin-shell input { min-height: 44px; border: 1px solid #374151; border-radius: 8px; padding: 10px 12px; }
```

Selektor `.admin-shell input` mengenai **semua** `<input>`, termasuk slider pemutar.
Pengukuran di browser membuktikan perbedaannya:

| | `/admin/trial` (sebelum) | `/watch/<id>` publik | `/admin/trial` (sesudah) |
|---|---|---|---|
| Tinggi slider | **44 px** | 4 px | **4 px** |
| `min-height` | 44 px | auto | 0 px |
| `border` | 0,67 px solid | 0 | 0 |
| `padding` | 10 px 12 px | 0 | 0 |

Diperbaiki di dua lapis:

1. `app/portal.css` — selektor dibatasi dengan `:not([type="range"])` agar gaya
   formulir tidak mengenai slider.
2. `app/globals.css` — pemutar **mereset sendiri** `min-height`, `padding`, `border`
   dan `background` slider-nya. Spesifisitas (0,2,0) sengaja lebih tinggi dari
   `.admin-shell input` (0,1,1), sehingga pemutar tampil sama di konteks mana pun.

`verify/player-trial-check.mjs` kini mengukur geometri slider dan akan **gagal**
bila kebocoran ini muncul kembali.

### Kesimpulan untuk Fase 2

Pemutar **tidak perlu pemutar baru**. Yang perlu dikerjakan hanya:
1. `web/lib/db.ts` agar memakai `ls.platform` (masih hardcode `'idn'`) — bagian dari Fase 4.
2. Keputusan `hls.js` bila ingin pratinjau rekaman mentah sebelum upload.

## 7. Risiko dan mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| 58 room x request tiap 5 detik ≈ 11,6 req/detik ke API Showroom | Rate-limit / blokir IP | Interval terpisah (30–60 s), concurrency 10, backoff, feature flag |
| Salah mendeteksi live baru vs reconnect | Satu live terpecah / dua live tergabung | Default tunggu window penuh; spike dengan reconnect nyata (§6) |
| `get_members_without_hls` menganggap member Showroom-only belum punya HLS | Loop discovery IDN sia-sia | Kecualikan baris Showroom-only |
| Live Showroom bersamaan jauh lebih banyak daripada IDN | Disk penuh, antrean Telegram menumpuk, CPU/bandwidth naik | Batasi concurrency, pantau disk, `AUTO_DELETE_AFTER_UPLOAD` sudah `true` |
| Video Showroom landscape & bitrate lebih besar | File per jam lebih besar, upload Telegram lebih banyak part | Ukur 1 live nyata sebelum mengaktifkan semua 58 room |
| Room ID berubah / room ditutup | Pemantauan diam-diam berhenti | Deteksi lewat kegagalan HTTP/API berulang → notifikasi admin Telegram. **Jangan pakai `is_displayed`** (10 room baru gen 16 bernilai `false` padahal valid) |
| Migrasi DB pada data produksi (38 video, 181 sesi Telegram) | Kehilangan data | Backup dulu; hanya ADD COLUMN; uji idempoten dua kali |
| `journal_mode=delete` dengan penulis bertambah | `database is locked` | Aktifkan WAL + `busy_timeout` |
| **Artefak data:** `file_size_bytes` per segmen ditimpa ukuran file gabungan saat upload (`main.py:228` → `update_sessions_by_merge_group` → `update_status` untuk semua `live_id` dalam grup) | Statistik ukuran per segmen tidak dapat dipercaya untuk grup berstatus `done_*`; analisis berbasis ukuran bisa menyesatkan | Jangan pakai `file_size_bytes` per segmen untuk grup yang sudah diupload. `download_ended_at` tidak terpengaruh. Bila butuh data bersih: pakai grup gagal/belum selesai, atau perbaiki agar segmen tidak ditimpa |
| Perubahan `main.py`/`merger.py` mengganggu rekaman IDN yang sedang jalan | Rekaman IDN rusak | Feature flag default OFF + jalur kode terpisah + tes regresi IDN |

## 8. Status kurasi `showroom_rooms.json`

Diperbarui 18 Sep 2026 sesuai arahan pemilik proyek:

- **58 room member + 1 room official** (332503, dipisahkan agar tidak pernah dipantau).
- **58/58 room berpasangan** dengan username IDN di `members.txt` (total **58 member IDN**).
- `jkt48_anindya` **ditambahkan** ke `members.txt` (57 → 58).
- **6 room dihapus** karena sudah tidak di JKT48: Amanda (400710), Alya (461451), Cathy (461454),
  Gendis (461476), Chelsea (461458), Auwia (547062) — dicatat di `excluded_rooms` + `curation_log`.
- **10 room baru ditambahkan** (member generasi 16, `Instagram: jkt48.u16`), diverifikasi lewat API:
  Carissa 572572 · Bella 572573 · Fahira 572574 · Rara 572575 · Maxine 572576 · Jazzy 572577 ·
  Sona 572579 · Ralyne 572587 · Fera 572589 · Heidi 572591.

### Temuan penting yang mengoreksi asumsi awal

10 username IDN yang sebelumnya saya tandai "belum punya room Showroom" **ternyata bukan member yang
sudah lulus** — mereka akhirnya cocok persis dengan 10 room baru di atas. Jadi roster kini konsisten
penuh: **0 member tanpa pasangan** di kedua arah.

### Temuan penting: `is_displayed` BUKAN penanda room valid

Kesepuluh room baru memiliki **`is_displayed: false`** padahal room-nya valid dan `is_official: true`.
Karena itu, rencana "deteksi room ditutup via `is_displayed`" di §7 **dibatalkan** — memakainya akan
membuat 10 member baru ini dianggap tidak valid. Deteksi cukup lewat kegagalan HTTP/API berulang.

### Field tambahan yang kini tersimpan untuk 10 room baru

`room_url_key` (mis. `JKT48_Sona`, bahan namespace `live_id`), `member_full_name` dari `description`
(mis. "Sona Kalyana", berguna untuk `display_name`), `profile_image`, dan `source` (penanda terverifikasi).
Untuk 48 room lama, field ini belum diambil — `seed_showroom.py` sebaiknya mengambilnya dari
`/api/room/profile` saat seeding agar semua 58 room seragam.

### Artefak

- Backup: `tmp/members.txt.bak`, `tmp/showroom_rooms.json.bak`, `tmp/showroom_rooms.json.bak2`
  (folder `tmp/` diabaikan git).
- 2 `review_issues` tersisa = tabrakan nama Jepang `エリン` dan `リリー`, **tidak berbahaya** karena
  username IDN-nya berbeda.

## 9. Fase pengerjaan

- [x] **Fase 0 — kurasi & keputusan** (tanpa kode): D1, D1b, D2, D3, D4 selesai. Roster lengkap
      **58 room = 58 member IDN**, 0 tanpa pasangan; 6 room lulus dihapus; 10 room gen 16 ditambahkan &
      diverifikasi API. Finalisasi Showroom memakai window 1 jam seperti IDN.
- [x] **Fase 1 — database & flag** ✅ SELESAI 18 Sep 2026.
      Yang diimplementasikan:
      - `bot/database.py`: 6 migrasi kolom idempoten; `insert_live(..., platform=)` dan
        `create_merge_group(..., platform=)` (default `'idn'`, pemanggil lama tidak berubah);
        fungsi baru `set_member_showroom()`; `get_members_without_hls()` melewati `showroom_only = 1`.
      - `bot/config.py`: 6 variabel Showroom, semuanya default aman (`SHOWROOM_ENABLED=false`).
      - `bot/seed_showroom.py` (baru): CLI idempoten + `--dry-run`, menolak data tidak valid.
      - `tests/test_showroom_seed.py` (baru, 19 tes) + `README.md` diperbarui.
      Kriteria terpenuhi: **114 tes lulus**, dan dengan flag mati perilaku bot tidak berubah
      (semua jalur Showroom belum tersambung ke `main.py`).
- [x] **Fase 2 — deteksi & rekam** ✅ SELESAI 18 Sep 2026 (belum diuji live nyata).
      Yang diimplementasikan:
      - `bot/showroom_monitor.py` (baru): `ShowroomMonitor` + `RoomState`;
        probe `is_onlive` lewat `/api/room/profile`; **debounce offline** —
        `is_room_live()` hanya mengembalikan `False` setelah
        `SHOWROOM_OFFLINE_CONFIRMATIONS` pembacaan offline berturut-turut,
        dan `None` selama belum terkonfirmasi sehingga satu hiccup API tidak
        memecah live; kegagalan pembacaan tidak pernah dihitung sebagai offline;
        `build_live_id()` → `sr_<room_url_key>_<epoch>`; `find_live_rooms()`
        memindai 58 room dengan `SHOWROOM_CONCURRENCY` dan hanya meminta URL HLS
        untuk room yang benar-benar live.
      - `bot/main.py`: `_record_showroom_task()` (platform='showroom', cover room
        sebagai thumbnail, nama room sebagai `live_title`, tanpa slug IDN);
        `_check_showroom()` pada interval terpisah dengan `try/except` sendiri;
        `_probe_member_active(username, platform)` memilih HLS vs API Showroom;
        `_fetch_member_live` **tidak memanggil IDN** untuk Showroom;
        `active_showroom` terpisah dari `active_recordings`;
        `_cancel_active_recording` memeriksa kedua kunci (`u` dan `sr:u`).
      - `bot/merger.py`: `platform` diteruskan melalui `add_segment` →
        `_finalize_group` → `_decide` → `_safe_probe`/`_safe_fetch_live`;
        kunci internal `_scope(member, platform)` memisahkan timer dan status
        "sedang merekam" sehingga IDN & Showroom orang yang sama tidak saling
        memblokir; `recover_stuck_groups` mengembalikan platform dari grup.
      - `bot/database.py`: `get_active_merge_group(..., platform=)` menyaring
        grup per platform; `get_members_with_showroom()`,
        `get_member_showroom_room_id()`, `set_member_showroom_enabled()`.
      - `tests/test_showroom_monitor.py` (baru, 19 tes) dan
        `tests/test_showroom_integration.py` (baru, 16 tes).
      Kriteria: **171 tes lulus** (dari 136). ️ Kriteria "1 room uji terekam
      utuh" **BELUM terpenuhi** — saat dikerjakan 58/58 room offline, jadi jalur
      `yt-dlp` → Showroom HLS belum diuji terhadap stream sungguhan.
- [x] **Fase 3 — merge & notifikasi** ✅ SELESAI untuk bagian *merge* (finalisasi
      platform-aware), digabung ke Fase 2 karena tidak terpisahkan.
      Sisa yang belum: notifikasi Telegram menyebut platform, dan CLI/`status`
      menampilkan cakupan Showroom.
- [x] **Fase 4 — platform di web** ✅ SELESAI 18 Sep 2026 (bagian web).
      `web/lib/db.ts` kini memakai `ls.platform` (bukan `'idn'` hardcoded) pada
      katalog dan halaman watch, plus `normalizePlatform()`; filter
      `platform=showroom` bekerja (sebelumnya selalu kosong). Karena
      `VideoPlayer` memilih orientasi dari `platform`, rekaman Showroom otomatis
      memakai pemutar landscape + fullscreen elemen.
      ⚠️ Rekaman Showroom hanya bisa **muncul** di website bila
      `UPLOAD_TARGET=youtube`; katalog web dibangun dari `youtube_video_id`.

## 10. Verifikasi

Perintah uji yang sudah ada di repo:

```bash
cd c:\Sendy\jkt48-live
python -m pytest tests -q
python -m pytest tests/test_showroom_scraper.py -q
python3 -m bot.status
```

Sudah dibuat & dijalankan (Fase 1–2):

- `tests/test_showroom_monitor.py` — 19 tes: semantik debounce (1..N-1 → `None`,
  ke-N → `False`), reset saat pembacaan live, kegagalan pembacaan tidak pernah
  jadi bukti offline, pemetaan field API, identitas `live_id`, dan
  `find_live_rooms` (hanya room live, HLS tidak diminta untuk room offline,
  satu exception tidak menggagalkan siklus).
- `tests/test_showroom_integration.py` — 16 tes: probe per platform
  (IDN↔HLS, Showroom↔API, tidak tertukar), Showroom tidak memanggil IDN,
  siklus `_check_showroom`, `_record_showroom_task` menyimpan
  `platform='showroom'` + `live_id` berprefix, pemisahan grup IDN vs Showroom,
  dan `_decide` Showroom tidak memanggil IDN lookup.
- `tests/test_showroom_seed.py` — 19 tes migrasi & seed (Fase 1).
- Regresi IDN: **171 tes lulus** dengan `SHOWROOM_ENABLED=false`.

Belum dijalankan (butuh live nyata — lihat §9):

- Uji 1 room Showroom nyata: cek `live_id`, nama file, status DB, hasil upload,
  dan apakah `yt-dlp` sanggup mengambil HLS Showroom.
- Uji beban ringan: probe 58 room interval 30 s, ukur waktu siklus & jumlah request.
- Uji finalisasi sesi yang benar-benar reconnect.

Website: `npx tsc --noEmit`, `npx eslint .`, `npm run build`, lalu kelima skrip di
`web/verify/` — `public-private-check.mjs` kini juga memverifikasi filter platform
(`?platform=showroom` menampilkan rekaman Showroom dan mengecualikan yang IDN,
dan sebaliknya).

## 11. Yang sengaja TIDAK disentuh

- `bot/video_splitter.py`, `bot/telegram_sender.py` — sudah platform-agnostik.
- `bot/idn_lookup.py`, `bot/idn_scraper.py` — jalur IDN tetap seperti sekarang.
- Gerbang publikasi web (`web_publications`) dan autentikasi admin — tidak terkait Showroom.
- `components/VideoPlayer.tsx` — sudah menangani video landscape, dan orientasi
  dipilih dari nilai `platform` yang kini datang dari database (bukan hardcoded).