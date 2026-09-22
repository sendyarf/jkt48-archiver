import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * Profil member dari cache resmi jkt48.com (jkt48_members.json) + daftar room
 * Showroom (showroom_rooms.json). Dipakai halaman /members/[username] untuk
 * menautkan kanal live IDN, live Showroom, dan sosial — bukan cuma TikTok.
 *
 * Pencocokan roster: nickname/nama/tiktok di-normalisasi (huruf kecil, tanpa
 * spasi/`JKT48`) — sama seperti member-photos.ts. Showroom dicocokkan lewat
 * kolom `idn_username` (sama dengan username IDN di member_hls).
 */

export interface RosterSocials {
  name: string;
  nickname: string;
  type: string;
  photo: string;
  tiktok: string;
  instagram: string;
  twitter: string;
}

export interface MemberLinks {
  /** Profil resmi di jkt48.com (null bila tidak ada di roster). */
  jkt48: string | null;
  /** Live channel IDN — username member adalah handle IDN (jkt48_xxx). */
  idn: string | null;
  /** Live room Showroom (null bila member tidak punya room). */
  showroom: string | null;
  tiktok: string | null;
  instagram: string | null;
  twitter: string | null;
}

export interface MemberProfileLinks {
  socials: RosterSocials | null;
  links: MemberLinks;
  /** Foto roster resmi (null bila kosong/tidak cocok). */
  photo: string | null;
}

interface RosterMember {
  jkt48_member_id?: number | string;
  code?: string;
  name?: string;
  nickname?: string;
  type?: string;
  tiktok_account?: string;
  instagram_account?: string;
  twitter_account?: string;
  photo?: string;
}

interface ShowroomRoom {
  room_id?: string;
  room_url_key?: string;
  display_name?: string;
  idn_username?: string;
}

let rosterCache: RosterMember[] | null = null;
let showroomCache: ShowroomRoom[] | null = null;
let linkIndex: {
  byName: Map<string, RosterMember>;
  byTiktok: Map<string, RosterMember>;
  byIdn: Map<string, ShowroomRoom>;
  byDisplayName: Map<string, ShowroomRoom>;
} | null = null;

function normalizeKey(value: string): string {
  return (value || '')
    .toLowerCase()
    .replace(/\bjkt48\b/g, '')
    .replace(/[^a-z0-9]/g, '');
}

function jsonCandidates(fileName: string): string[] {
  return [
    path.resolve(process.cwd(), '..', fileName),
    // turbopackIgnore: akses berkas opsional (cache JSON di root repo saat dev/build)
    path.resolve(/* turbopackIgnore: true */ process.cwd(), fileName),
  ];
}

function readJson<T>(fileName: string): T | null {
  try {
    const file = jsonCandidates(fileName).find((p) => existsSync(p));
    if (!file) return null;
    return JSON.parse(readFileSync(file, 'utf8')) as T;
  } catch {
    return null;
  }
}

function loadIndex() {
  if (linkIndex) return linkIndex;

  const rosterFile = readJson<{ members?: RosterMember[] }>('jkt48_members.json');
  rosterCache = rosterFile?.members || [];

  const showroomFile = readJson<{ rooms?: ShowroomRoom[] }>('showroom_rooms.json');
  showroomCache = showroomFile?.rooms || [];

  const index = {
    byName: new Map<string, RosterMember>(),
    byTiktok: new Map<string, RosterMember>(),
    byIdn: new Map<string, ShowroomRoom>(),
    byDisplayName: new Map<string, ShowroomRoom>(),
  };

  for (const m of rosterCache) {
    const nick = normalizeKey(m.nickname || '');
    const name = normalizeKey(m.name || '');
    const code = normalizeKey(m.code || '');
    if (nick && !index.byName.has(nick)) index.byName.set(nick, m);
    if (name && !index.byName.has(name)) index.byName.set(name, m);
    if (code && !index.byName.has(code)) index.byName.set(code, m);
    const handle = normalizeKey(m.tiktok_account || '');
    if (handle && !index.byTiktok.has(handle)) index.byTiktok.set(handle, m);
  }

  for (const room of showroomCache) {
    const idn = (room.idn_username || '').trim().toLowerCase();
    if (idn && !index.byIdn.has(idn)) index.byIdn.set(idn, room);
    const dn = normalizeKey(room.display_name || '');
    if (dn && !index.byDisplayName.has(dn)) index.byDisplayName.set(dn, room);
  }

  linkIndex = index;
  return linkIndex;
}

function showroomUrl(room: ShowroomRoom): string | null {
  const key = (room.room_url_key || '').trim();
  if (key) return `https://www.showroom-live.com/r/${key}`;
  const id = (room.room_id || '').trim();
  if (id) return `https://www.showroom-live.com/room/${id}`;
  return null;
}

function findRoster(username: string, displayName: string): RosterMember | null {
  const index = loadIndex();
  const keys = [
    normalizeKey(username),
    normalizeKey(username.replace(/^jkt48_?/, '')),
    normalizeKey(displayName),
  ].filter(Boolean);

  for (const key of keys) {
    const byTt = index.byTiktok.get(key);
    if (byTt) return byTt;
  }

  const nameKey = normalizeKey(displayName);
  if (nameKey) {
    const exact = index.byName.get(nameKey);
    if (exact) return exact;
    for (const [key, member] of index.byName) {
      if (
        key === nameKey ||
        nameKey.startsWith(key) ||
        key.startsWith(nameKey)
      ) {
        if (nameKey.length >= 3 && key.length >= 3) return member;
      }
    }
  }

  // Fallback: kode roster dari username IDN (jkt48_aralie → aralie → ABIGAIL…)
  // sudah dicoba di atas lewat nickname; coba juga strip underscore.
  const stripped = normalizeKey(username.replace(/^jkt48_?/, ''));
  if (stripped) {
    const exact = index.byName.get(stripped);
    if (exact) return exact;
  }
  return null;
}

function findShowroom(username: string, displayName: string): ShowroomRoom | null {
  const index = loadIndex();
  const idn = username.trim().toLowerCase();
  if (idn && index.byIdn.has(idn)) return index.byIdn.get(idn)!;
  const dn = normalizeKey(displayName);
  if (dn && index.byDisplayName.has(dn)) return index.byDisplayName.get(dn)!;
  const nick = normalizeKey(displayName.replace(/\bjkt48\b/gi, ''));
  if (nick && index.byDisplayName.has(nick)) return index.byDisplayName.get(nick)!;
  return null;
}

/**
 * Kumpulkan tautan eksternal + info roster untuk satu member.
 * `username` = username IDN (member_hls.username, mis. jkt48_aralie).
 */
export function getMemberProfileLinks(
  username: string,
  displayName: string,
): MemberProfileLinks {
  const roster = findRoster(username, displayName);
  const room = findShowroom(username, displayName);

  const idnHandle = (username || '').trim();
  const idn = idnHandle ? `https://www.idn.app/${idnHandle}` : null;
  const showroom = room ? showroomUrl(room) : null;

  const tiktokHandle = (roster?.tiktok_account || '').trim().replace(/^@/, '');
  const instagram = (roster?.instagram_account || '').trim().replace(/^@/, '');
  const twitter = (roster?.twitter_account || '').trim().replace(/^@/, '');

  const socials: RosterSocials | null = roster
    ? {
        name: (roster.name || '').trim(),
        nickname: (roster.nickname || '').trim(),
        type: (roster.type || '').trim(),
        photo: (roster.photo || '').trim(),
        tiktok: tiktokHandle,
        instagram,
        twitter,
      }
    : null;

  const links: MemberLinks = {
    jkt48: roster?.jkt48_member_id
      ? `https://jkt48.com/member/${roster.jkt48_member_id}`
      : null,
    idn,
    showroom,
    tiktok: tiktokHandle ? `https://www.tiktok.com/@${tiktokHandle}` : null,
    instagram: instagram ? `https://www.instagram.com/${instagram}/` : null,
    twitter: twitter ? `https://x.com/${twitter}` : null,
  };

  return {
    socials,
    links,
    photo: socials?.photo || null,
  };
}
