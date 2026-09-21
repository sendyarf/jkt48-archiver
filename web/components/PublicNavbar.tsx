'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { Menu, Users, Video, X } from 'lucide-react';

function NavbarInner() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);

  // Tutup menu setiap navigasi selesai (path ATAU query berubah).
  const navKey = `${pathname}?${searchParams.toString()}`;
  const [lastNavKey, setLastNavKey] = useState(navKey);
  if (navKey !== lastNavKey) {
    setLastNavKey(navKey);
    setOpen(false);
  }

  // Kunci scroll body saat menu terbuka agar tidak bocor ke konten.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open ]);

  return (
    <header className="navbar">
      <div className="container nav-container">
        <Link href="/" className="brand" aria-label="JKT48 Replay — halaman utama">
          <span className="brand-title">JKT48 <strong>REPLAY</strong></span>
        </Link>
        <button
          type="button"
          className="nav-toggle"
          aria-expanded={open}
          aria-controls="navigasi-utama"
          aria-label={open ? 'Tutup menu navigasi' : 'Buka menu navigasi'}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
        </button>
        <nav id="navigasi-utama" className={`nav-menu${open ? ' open' : ''}`} aria-label="Navigasi utama">
          <Link href="/" className={`nav-link ${pathname === '/' ? 'active' : ''}`} aria-current={pathname === '/' ? 'page' : undefined}>
            <Video size={16} aria-hidden="true" /> Replay
          </Link>
          <Link href="/members" className={`nav-link ${pathname === '/members' ? 'active' : ''}`} aria-current={pathname === '/members' ? 'page' : undefined}>
            <Users size={16} aria-hidden="true" /> Member
          </Link>
        </nav>
        {open && (
          <button type="button" className="nav-scrim" aria-hidden="true" tabIndex={-1} onClick={() => setOpen(false)} />
        )}
      </div>
    </header>
  );
}

export default function Navbar() {
  return (
    <Suspense>
      <NavbarInner />
    </Suspense>
  );
}
