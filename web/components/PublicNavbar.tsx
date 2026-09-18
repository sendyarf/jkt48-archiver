'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Users, Video } from 'lucide-react';

export default function Navbar() {
  const pathname = usePathname();
  return (
    <header className="navbar">
      <div className="container nav-container">
        <Link href="/" className="brand" aria-label="JKT48 Live — Beranda">
          <span className="brand-logo">48</span>
          <span className="brand-title">JKT48 LIVE</span>
          <span className="brand-badge">ARCHIVE</span>
        </Link>
        <nav className="nav-menu" aria-label="Navigasi utama">
          <Link href="/" className={`nav-link ${pathname === '/' ? 'active' : ''}`} aria-current={pathname === '/' ? 'page' : undefined}>
            <Video size={16} aria-hidden="true" /> Jelajahi
          </Link>
          <Link href="/members" className={`nav-link ${pathname === '/members' ? 'active' : ''}`} aria-current={pathname === '/members' ? 'page' : undefined}>
            <Users size={16} aria-hidden="true" /> Member
          </Link>
        </nav>
      </div>
    </header>
  );
}
