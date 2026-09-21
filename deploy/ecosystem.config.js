// Konfigurasi PM2 untuk jkt48-live: website (Next.js) + bot (Python).
//
// Pakai:
//   pm2 start deploy/ecosystem.config.js
//   pm2 save
//   pm2 logs
//
// Path dihitung dari lokasi berkas ini, jadi tidak perlu diubah manual selama
// struktur repo tidak berubah.

const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');
const webRoot = path.join(repoRoot, 'web');

// Python untuk bot: venv repo bila ada, kalau tidak pakai python3 sistem.
// path.join dipakai agar tetap valid di Windows saat file ini dicek sintaksnya.
const fs = require('node:fs');
//
// Urutan pencarian interpreter bot:
//   1. BOT_PYTHON (env)          -- jalan keluar tanpa menyunting berkas ini,
//                                   dipakai bila venv diletakkan di luar repo.
//   2. .venv/bin/python          -- nama yang dipakai README instalasi.
//   3. venv/bin/python           -- nama lama di README. Tetap didukung supaya
//                                   setup yang sudah jalan tidak mendadak pindah
//                                   ke python3 sistem yang dependency-nya belum
//                                   tentu lengkap (bot langsung mati dengan
//                                   ModuleNotFoundError).
//   4. Scripts/python.exe        -- venv Windows (dipakai saat uji lokal).
//   5. python3                   -- sistem.
//
// Karena pip sistem Debian/Ubuntu 24.04 dikunci PEP 668
// ("externally-managed-environment"), dependensi bot memang seharusnya berada di
// venv. Cara memastikan interpreter yang dipakai: `pm2 describe` lalu lihat
// "script path"/"exec cwd".
const venvCandidates = [
  path.join(repoRoot, '.venv', 'bin', 'python'),
  path.join(repoRoot, 'venv', 'bin', 'python'),
  path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
  path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
];
const venvPython = venvCandidates.find((candidate) => fs.existsSync(candidate));
const botPython = process.env.BOT_PYTHON || venvPython || 'python3';

module.exports = {
  apps: [
    {
      name: 'jkt48-web',
      cwd: webRoot,
      script: 'node_modules/next/dist/bin/next',
      // -H 127.0.0.1: hanya loopback, sehingga port 3101 tidak terekspos ke internet.
      // Semua trafik publik harus lewat nginx.
      args: 'start -p 3101 -H 127.0.0.1',
      interpreter: 'node',
      env: { NODE_ENV: 'production' },
      // Mode fork 1 instance, bukan cluster: database SQLite hanya menerima satu
      // penulis dan pembatasan percobaan login disimpan di memori proses.
      exec_mode: 'fork',
      instances: 1,
      autorestart: true,
      max_memory_restart: '700M',
      kill_timeout: 5000,
      time: true,
      merge_logs: true,
      out_file: path.join(repoRoot, 'logs', 'web-out.log'),
      error_file: path.join(repoRoot, 'logs', 'web-error.log'),
    },
    {
      name: 'jkt48-archiver-bot',
      cwd: repoRoot,
      // Pakai python dari venv repo bila ada; jatuh ke python3 sistem bila tidak.
      // Tanpa ini PM2 memakai python3 sistem yang tidak punya dependency bot.
      // Nilainya dihitung di atas (lihat `botPython`); set `BOT_PYTHON` untuk
      // memaksa interpreter tertentu.
      script: botPython,
      args: '-m bot.main',
      interpreter: 'none',
      autorestart: true,
      // PENTING: bot menunggu segmen aktif tersimpan sebelum keluar
      // (GRACEFUL_SHUTDOWN_SECONDS, default 25 detik). Default kill_timeout PM2
      // hanya 1600 ms, sehingga tanpa nilai di bawah ini PM2 akan SIGKILL lebih
      // dulu dan segmen parsial hilang -- justru merusak jaminan di README.
      kill_timeout: 35000,
      time: true,
      merge_logs: true,
      out_file: path.join(repoRoot, 'logs', 'bot-out.log'),
      error_file: path.join(repoRoot, 'logs', 'bot-error.log'),
    },
  ],
};
