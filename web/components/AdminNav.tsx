'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const GROUPS: { label: string; items: { href: string; label: string }[] }[] = [
  {
    label: 'Konten',
    items: [
      { href: '/admin', label: 'Member' },
      { href: '/admin/publications', label: 'Publikasi' },
      { href: '/admin/videos', label: 'Sembunyikan' },
    ],
  },
  {
    label: 'Operasional',
    items: [
      { href: '/admin/queue', label: 'Antrean' },
      { href: '/admin/status', label: 'Sistem' },
    ],
  },
  {
    label: 'Alat',
    items: [{ href: '/admin/trial', label: 'Uji pemutar' }],
  },
];

function isActive(href: string, pathname: string): boolean {
  if (href === '/admin') return pathname === '/admin';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function AdminNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Navigasi admin" className="admin-nav">
      {GROUPS.map((group) => (
        <div className="admin-nav-group" key={group.label}>
          <p className="admin-nav-label">{group.label}</p>
          <div className="admin-nav-items">
            {group.items.map((item) => {
              const active = isActive(item.href, pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? 'active' : undefined}
                  aria-current={active ? 'page' : undefined}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
      <div className="admin-nav-group">
        <p className="admin-nav-label">Lainnya</p>
        <div className="admin-nav-items">
          <Link href="/">← Situs publik</Link>
        </div>
      </div>
    </nav>
  );
}
