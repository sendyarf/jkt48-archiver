'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import Link from 'next/link';
import { MonitorPlay, Search, User } from 'lucide-react';

interface MemberOpt { username: string; display_name: string }

interface Props {
  q: string;
  member: string;
  platform: string;
  members: MemberOpt[];
}

function CatalogFilterFormInner({ q, member, platform, members }: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(q);
  const [prevQ, setPrevQ] = useState(q);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Sinkronkan input saat prop q berubah dari luar (mis. back/forward URL)
  if (q !== prevQ) {
    setPrevQ(q);
    setQuery(q);
  }

  const apply = (next: { q?: string; member?: string; platform?: string }) => {
    const params = new URLSearchParams(searchParams.toString());
    const nq = (next.q ?? q).slice(0, 100);
    const nm = next.member ?? member;
    const np = next.platform ?? platform;
    if (nq) params.set('q', nq); else params.delete('q');
    if (nm) params.set('member', nm); else params.delete('member');
    if (np) params.set('platform', np); else params.delete('platform');
    params.delete('page');
    params.set('_', Date.now().toString(36));
    router.replace(`/?${params.toString()}#catalog`, { scroll: false });
  };

  const onQueryChange = (value: string) => {
    setQuery(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => apply({ q: value }), 350);
  };

  useEffect(() => () => clearTimeout(debounceRef.current), []);

  return (
    <form
      action="/#catalog"
      method="GET"
      className="catalog-filters"
      onSubmit={(e) => {
        e.preventDefault();
        clearTimeout(debounceRef.current);
        apply({ q: query });
      }}
    >
      <label className="catalog-search">
        Cari replay
        <span className="input-with-icon">
          <Search size={18} aria-hidden="true" />
          <input
            name="q"
            type="search"
            id="catalog-search-input"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            maxLength={100}
            placeholder="Judul, member, atau tanggal"
          />
        </span>
      </label>
      <label className="catalog-select">
        Member
        <span className="select-with-icon">
          <User size={18} aria-hidden="true" />
          <select name="member" value={member} onChange={(e) => apply({ member: e.target.value })}>
            <option value="">Semua member</option>
            {member && !members.some((m) => m.username === member) && (
              <option value={member}>{member}</option>
            )}
            {members.map((m) => (
              <option value={m.username} key={m.username}>{m.display_name}</option>
            ))}
          </select>
        </span>
      </label>
      <label className="catalog-select">
        Platform
        <span className="select-with-icon">
          <MonitorPlay size={18} aria-hidden="true" />
          <select name="platform" value={platform} onChange={(e) => apply({ platform: e.target.value })}>
            <option value="">Semua platform</option>
            <option value="idn">IDN Live</option>
            <option value="showroom">Showroom</option>
          </select>
        </span>
      </label>
      <div className="filter-actions">
        <button className="primary-button">Cari</button>
        {(q || member || platform) && (
          <Link
            href="/#catalog"
            className="text-button"
            onClick={(e) => {
              e.preventDefault();
              clearTimeout(debounceRef.current);
              setQuery('');
              router.replace('/#catalog', { scroll: false });
            }}
          >
            Reset
          </Link>
        )}
      </div>
    </form>
  );
}

export default function CatalogFilterForm(props: Props) {
  return (
    <Suspense>
      <CatalogFilterFormInner {...props} />
    </Suspense>
  );
}
