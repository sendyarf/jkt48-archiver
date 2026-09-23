import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { isAdmin } from '@/lib/auth';
import LoginForm from '@/components/LoginForm';
export const metadata: Metadata = { title: 'Login Admin', robots: { index: false, follow: false } };
export default async function LoginPage() {
  if (await isAdmin()) redirect('/admin');
  const siteKey = process.env.TURNSTILE_SITE_KEY;
  return <div className="container page-section"><LoginForm turnstileSiteKey={siteKey && siteKey !== 'off' ? siteKey : undefined} /></div>;
}
