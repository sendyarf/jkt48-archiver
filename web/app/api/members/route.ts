import { NextResponse } from 'next/server';
import { getPublicMembers } from '@/lib/db';

export const dynamic = 'force-dynamic';
export async function GET() {
  try {
    return NextResponse.json({ success: true, members: getPublicMembers() }, { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json({ success: false, message: 'Direktori sementara tidak tersedia.' }, { status: 503 });
  }
}
