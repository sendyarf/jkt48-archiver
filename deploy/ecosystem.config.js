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
      script: 'python3',
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
