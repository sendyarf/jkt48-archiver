'use client';

import { Moon, Sun } from 'lucide-react';
import { useCallback, useSyncExternalStore } from 'react';

type Theme = 'light' | 'dark';

const STORAGE_KEY = 'jkt48_theme';
const listeners = new Set<() => void>();
let cached: Theme | null = null;
let hydrated = false;

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    /* ignore */
  }
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: light)').matches) {
    return 'light';
  }
  return 'dark';
}

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): Theme | null {
  if (!hydrated) {
    hydrated = true;
    cached = readTheme();
  }
  return cached;
}

function getServerSnapshot(): Theme | null {
  return null;
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
  cached = theme;
  emit();
}

/** Tombol ganti tema light/dark — sinkron dengan atribut data-theme di <html>. */
export default function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const handleToggle = useCallback(() => {
    const current = theme ?? readTheme();
    applyTheme(current === 'dark' ? 'light' : 'dark');
  }, [theme]);

  if (!theme) {
    // Placeholder stabil agar layout navbar tidak melompat saat hydrate.
    return <span className="theme-toggle" aria-hidden="true" />;
  }

  const next: Theme = theme === 'dark' ? 'light' : 'dark';

  return (
    <button
      type="button"
      className="theme-toggle"
      aria-label={next === 'light' ? 'Aktifkan mode terang' : 'Aktifkan mode gelap'}
      title={next === 'light' ? 'Mode terang' : 'Mode gelap'}
      onClick={handleToggle}
    >
      {theme === 'dark' ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
    </button>
  );
}
