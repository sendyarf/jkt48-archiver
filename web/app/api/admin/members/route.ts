import { NextResponse } from 'next/server';
import { isAdmin, sameOrigin } from '@/lib/auth';
import { getStreamersList, setMemberEnabled, addMember } from '@/lib/db';
export const dynamic = 'force-dynamic';
const json = (body: object, status = 200) => NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
export async function GET() {
  if (!(await isAdmin())) return json({ success: false }, 401);
  try { return json({ success: true, members: getStreamersList() }); }
  catch { return json({ success: false, message: 'Data tidak tersedia.' }, 503); }
}
export async function POST(request: Request) {
  if (!(await isAdmin())) return json({ success: false }, 401);
  if (!sameOrigin(request)) return json({ success: false }, 403);
  try {
    const { action, username, enabled, displayName } = await request.json();
    if (typeof username !== 'string' || !/^[a-zA-Z0-9_.-]{1,80}$/.test(username)) return json({ success: false, message: 'Username tidak valid.' }, 400);
    let ok = false;
    if (action === 'toggle' && typeof enabled === 'boolean') ok = setMemberEnabled(username, enabled);
    else if (action === 'add' && typeof displayName === 'string' && displayName.length <= 120) ok = addMember(username, displayName);
    else return json({ success: false, message: 'Data tidak valid.' }, 400);
    return json({ success: ok, message: ok ? 'Perubahan disimpan.' : 'Member tidak ditemukan.' }, ok ? 200 : 404);
  } catch { return json({ success: false, message: 'Perubahan gagal disimpan.' }, 400); }
}
