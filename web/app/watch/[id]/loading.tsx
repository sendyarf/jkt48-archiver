/** Skeleton halaman watch — meniru pemutar + detail agar transisi tidak blank. */
export default function WatchLoading() {
  return (
    <div className="container page-section" aria-busy="true" aria-live="polite">
      <div className="watch-layout">
        <div>
          <div className="skeleton-card" style={{ aspectRatio: '16 / 9', borderRadius: 'var(--radius-xl)' }} />
          <div className="watch-details-card">
            <div className="skeleton-line" style={{ height: '20px', width: '70%' }} />
            <div className="skeleton-line short" />
            <div className="skeleton-line short" />
          </div>
        </div>
        <aside>
          <div className="skeleton-line" style={{ marginBottom: '10px' }} />
          <div className="skeleton-card"><div className="skeleton-thumb" /><div className="skeleton-lines"><div className="skeleton-line" /><div className="skeleton-line short" /></div></div>
        </aside>
      </div>
    </div>
  );
}
