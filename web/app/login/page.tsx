import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { isAdmin } from '@/lib/auth';
import LoginForm from '@/components/LoginForm';
export const metadata: Metadata = { title: 'Login Admin | JKT48 Live', robots: { index: false, follow: false } };
export default async function LoginPage() {
  if (await isAdmin()) redirect('/admin');
  return <div className="container page-section"><LoginForm /></div>;
}
