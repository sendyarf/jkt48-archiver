import type { Metadata } from 'next';

export const dynamic = 'force-dynamic';
export { default } from '@/components/PublicCatalog';

// Kanonis tetap "/" — varian ?q=&member=&platform=&page= di-consolidate ke root.
export const metadata: Metadata = {
  alternates: { canonical: '/' },
};
