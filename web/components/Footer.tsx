import Link from 'next/link';

export default function Footer() {
  return (
    <footer className="footer">
      <div className="container footer-content">
        <div>
          <p className="footer-brand">JKT48 Replay</p>
          <p>Arsip komunitas fans. Bukan situs resmi JKT48, IDN, Showroom, atau YouTube.</p>
        </div>
        <nav className="footer-links" aria-label="Navigasi footer">
          <Link href="/" className="footer-link">Replay</Link>
          <Link href="/members" className="footer-link">Member</Link>
          <Link href="/about" className="footer-link">Tentang & privasi</Link>
        </nav>
      </div>
    </footer>
  );
}

