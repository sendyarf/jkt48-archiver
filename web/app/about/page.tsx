import type { Metadata } from 'next';
export const metadata: Metadata = { title: 'Tentang & Privasi' };
export default function AboutPage() {
  return <article className="container page-section prose"><p className="eyebrow">TENTANG SITUS</p><h1>Arsip untuk komunitas</h1><p>JKT48 Replay adalah arsip komunitas untuk menonton ulang live JKT48. Situs ini tidak berafiliasi dengan JKT48, IDN, Showroom, atau YouTube. Hak atas konten tetap dimiliki pemiliknya.</p>
    <section className="panel"><h2>Apa yang tersedia di sini?</h2><p>Halaman publik menampilkan replay yang sudah disetujui pengelola: judul, member, platform, tanggal, dan thumbnail. Selesainya proses unggah tidak otomatis membuat replay terbit — pengelola bisa menerbitkan lebih cepat atau menahannya.</p></section>
    <section className="panel"><h2>Privasi & layanan pihak ketiga</h2><p>Anda tidak perlu akun untuk menjelajahi arsip. Pemutar dan thumbnail menggunakan layanan YouTube, sehingga browser dapat mengirim alamat IP dan informasi perangkat ke penyedia tersebut. Font situs ini disajikan dari server sendiri (self-hosted), jadi tidak ada permintaan ke penyedia font pihak ketiga. Pemutaran video tunduk pada kebijakan YouTube.</p><p>Cookie sesi digunakan hanya untuk login pengelola. Pencarian tersimpan di URL dan dapat tercatat dalam riwayat browser serta log akses server.</p></section>
    <section className="panel"><h2>Hak konten</h2><p>Publikasi di situs tidak mengalihkan hak cipta. Untuk laporan pelanggaran pada video, gunakan fasilitas pelaporan YouTube melalui tautan video terkait. Penarikan dari arsip tidak menghapus video dari platform asal.</p></section>
  </article>;
}
