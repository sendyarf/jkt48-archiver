'use client';

import { useEffect, useState } from 'react';

/** Hitung mundur ringkas untuk kartu pra-rilis: "2j 15m" / "45m" / "Sebentar lagi". */
export default function MiniCountdown({ publishAt }: { publishAt: string }) {
  const target = new Date(publishAt).getTime();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const diffMs = target - now;
  if (!publishAt || Number.isNaN(target) || diffMs <= 0) return null;

  const totalMin = Math.floor(diffMs / 60000);
  const hours = Math.floor(totalMin / 60);
  const mins = totalMin % 60;

  if (hours > 0) return <span>{hours}j {mins}m</span>;
  if (mins > 0) return <span>{mins}m</span>;
  return <span>Sebentar lagi</span>;
}
