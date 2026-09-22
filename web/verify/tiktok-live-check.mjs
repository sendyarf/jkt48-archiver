/**
 * tiktok-live-check.mjs — diagnostik halaman arsip TikTok di server yang SEDANG
 * berjalan (bukan uji fungsional; uji itu ada di `verify/tiktok-page.mjs`).
 *
 * Bedanya dengan `verify:tiktok`:
 *   - `verify:tiktok` membuat database fixture sendiri lalu menyalakan server
 *     sementara, jadi ia menguji KODE.
 *   - skrip ini memeriksa DATA + KONFIGURASI server nyata, karena halaman
 *     /tiktok bisa "kosong" walau kodenya benar (mis. web dan bot menunjuk
 *     database berbeda, atau bot belum pernah mengarsipkan apa pun).
 *
 * Pakai di VPS:
 *   cd web && node verify/tiktok-live-check.mjs
 *   node verify/tiktok-live-check.mjs http://localhost:3000
 *
 * Skrip ini TIDAK menulis apa pun ke database dan tidak menyentuh proses lain.
 */
import { existsSync, readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { join, resolve } from 'node:path';

const origin = (process.argv[2] || 'http://localhost:3000').replace(/\/$/, '');
const root = resolve(import.meta.dirname, '..');      // folder web/
const repo = resolve(root, '..');
const problems = [];
const hints = [];

function line(label, value) {
  console.log(`${label.padEnd(34)}: ${value}`);
}

console.log('=== 1. Berkas .env web ===');
const envPath = join(root, '.env');
let envDbPath = '';
if (existsSync(envPath)) {
  const env = readFileSync(envPath, 'utf8');
  const match = env.match(/^\s*DB_PATH\s*=\s*(.+?)\s*$/m);
  envDbPath = match ? match[1].replace(/^["']|["']$/g, '') : '';
  const botUser = env.match(/^\s*NEXT_PUBLIC_REPLAY_BOT_USERNAME\s*=\s*(.*?)\s*$/m);
  line('web/.env', 'ADA');
  line('  DB_PATH', envDbPath || '(tidak diisi!)');
  line('  NEXT_PUBLIC_REPLAY_BOT_USERNAME', botUser?.[1] || '(kosong — tombol download kosong)');
} else {
  line('web/.env', 'TIDAK ADA');
  problems.push('web/.env tidak ada di folder web/, jadi DB_PATH tidak diset.');
}

// Salinan sengaja mengikuti urutan di web/lib/db.ts::getDb() — kalau berbeda,
// yang dilaporkan skrip ini tidak lagi menggambarkan yang dipakai situs.
const candidates = [
  process.env.DB_PATH,
  envDbPath,
  resolve(root, '..', 'jkt48_live.db'),
  resolve(root, 'jkt48_live.db'),
].filter(Boolean);

console.log('\n=== 2. Database mana yang benar-benar dibuka web ===');
let dbPath = candidates[0];
candidates.forEach((candidate, index) => {
  console.log(`  [${index}] ${existsSync(candidate) ? 'ADA   ' : 'TIDAK '} ${candidate}`);
});
for (const candidate of candidates) {
  if (existsSync(candidate)) {
    dbPath = candidate;
    break;
  }
}
line('dipakai web', dbPath || '(tidak ada satu pun yang ada!)');

const dbPathFromEnv = process.env.DB_PATH || envDbPath;
if (!dbPathFromEnv) {
  problems.push('DB_PATH tidak diset (web/.env atau environment PM2).');
} else if (!existsSync(dbPathFromEnv)) {
  problems.push(
    `DB_PATH menunjuk berkas yang TIDAK ada (${dbPathFromEnv}). ` +
      'Web diam-diam jatuh ke kandidat berikutnya, yang bisa jadi database berbeda.',
  );
  hints.push(
    'Perbaiki DB_PATH di web/.env (harus ABSOLUT dan sama persis dengan DB_PATH bot), ' +
      'lalu pm2 restart jkt48-web.',
  );
}

console.log('\n=== 3. Database yang dipakai BOT (repo/.env) ===');
const botEnvPath = join(repo, '.env');
let botDbPath = '';
if (existsSync(botEnvPath)) {
  const env = readFileSync(botEnvPath, 'utf8');
  const match = env.match(/^\s*DB_PATH\s*=\s*(.+?)\s*$/m);
  const raw = match ? match[1].replace(/^["']|["']$/g, '') : 'jkt48_live.db';
  // DB_PATH relatif diselesaikan dari CWD bot, dan bot dijalankan dari root repo.
  botDbPath = raw.startsWith('/') ? raw : resolve(repo, raw);
  const enabled = env.match(/^\s*TIKTOK_ENABLED\s*=\s*(.*?)\s*$/m);
  line('repo/.env', 'ADA');
  line('  DB_PATH bot', `${raw}  ->  ${botDbPath}`);
  line('  TIKTOK_ENABLED', enabled?.[1] || '(tidak diisi — default false)');
  if ((enabled?.[1] || 'false').toLowerCase() !== 'true') {
    hints.push(
      'TIKTOK_ENABLED belum true di repo/.env, jadi bot TIDAK memantau TikTok ' +
        'dan tidak akan pernah ada arsip baru.',
    );
  }
} else {
  line('repo/.env', 'TIDAK ADA');
  botDbPath = resolve(repo, 'jkt48_live.db');
  line('  DB_PATH bot (default)', botDbPath);
  problems.push('repo/.env tidak ada; bot memakai default relatif jkt48_live.db.');
}

if (botDbPath && dbPath && resolve(botDbPath) !== resolve(dbPath)) {
  problems.push('BOT dan WEB menunjuk database BERBEDA (penyebab klasik halaman kosong).');
  hints.push('Samakan DB_PATH di repo/.env dan web/.env, lalu restart bot + web.');
}

function describe(target, label) {
  console.log(`\n=== ${label} ===`);
  if (!target || !existsSync(target)) {
    line('berkas', 'TIDAK ADA');
    return null;
  }
  let db;
  try {
    db = new DatabaseSync(target, { readOnly: true });
  } catch {
    db = new DatabaseSync(target);
  }
  const tables = db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
    .all()
    .map((row) => row.name);
  const has = (name) => tables.includes(name);
  line('berkas', target);
  line('tabel tiktok_accounts', has('tiktok_accounts') ? 'ADA' : 'TIDAK ADA');
  line('tabel tiktok_posts', has('tiktok_posts') ? 'ADA' : 'TIDAK ADA');
  if (!has('tiktok_accounts') || !has('tiktok_posts')) {
    return { accounts: 0, ready: 0, photos: 0, total: 0 };
  }
  const count = (sql) => db.prepare(sql).get().n;
  const ready = count(
    "SELECT COUNT(*) n FROM tiktok_posts WHERE visible = 1 AND " +
      "(LENGTH(COALESCE(youtube_video_id,'')) > 0 " +
      " OR LENGTH(COALESCE(telegram_message_ids,'')) > 0)",
  );
  const accounts = count('SELECT COUNT(*) n FROM tiktok_accounts WHERE enabled = 1');
  const total = count('SELECT COUNT(*) n FROM tiktok_posts');
  let photos = 0;
  try {
    photos = count(
      "SELECT COUNT(*) n FROM tiktok_accounts " +
        "WHERE avatar_url IS NOT NULL AND avatar_url != ''",
    );
  } catch {
    photos = -1; // kolom avatar_url belum ada -> migrasi bot belum jalan
  }
  line('akun aktif (sidebar)', accounts);
  line('akun punya foto', photos < 0 ? 'kolom avatar_url BELUM ADA (jalankan seed)' : photos);
  line('arsip tersimpan', total);
  line('arsip SIAP TAYANG', ready);
  if (photos < 0) {
    hints.push('Jalankan python3 -m bot.seed_tiktok agar kolom avatar_url + foto terisi.');
  }
  db.close();
  return { accounts, ready, photos, total };
}

const webStats = describe(dbPath, '4. Isi database yang dibaca web');
const botStats = describe(botDbPath, '5. Isi database yang ditulis bot');

console.log('\n=== 6. Build produksi (jalur /tiktok ada?) ===');
const buildDir = join(root, '.next');
const manifest = join(buildDir, 'server', 'app', 'tiktok.html');
const routeDir = join(buildDir, 'server', 'app', 'tiktok');
line('.next ada', existsSync(buildDir) ? 'ya' : 'TIDAK (belum pernah npm run build)');
line('rute /tiktok ter-build', existsSync(manifest) || existsSync(routeDir) ? 'ya' : 'TIDAK');
if (!existsSync(buildDir) || !(existsSync(manifest) || existsSync(routeDir))) {
  problems.push('Build produksi tidak memuat rute /tiktok.');
  hints.push(
    'Jalankan npm run build di folder web, lalu pm2 restart jkt48-web ' +
      '(pm2 restart saja TIDAK memperbarui build).',
  );
}

console.log(`\n=== 7. Server yang berjalan (${origin}) ===`);
try {
  const home = await fetch(origin, { redirect: 'manual' });
  line('GET /', `HTTP ${home.status}`);
  const page = await fetch(`${origin}/tiktok`, { redirect: 'manual' });
  line('GET /tiktok', `HTTP ${page.status}`);
  if (page.status === 404) {
    problems.push('/tiktok menjawab 404 — build lama atau routing belum memuat halaman ini.');
  } else if (page.status >= 500) {
    problems.push(`/tiktok menjawab HTTP ${page.status} — ada error runtime di server.`);
  } else if (page.status === 200) {
    const html = await page.text();
    const count = (re) => (html.match(re) || []).length;
    line('  ukuran HTML', `${html.length} byte`);
    line('  akun di sidebar', count(/tiktok-account-item/g) - 1); // -1 = tombol "Semua akun"
    line('  kartu arsip', count(/tiktok-post-item/g));
    line('  foto member termuat', count(/tiktok-account-avatar/g));
    line('  fallback inisial', count(/tiktok-account-initial/g));
    const empty = html.includes('Belum ada arsip TikTok yang tayang');
    line('  pesan "belum ada arsip"', empty ? 'MUNCUL' : 'tidak');
    const summary = html.match(/(\d+) arsip dari (\d+) akun — (\d+) bisa diputar · (\d+) unduh via bot/)
      || html.match(/(\d+) arsip siap ditonton dari (\d+) akun/)
      || html.match(/(\d+) akun dipantau/);
    line('  ringkasan header', summary ? summary[0] : '(tidak terbaca)');
    if (!html.includes('tiktok-shell')) {
      problems.push('/tiktok 200 tetapi tata letak arsipnya tidak dirender (tidak ada .tiktok-shell).');
    } else if (empty) {
      hints.push(
        'Halaman SUDAH benar dan akunnya tampil; yang kosong hanya daftar ARSIP karena ' +
          'belum ada postingan yang siap tayang. Jalankan bot: ' +
          'TIKTOK_ENABLED=true python3 -m bot.tiktok_monitor --once',
      );
    }
  }
} catch (error) {
  problems.push(`Tidak bisa menghubungi ${origin} (${error.message}). Cek port PM2 / nginx.`);
  hints.push('Coba jalankan dengan port yang benar, mis. node verify/tiktok-live-check.mjs http://localhost:3000');
}

console.log('\n=== KESIMPULAN ===');
// Perbandingan paling menentukan: bila bot sudah punya arsip siap tayang tetapi
// DB yang dibaca web belum, berarti web memakai database yang BASI/berbeda
// walau berkasnya sama-sama ada.
const webReady = webStats?.ready ?? 0;
const botReady = botStats?.ready ?? 0;
if (botReady > 0 && webReady < botReady) {
  problems.push(
    `DB web memuat ${webReady} arsip siap tayang, sedangkan DB bot ${botReady}. ` +
      'Web memakai database yang berbeda atau masih basi.',
  );
}

if (!problems.length) {
  console.log('Tidak ada masalah konfigurasi. Bila daftar arsip masih kosong, itu soal DATA:');
  console.log(`arsip siap tayang di DB bot = ${botReady}.`);
  if (botReady === 0) {
    console.log('Jalankan satu siklus bot lalu muat ulang halaman:');
    console.log('  cd .. && TIKTOK_ENABLED=true python3 -m bot.tiktok_monitor --once');
  } else {
    console.log(`Sitanya halaman seharusnya menampilkan ${botReady} arsip. Bila tidak,`);
    console.log('muat ulang dengan Ctrl+Shift+R (halaman di-cache browser).');
  }
} else {
  console.log('MASALAH yang ditemukan:');
  problems.forEach((item, i) => console.log(`  ${i + 1}. ${item}`));
}
if (hints.length) {
  console.log('\nLangkah perbaikan:');
  hints.forEach((item) => console.log(`  -> ${item}`));
}

// Kode keluar eksplisit supaya skrip ini bisa dipakai di pemeriksaan otomatis:
// 0 = konfigurasi sehat, 1 = ada masalah yang harus diperbaiki.
process.exitCode = problems.length ? 1 : 0;