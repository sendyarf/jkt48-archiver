import { redirect } from 'next/navigation';
// Fallback bila redirects di next.config tidak terpakai (mis. dev tertentu).
// Tidak perlu force-dynamic — hanya redirect, tanpa data/request API.
export default function StatusPage() { redirect('/admin/status'); }
