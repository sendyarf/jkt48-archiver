import { redirect } from 'next/navigation';
import { isAdmin } from '@/lib/auth';
import type { Metadata } from 'next';
import Link from 'next/link';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Administrasi | JKT48 Live', robots: { index: false, follow: false } };
export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  if (!(await isAdmin())) redirect('/login');
  return <div className="admin-shell container">
    <aside className="admin-sidebar"><p className="eyebrow">RUANG PENGELOLA</p><h2>Admin Studio</h2>
      <nav aria-label="Navigasi admin"><Link href="/admin">Member</Link><Link href="/admin/publications">Publikasi</Link><Link href="/admin/status">Sistem & antrean</Link><Link href="/admin/trial">Ujicoba pemutar</Link><Link href="/">Kembali ke situs</Link></nav>
    </aside><div className="admin-content">{children}</div>
  </div>;
}
