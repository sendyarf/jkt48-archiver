import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * Foto member dari roster resmi jkt48.com (jkt48_members.json, dihasilkan
 * bot/jkt48_members.py). Dipakai sebagai FALLBACK bila kolom bot
 * `tiktok_accounts.avatar_url` kosong (member belum punya akun TikTok tertaut).
 *
 * Pencocokan: (1) nickname/nama di-normalisasi huruf kecil tanpa spasi/`JKT48`,
 * (2) handle TikTok roster. Indeks dibangun sekalian lalu di-cache di memori.
 */

interface RosterMember {
  name?: string;
  nickname?: string;
  tiktok_account?: string;
  photo?: string;
}

interface PhotoIndex {
  byName: Map<string, string>;
  byTiktok: Map<string, string>;
}

let cached: PhotoIndex | null = null;

function normalizeKey(value: string): string {
  return (value || '')
    .toLowerCase()
    .replace(/\bjkt48\b/g, '')
    .replace(/[^a-z0-9]/g, '');
}

function rosterPathCandidates(): string[] {
  return [
    path.resolve(process.cwd(), '..', 'jkt48_members.json'),
    path.resolve(process.cwd(), 'jkt48_members.json'),
  ];
}

function loadIndex(): PhotoIndex {
  if (cached) return cached;
  const index: PhotoIndex = { byName: new Map(), byTiktok: new Map() };
  try {
    const file = rosterPathCandidates().find((p) => existsSync(p));
    if (!file) {
      cached = index;
      return cached;
    }
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as { members?: RosterMember[] };
    for (const member of parsed.members || []) {
      const photo = (member.photo || '').trim();
      if (!photo) continue;
      const nick = normalizeKey(member.nickname || '');
      const name = normalizeKey(member.name || '');
      if (nick && !index.byName.has(nick)) index.byName.set(nick, photo);
      if (name && !index.byName.has(name)) index.byName.set(name, photo);
      const handle = normalizeKey(member.tiktok_account || '');
      if (handle) index.byTiktok.set(handle, photo);
    }
  } catch {
    // Roster tidak terbaca — biarkan fallback kosong (inisial tetap tampil).
  }
  cached = index;
  return cached;
}

/** Cari foto roster untuk username/display name member (null bila tidak ketemu). */
export function rosterPhotoFor(username: string, displayName: string): string | null {
  const index = loadIndex();
  const handleCandidates = [
    normalizeKey(username),
    normalizeKey(username.replace(/^jkt48_?/, '')),
    normalizeKey(displayName),
  ];
  for (const key of handleCandidates) {
    const hit = key ? index.byTiktok.get(key) : undefined;
    if (hit) return hit;
  }
  const nameKey = normalizeKey(displayName);
  if (nameKey) {
    const exact = index.byName.get(nameKey);
    if (exact) return exact;
    // "Indah JKT48" → "indah" cocok dengan nickname roster "Indah".
    for (const [key, photo] of index.byName) {
      if (key === nameKey || nameKey === key || nameKey.startsWith(key) || key.startsWith(nameKey)) {
        if (nameKey.length >= 3 && key.length >= 3) return photo;
      }
    }
  }
  return null;
}
