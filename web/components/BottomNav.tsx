'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Music2, Users, Video } from 'lucide-react';

const LINKS = [
  { href: '/', label: 'Replay', icon: Video },
  { href: '/members', label: 'Member', icon: Users },
  { href: '/tiktok', label: 'TikTok', icon: Music2 },
] as const;

/** Navigasi bawah untuk layar kecil — hanya rute publik (tanpa /status /admin). */
export default function BottomNav() {
  const pathname = usePathname();
  return (
    <nav className="bottom-nav" aria-label="Navigasi bawah">
      {LINKS.map(({ href, label, icon: Icon }) => {
        const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={`bottom-nav-item${active ? ' active' : ''}`}
            aria-current={active ? 'page' : undefined}
          >
            <Icon size={18} aria-hidden="true" />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
