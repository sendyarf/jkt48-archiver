import type { Metadata } from 'next';
export const metadata: Metadata = { title: 'Tentang & Privasi' };
export default function AboutPage() {
  return <article className="container page-section prose"><p className="eyebrow">TENTANG SITUS</p><h1>Arsip untuk komunitas</h1><p>JKT48 Replay merupakan katalog komunitas untuk menelusuri siaran ulang. Situs ini tidak berafiliasi dengan JKT48, IDN, Showroom, atau YouTube. Hak atas konten tetap dimiliki pemiliknya.</p>
    <section className="panel"><h2>Apa yang tersedia di sini?</h2><p>Halaman publik menampilkan rekaman yang telah disetujui pengelola untuk diterbitkan, beserta judul, nama member, tanggal, dan thumbnail. Selesainya proses upload tidak otomatis membuat rekaman masuk katalog.</p></section>
    <section className="panel"><h2>Privasi & layanan pihak ketiga</h2><p>Anda tidak perlu akun untuk menjelajahi katalog. Pemutar dan thumbnail menggunakan layanan YouTube; avatar di beberapa halaman menggunakan ui-avatars.com, dan font menggunakan Google Fonts. Browser dapat mengirim alamat IP dan informasi perangkat ke penyedia tersebut. Pemutaran video tunduk pada kebijakan YouTube.</p><p>Cookie sesi digunakan hanya untuk login pengelola. Pencarian tersimpan di URL dan dapat tercatat dalam riwayat browser serta log akses server.</p></section>
    <section className="panel"><h2>Hak konten</h2><p>Publikasi di situs tidak mengalihkan hak cipta. Untuk laporan pelanggaran pada video, gunakan fasilitas pelaporan YouTube melalui tautan video terkait. Penarikan dari katalog tidak menghapus video dari platform asal.</p></section>
  </article>;
}
