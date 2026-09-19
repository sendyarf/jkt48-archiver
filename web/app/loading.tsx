/**
 * loading.tsx - Skeleton katalog saat data sedang dimuat (navigasi/filter).
 * Meniru bentuk video-card agar transisi terasa instan, bukan layar kosong.
 */
export default function CatalogLoading() {
  return (
    <div className="container page-section" aria-busy="true" aria-live="polite">
      <p className="result-summary" style={{ visibility: 'hidden' }}>
        Memuat replay…
      </p>
      <div className="video-grid">
        {Array.from({ length: 8 }).map((_, i) => (
          <div className="skeleton-card" key={i}>
            <div className="skeleton-thumb" />
            <div className="skeleton-lines">
              <div className="skeleton-line" />
              <div className="skeleton-line short" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
