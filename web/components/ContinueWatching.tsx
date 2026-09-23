'use client';

import { useMemo, useSyncExternalStore } from 'react';
import Link from 'next/link';
import Image from 'next/image';

interface Entry {
  id: string;
  title: string;
  thumbnail: string;
  streamer: string;
}

const KEY = 'jkt48_continue_watching';

const EMPTY = '';

function subscribe() {
  return () => {};
}

function getServerSnapshot() {
  return EMPTY;
}

function getClientSnapshot() {
  return localStorage.getItem(KEY) ?? EMPTY;
}

function parseEntries(raw: string): Entry[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw) as Entry[];
    if (!Array.isArray(list)) return [];
    // WatchTracker menyimpan path penuh (/watch/…); data lama bisa berupa
    // token mentah — normalisasi agar href tidak jadi /watch//watch/….
    return list
      .map((entry) => ({
        ...entry,
        id: entry.id.startsWith('/') ? entry.id : `/watch/${entry.id}`,
      }))
      .slice(0, 4);
  } catch {
    // data rusak — biarkan kosong
    return [];
  }
}

/** Lanjutkan menonton: membaca localStorage via useSyncExternalStore (hindari hydration mismatch). */
export default function ContinueWatching() {
  const raw = useSyncExternalStore(subscribe, getClientSnapshot, getServerSnapshot);
  const entries = useMemo(() => parseEntries(raw), [raw]);

  if (!entries.length) return null;

  return (
    <section className="container page-section continue-watching" aria-labelledby="continue-watching-title">
      <header className="section-heading">
        <div>
          <p className="eyebrow">LANJUTKAN</p>
          <h2 id="continue-watching-title">Lanjutkan menonton</h2>
        </div>
      </header>
      <div className="video-grid">
        {entries.map((entry) => (
          <Link key={entry.id} href={entry.id} className="continue-card">
            <div className="continue-thumb-box">
              {entry.thumbnail ? (
                <Image
                  src={entry.thumbnail}
                  alt={entry.title}
                  fill
                  sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
                  className="continue-thumb"
                />
              ) : null}
            </div>
            <div className="continue-info">
              <h3 className="continue-title">{entry.title}</h3>
              <span className="continue-meta">{entry.streamer}</span>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}
