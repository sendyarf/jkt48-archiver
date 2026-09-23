import { notFound } from 'next/navigation';
import { isAdmin } from '@/lib/auth';
import type { Metadata } from 'next';
import AdminNav from '@/components/AdminNav';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Administrasi', robots: { index: false, follow: false } };
export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  if (!(await isAdmin())) notFound();
  return <div className="admin-shell container">
    <aside className="admin-sidebar"><p className="eyebrow">RUANG PENGELOLA</p><h2>Admin Studio</h2>
      <AdminNav />
    </aside><div className="admin-content">{children}</div>
  </div>;
}
