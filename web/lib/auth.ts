import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { cookies } from 'next/headers';
import { getDb } from './db';

export const SESSION_COOKIE = 'jkt48_admin_session';
export const SESSION_SECONDS = 8 * 60 * 60;
const hash = (value: string) => createHash('sha256').update(value).digest('hex');

function authDb() {
  const db = getDb();
  db.exec(`CREATE TABLE IF NOT EXISTS web_admin_sessions (
    token_hash TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, expires_at INTEGER NOT NULL
  ); CREATE TABLE IF NOT EXISTS web_login_limits (
    bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires_at INTEGER NOT NULL
  );`);
  return db;
}

export function configuredSecret() {
  const secret = process.env.ADMIN_SECRET;
  return secret && secret.length >= 32 ? secret : null;
}

export function validSecret(input: unknown): boolean {
  const expected = configuredSecret();
  return !!expected && typeof input === 'string' && input.length <= 1024 &&
    timingSafeEqual(Buffer.from(hash(input)), Buffer.from(hash(expected)));
}

// Shared SQLite limit: survives restarts and does not trust forwarded IP headers.
export function allowLogin(): boolean {
  const db = authDb();
  const now = Date.now();
  db.prepare('DELETE FROM web_login_limits WHERE expires_at <= ?').run(now);
  const row = db.prepare(`INSERT INTO web_login_limits (bucket, attempts, expires_at)
    VALUES ('admin', 1, ?) ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1
    RETURNING attempts`).get(now + 15 * 60 * 1000) as { attempts: number };
  return row.attempts <= 20;
}

export async function createSession() {
  const secret = configuredSecret();
  if (!secret) throw new Error('Admin unavailable');
  const db = authDb();
  db.prepare('DELETE FROM web_admin_sessions WHERE expires_at <= ?').run(Date.now());
  await destroySession();
  const token = randomBytes(32).toString('hex');
  db.prepare('INSERT INTO web_admin_sessions VALUES (?, ?, ?)')
    .run(hash(token), hash(secret), Date.now() + SESSION_SECONDS * 1000);
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true, secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict', path: '/', maxAge: SESSION_SECONDS,
  });
}

export async function isAdmin(): Promise<boolean> {
  const secret = configuredSecret();
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!secret || !token || !/^[a-f0-9]{64}$/.test(token)) return false;
  return !!authDb().prepare(`SELECT 1 FROM web_admin_sessions
    WHERE token_hash = ? AND secret_hash = ? AND expires_at > ?`)
    .get(hash(token), hash(secret), Date.now());
}

export async function destroySession() {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (token) authDb().prepare('DELETE FROM web_admin_sessions WHERE token_hash = ?').run(hash(token));
  jar.set(SESSION_COOKIE, '', { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'strict', path: '/', maxAge: 0 });
}

export function sameOrigin(request: Request): boolean {
  const expected = process.env.APP_ORIGIN || new URL(request.url).origin;
  return request.headers.get('origin') === expected;
}
