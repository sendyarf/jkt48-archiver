import type { Metadata } from 'next';
import VideoPlayer from '@/components/VideoPlayer';

export const metadata: Metadata = {
  title: 'Ujicoba Pemutar | Admin',
  robots: { index: false, follow: false },
};

export const dynamic = 'force-dynamic';

const YT_ID = /^[A-Za-z0-9_-]{11}$/;
const SAFE_URL = /^https?:\/\//i;

interface TrialParams {
  platform?: string;
  youtube?: string;
  src?: string;
  title?: string;
  poster?: string;
}

export default async function PlayerTrialPage({
  searchParams,
}: {
  searchParams: Promise<TrialParams>;
}) {
  const params = await searchParams;
  const platform = params.platform === 'showroom' ? 'showroom' : 'idn';
  const youtube = params.youtube && YT_ID.test(params.youtube) ? params.youtube : '';
  const src = params.src && SAFE_URL.test(params.src) ? params.src : '';
  const poster = params.poster && SAFE_URL.test(params.poster) ? params.poster : '';
  const title = (params.title || 'Ujicoba pemutar').slice(0, 120);
  const isHls = /\.m3u8(\?|$)/i.test(src);
  const hasSource = Boolean(src || youtube);

  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">ALAT UJI · PRIVATE</p>
          <h1>Ujicoba pemutar video</h1>
          <p>
            Halaman ini hanya untuk menguji tampilan pemutar. Tidak terhubung ke katalog dan tidak
            dipublikasikan ke pengunjung.
          </p>
        </div>
      </div>

      <form className="form-row" action="/admin/trial" method="GET">
        <label>
          Platform (menentukan orientasi)
          <select name="platform" defaultValue={platform}>
            <option value="showroom">Showroom — landscape 16:9</option>
            <option value="idn">IDN — vertikal 9:16</option>
          </select>
        </label>
        <label>
          YouTube ID (11 karakter)
          <input name="youtube" defaultValue={youtube} maxLength={11} placeholder="mis. R8pnx79dyDQ" />
        </label>
        <label>
          Atau URL media langsung (.mp4 / .m3u8)
          <input name="src" defaultValue={src} maxLength={300} placeholder="https://.../video.mp4" />
        </label>
        <label>
          Judul
          <input name="title" defaultValue={title} maxLength={120} />
        </label>
        <label>
          Poster (opsional)
          <input name="poster" defaultValue={poster} maxLength={300} placeholder="https://.../thumb.jpg" />
        </label>
        <button className="primary-button">Muat pemutar</button>
      </form>

      {!hasSource && (
        <p className="notice">
          Isi <strong>YouTube ID</strong> atau <strong>URL media langsung</strong> lalu tekan
          “Muat pemutar”. Untuk ujicoba Showroom, pilih platform <strong>Showroom</strong> agar
          pemutar memakai tata letak landscape 16:9.
        </p>
      )}

      {isHls && (
        <p className="notice error">
          URL <code>.m3u8</code> memerlukan <code>hls.js</code> yang belum terpasang di proyek ini.
          Untuk ujicoba HLS, pakai berkas <code>.mp4</code> dulu, atau setujui penambahan dependensi{' '}
          <code>hls.js</code>.
        </p>
      )}

      {hasSource && (
        <div id="trial-player">
          <VideoPlayer
            key={`${platform}|${youtube}|${src}|${poster}`}
            youtubeId={youtube}
            directSrc={src || undefined}
            title={title}
            poster={poster || undefined}
            platform={platform}
          />
        </div>
      )}

      <div className="panel">
        <h2>Yang bisa disimpulkan dari ujicoba ini</h2>
        <ul className="trial-notes">
          <li>
            Platform <strong>showroom</strong> memakai kelas <code>horizontal-player</code> dan rasio
            paksa <code>16 / 9</code>; tombol FIT/FILL/9:16 hanya muncul untuk IDN karena memang
            khusus vertikal.
          </li>
          <li>
            Mode Theater, kontrol volume, scrubber, dan Escape tetap berfungsi di kedua orientasi —
            lebar theater mengikuti variabel <code>--theater-ratio</code> (16:9 vs 9:16).
          </li>
          <li>
            Sumber masih berupa YouTube atau berkas media langsung. Bila nanti Showroom diunggah ke
            YouTube (target upload global), pemutar yang sama langsung bekerja tanpa perubahan.
          </li>
        </ul>
      </div>
    </section>
  );
}