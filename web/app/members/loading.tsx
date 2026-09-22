/** Skeleton halaman daftar member. */
export default function MembersLoading() {
  return (
    <div className="container page-section" aria-busy="true" aria-live="polite">
      <header className="section-heading">
        <div>
          <p className="eyebrow">DAFTAR MEMBER</p>
          <h1>Temukan member favoritmu</h1>
        </div>
      </header>
      <div className="members-grid">
        {Array.from({ length: 12 }).map((_, i) => (
          <div className="member-card skeleton-card" key={i}>
            <span className="member-initial" aria-hidden="true" />
            <div className="skeleton-line" style={{ width: '70%' }} />
            <div className="skeleton-line short" />
          </div>
        ))}
      </div>
    </div>
  );
}
