'use client';

import { useEffect } from 'react';

interface Props {
  watchId: string;
  title: string;
  thumbnailUrl: string;
  streamerName: string;
}

const KEY = 'jkt48_continue_watching';
const MAX = 6;

/** Menyimpan replay yang sedang dibuka ke localStorage (tanpa UI). */
export default function WatchTracker({ watchId, title, thumbnailUrl, streamerName }: Props) {
  useEffect(() => {
    if (!watchId) return;
    try {
      const raw = localStorage.getItem(KEY);
      const list: Array<{ id: string; title: string; thumbnail: string; streamer: string; at: number }> = raw
        ? JSON.parse(raw)
        : [];
      const next = [
        { id: watchId, title, thumbnail: thumbnailUrl, streamer: streamerName, at: Date.now() },
        ...list.filter((item) => item.id !== watchId),
      ].slice(0, MAX);
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // localStorage tidak tersedia — abaikan.
    }
  }, [watchId, title, thumbnailUrl, streamerName]);

  return null;
}
