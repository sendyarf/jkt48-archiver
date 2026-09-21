import type { Metadata } from 'next';
export const metadata: Metadata = { title: 'Tentang & Privasi' };
export default function AboutPage() {
  return <article className="container page-section prose"><p className="eyebrow">TENTANG SITUS</p><h1>Arsip yang dibuat fans, untuk fans</h1><p>JKT48 Replay adalah arsip komunitas untuk nonton ulang live JKT48. Situs ini bukan situs resmi dan tidak berafiliasi dengan JKT48, IDN, Showroom, maupun YouTube — hak atas kontennya tetap milik pemiliknya.</p>
    <section className="panel"><h2>Apa saja yang ada di sini?</h2><p>Di halaman publik kamu bisa melihat replay yang sudah lolos kurasi: judul, member, platform, tanggal, dan thumbnail-nya. Selesai di-upload belum berarti replay langsung tayang — pengelola yang memutuskan, dan bisa menerbitkan lebih cepat atau menahannya.</p></section>
    <section className="panel"><h2>Privasi & layanan pihak ketiga</h2><p>Kamu tidak perlu bikin akun untuk menjelajah arsip. Pemutar video dan thumbnail di sini datang dari YouTube, jadi browser kamu bisa mengirim alamat IP dan informasi perangkat ke mereka. Font situs ini kami simpan sendiri (self-hosted), sehingga tidak ada permintaan ke penyedia font pihak ketiga. Urusan pemutaran video ikut aturan YouTube.</p><p>Cookie sesi dipakai hanya untuk login pengelola. Kata kunci pencarian tersimpan di URL, jadi bisa ikut tercatat di riwayat browser kamu dan di log akses server.</p></section>
    <section className="panel"><h2>Soal hak cipta</h2><p>Menampilkan video di sini tidak memindahkan hak ciptanya. Kalau ada video yang bermasalah, laporkan lewat fitur pelaporan YouTube pada video terkait. Menarik video dari arsip di sini juga tidak menghapusnya dari platform aslinya.</p></section>
  </article>;
}
