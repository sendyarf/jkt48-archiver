'use client';

import Image from 'next/image';
import { useCallback, useMemo, useState } from 'react';
import { ExternalLink, Images, Music2, Play, Search } from 'lucide-react';
import VideoPlayer from './VideoPlayer';
import TelegramDownloadButton from './TelegramDownloadButton';
import type { TikTokAccount, TikTokPost } from '@/lib/tiktok';

interface Props {
  accounts: TikTokAccount[];
  initialPosts: TikTokPost[];
  initialTotal: number;
  /** '' = semua akun. */
  initialAccount?: string;
  initialSelectedId?: string;
}

function kindBadge(post: TikTokPost): string {
  if (post.is_story) return post.kind === 'photo' ? 'Story · foto' : 'Story';
  return post.kind === 'photo' ? 'Foto' : 'Video';
}

function KindIcon({ post }: { post: TikTokPost }) {
  if (post.kind === 'photo') return <Images size={14} aria-hidden="true" />;
  if (post.is_story) return <Music2 size={14} aria-hidden="true" />;
  return <Play size={14} aria-hidden="true" />;
}

/**
 * Halaman arsip TikTok: sidebar kiri daftar akun, tengah pemutar, sidebar kanan
 * daftar postingan (tata letak desktop 3 kolom). Di layar kecil susunannya
 * menumpuk menjadi satu kolom lewat CSS.
 *
 * Sumber pemutaran = video YouTube (unlisted). Postingan FOTO tetap bisa diunduh
 * sebagai foto lewat bot Telegram (deep-link `tt_<post_id>`).
 */
export default function TikTokArchive({
  accounts,
  initialPosts,
  initialTotal,
  initialAccount = '',
  initialSelectedId = '',
}: Props) {
  const [account, setAccount] = useState(initialAccount);
  const [posts, setPosts] = useState(initialPosts);
  const [total, setTotal] = useState(initialTotal);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(initialSelectedId || initialPosts[0]?.id || '');
  const [keyword, setKeyword] = useState('');
  const [accountKeyword, setAccountKeyword] = useState('');

  // Arsip pertama yang dipilih saat daftar berganti = yang terbaru DAN bisa
  // diputar (punya video YouTube), supaya pengunjung tidak disambut placeholder
  // "belum siap" padahal masih ada yang bisa ditonton.
  const firstSelectableId = useCallback(
    (list: TikTokPost[]) =>
      (list.find((p) => p.youtube_video_id) || list[0])?.id || '',
    []
  );

  const visibleAccounts = useMemo(() => {
    const q = accountKeyword.trim().toLowerCase();
    if (!q) return accounts;
    return accounts.filter(
      (a) =>
        a.display_name.toLowerCase().includes(q) ||
        a.unique_id.toLowerCase().includes(q)
    );
  }, [accounts, accountKeyword]);

  const selected = useMemo(() => {
    if (!posts.length) return null;
    return posts.find((p) => p.id === selectedId) || posts[0];
  }, [posts, selectedId]);

  const visiblePosts = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    if (!q) return posts;
    return posts.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        p.account.toLowerCase().includes(q) ||
        kindBadge(p).toLowerCase().includes(q)
    );
  }, [posts, keyword]);

  const totalArchived = useMemo(
    () => accounts.reduce((sum, a) => sum + a.post_count, 0),
    [accounts]
  );

  const accountName = useMemo(
    () => accounts.find((a) => a.unique_id === account)?.display_name || '',
    [accounts, account]
  );

  const pickAccount = useCallback(
    async (uniqueId: string) => {
      if (uniqueId === account) return;
      setLoading(true);
      setError('');
      try {
        const res = await fetch(
          `/api/tiktok/posts?account=${encodeURIComponent(uniqueId)}&limit=120`,
          { cache: 'no-store' }
        );
        const data = await res.json();
        if (!data?.success) throw new Error(data?.message || 'gagal');
        setAccount(uniqueId);
        setPosts(data.posts || []);
        setTotal(Number(data.total || 0));
        setSelectedId(firstSelectableId(data.posts || []));
        setKeyword('');
      } catch {
        setError('Gagal memuat arsip akun ini. Coba lagi ya.');
      } finally {
        setLoading(false);
      }
    },
    [account, firstSelectableId]
  );

  return (
    <div className="tiktok-shell">
      {/* ── Sidebar kiri: daftar akun ─────────────────────────────────────── */}
      <aside className="tiktok-panel tiktok-accounts" aria-label="Akun TikTok member">
        <h2 className="tiktok-panel-title">
          Akun <span className="tiktok-count">{accounts.length}</span>
        </h2>
        <label className="tiktok-search input-with-icon">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={accountKeyword}
            onChange={(e) => setAccountKeyword(e.target.value)}
            placeholder="Cari member"
            aria-label="Cari akun member"
          />
        </label>
        <div className="tiktok-account-list">
          <button
            type="button"
            className={`tiktok-account-item${account === '' ? ' active' : ''}`}
            aria-pressed={account === ''}
            onClick={() => pickAccount('')}
          >
            <span className="tiktok-account-name">Semua akun</span>
            <span className="tiktok-count">{totalArchived}</span>
          </button>
          {visibleAccounts.length === 0 && (
            <p className="help-text">Tidak ada akun yang cocok. Coba kata kunci lain ya.</p>
          )}
          {visibleAccounts.map((acc) => (
            <button
              key={acc.unique_id}
              type="button"
              className={`tiktok-account-item${account === acc.unique_id ? ' active' : ''}`}
              aria-pressed={account === acc.unique_id}
              onClick={() => pickAccount(acc.unique_id)}
            >
              {acc.avatar_url ? (
                <span className="tiktok-account-avatar">
                  <Image
                    src={acc.avatar_url}
                    alt=""
                    width={30}
                    height={30}
                    sizes="30px"
                  />
                </span>
              ) : (
                <span className="tiktok-account-initial" aria-hidden="true">
                  {acc.display_name.trim().charAt(0).toUpperCase() || '?'}
                </span>
              )}
              <span className="tiktok-account-meta">
                <span className="tiktok-account-name">{acc.display_name}</span>
                <span className="tiktok-account-handle">@{acc.unique_id}</span>
              </span>
              <span className="tiktok-count">{acc.post_count}</span>
            </button>
          ))}
        </div>
      </aside>

      {/* ── Tengah: pemutar + detail ──────────────────────────────────────── */}
      <div className="tiktok-stage">
        {error && (
          <p className="notice" role="status">
            {error}
          </p>
        )}
        {loading && <p className="help-text">Memuat arsip akun…</p>}

        {selected ? (
          <>
            <div id="tiktok-player-container">
              {selected.youtube_video_id ? (
                <VideoPlayer
                  youtubeId={selected.youtube_video_id}
                  title={selected.display_title}
                  poster={selected.thumbnail_url}
                  platform="idn"
                />
              ) : (
                <div className="tiktok-player-placeholder">
                  <KindIcon post={selected} />
                  <p>
                    Video YouTube-nya belum siap, tapi medianya sudah aman di arsip.
                    {selected.telegram_archived
                      ? ' Ambil lewat bot Telegram di bawah ya.'
                      : ' Coba cek lagi sebentar lagi.'}
                  </p>
                </div>
              )}
            </div>

            <div className="tiktok-details">
              <h2 className="tiktok-details-title">{selected.display_title}</h2>
              <div className="tiktok-details-meta">
                <span className={`tiktok-badge ${selected.kind}`}>
                  <KindIcon post={selected} /> {kindBadge(selected)}
                </span>
                <span className="tiktok-badge">@{selected.account}</span>
                {selected.date_display && (
                  <span className="tiktok-badge">{selected.date_display}</span>
                )}
                {selected.duration_formatted && (
                  <span className="tiktok-badge">{selected.duration_formatted}</span>
                )}
              </div>

              {selected.title && <p className="tiktok-caption">{selected.title}</p>}

              <div className="tiktok-actions">
                {selected.telegram_archived && (
                  <TelegramDownloadButton
                    youtubeVideoId={selected.youtube_video_id}
                    payload={selected.download_payload}
                    media={selected.kind}
                    title={selected.display_title}
                  />
                )}
                {selected.source_url && (
                  <a
                    className="text-button"
                    href={selected.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink size={14} aria-hidden="true" /> Buka di TikTok
                  </a>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="empty-state tiktok-empty">
            <Images size={22} aria-hidden="true" />
            <h2>Belum ada arsip TikTok yang tayang</h2>
            <p>Arsipnya masih diproses bot (unduh + upload). Coba cek lagi sebentar lagi ya.</p>
          </div>
        )}
      </div>


      {/* ── Sidebar kanan: daftar postingan ───────────────────────────────── */}
      <aside className="tiktok-panel tiktok-posts" aria-label="Daftar arsip TikTok">
        <h2 className="tiktok-panel-title">
          {account ? accountName || 'Arsip akun' : 'Terbaru dulu'}
          <span className="tiktok-count">{total}</span>
        </h2>

        <label className="tiktok-search input-with-icon">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="Cari di daftar ini"
            aria-label="Cari arsip TikTok di daftar ini"
          />
        </label>

        {visiblePosts.length === 0 ? (
          <p className="help-text">
            {posts.length === 0
              ? account
                ? 'Akun ini belum punya arsip yang tayang.'
                : 'Belum ada arsip TikTok yang tayang. Cek lagi nanti ya.'
              : 'Belum ada arsip yang cocok. Coba kata kunci lain ya.'}
          </p>
        ) : (
          <div className="tiktok-post-list">
            {visiblePosts.map((post) => (
              <button
                key={post.id}
                type="button"
                className={`tiktok-post-item${selected?.id === post.id ? ' active' : ''}`}
                aria-pressed={selected?.id === post.id}
                onClick={() => setSelectedId(post.id)}
              >
                <span className="tiktok-post-thumb">
                  {post.thumbnail_url ? (
                    <Image
                      src={post.thumbnail_url}
                      alt=""
                      fill
                      sizes="(max-width: 1100px) 30vw, 128px"
                      className="tiktok-post-img"
                    />
                  ) : (
                    <span className="tiktok-post-fallback" aria-hidden="true">
                      <KindIcon post={post} />
                    </span>
                  )}
                </span>
                <span className="tiktok-post-info">
                  <span className="tiktok-post-title">
                    {post.title?.trim() || post.display_title}
                  </span>
                  <span className="tiktok-post-meta">
                    <KindIcon post={post} /> {kindBadge(post)}
                    {post.date_display ? ` · ${post.date_display}` : ''}
                    {post.kind === 'photo' && post.image_count
                      ? ` · ${post.image_count} foto`
                      : ''}
                    {!post.youtube_video_id && (
                      <span className="tiktok-badge-dl">Unduh via bot</span>
                    )}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}

