import { redirect } from 'next/navigation';
export const dynamic = 'force-dynamic';
// Fallback bila redirects di next.config tidak terpakai (mis. dev tertentu).
export default function StatusPage() { redirect('/admin/status'); }
