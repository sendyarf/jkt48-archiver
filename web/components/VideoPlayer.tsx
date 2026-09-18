'use client';

import React, { useRef, useState, useEffect, useCallback, useId, useSyncExternalStore } from 'react';
import {
  MediaPlayer,
  MediaProvider,
  type MediaPlayerInstance
} from '@vidstack/react';
import '@vidstack/react/player/styles/default/theme.css';

export interface VideoPlayerProps {
  youtubeId: string;
  title: string;
  poster?: string;
  platform?: 'idn' | 'showroom' | string;
  /**
   * Sumber media langsung (mis. URL .mp4 atau .m3u8) untuk ujicoba.
   * Bila diisi, nilai ini dipakai menggantikan sumber YouTube.
   * Kosongkan (default) agar perilaku IDN/YouTube tidak berubah sama sekali.
   */
  directSrc?: string;
}

type FitMode = 'fit' | 'fill' | 'standard';

// Fit preference lives outside React so hydration stays consistent: the server
// snapshot is 'fit' until the browser snapshot (localStorage) is read.
const FIT_MODES = ['fit', 'fill', 'standard'] as const;
const fitModeListeners = new Set<() => void>();
const noopSubscribe = () => () => {};
const getClientMounted = () => true;
const getServerMounted = () => false;
const getServerFitMode = (): FitMode => 'fit';

function getFitModeSnapshot(): FitMode {
  try {
    const saved = localStorage.getItem('jkt48_player_fit_mode');
    return saved && (FIT_MODES as readonly string[]).includes(saved) ? (saved as FitMode) : 'fit';
  } catch {
    return 'fit';
  }
}

function subscribeFitMode(listener: () => void) {
  fitModeListeners.add(listener);
  return () => {
    fitModeListeners.delete(listener);
  };
}

function persistFitMode(mode: FitMode) {
  try {
    localStorage.setItem('jkt48_player_fit_mode', mode);
  } catch {
    // ignore
  }
  fitModeListeners.forEach(listener => listener());
}

type PlayerStyle = React.CSSProperties & { [key: `--${string}`]: string | number | null | undefined };

export default function VideoPlayer({
  youtubeId,
  title,
  poster,
  platform = 'idn',
  directSrc,
}: VideoPlayerProps) {
  const isVertical = platform === 'idn' || platform === 'idn_live';
  // Landscape (Showroom) memakai fullscreen ELEMEN — rasio 16:9 mengisi layar
  // secara natural. Vertikal (IDN) tetap memakai "theater" satu halaman karena
  // video 9:16 butuh tata letak khusus agar tidak menyisakan ruang kosong besar.
  const isElementFullscreen = !isVertical;
  const playerRef = useRef<MediaPlayerInstance>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const [isTheater, setIsTheater] = useState(false);
  const theaterActiveRef = useRef(false);
  const ownsFullscreenRef = useRef(false);
  const theaterButtonRef = useRef<HTMLButtonElement>(null);
  const theaterShellRef = useRef<HTMLDivElement>(null);

  const exitTheater = useCallback(() => {
    theaterActiveRef.current = false;
    setIsTheater(false);
    if (ownsFullscreenRef.current && document.fullscreenElement === document.documentElement) {
      document.exitFullscreen().catch(() => {});
    }
    ownsFullscreenRef.current = false;
    theaterButtonRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    if (!isTheater || isElementFullscreen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const background: { element: HTMLElement; inert: boolean }[] = [];
    let branch: HTMLElement | null = theaterShellRef.current;
    while (branch?.parentElement) {
      for (const sibling of Array.from(branch.parentElement.children)) {
        if (sibling !== branch && sibling instanceof HTMLElement) {
          background.push({ element: sibling, inert: sibling.inert });
          sibling.inert = true;
        }
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault();
        exitTheater();
      }
      if (event.key === 'Tab') {
        const controls = Array.from(wrapperRef.current?.querySelectorAll<HTMLElement>('button, input') || [])
          .filter(element => !element.hasAttribute('disabled') && element.getClientRects().length);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    const onFullscreenChange = () => {
      if (ownsFullscreenRef.current && !document.fullscreenElement) exitTheater();
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    theaterButtonRef.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = previousOverflow;
      background.forEach(({ element, inert }) => { element.inert = inert; });
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('fullscreenchange', onFullscreenChange);
      theaterActiveRef.current = false;
      if (ownsFullscreenRef.current && document.fullscreenElement === document.documentElement) {
        document.exitFullscreen().catch(() => {});
      }
      ownsFullscreenRef.current = false;
    };
  }, [isTheater, exitTheater, isElementFullscreen]);

  const mounted = useSyncExternalStore(noopSubscribe, getClientMounted, getServerMounted);
  const fitMode = useSyncExternalStore(subscribeFitMode, getFitModeSnapshot, getServerFitMode);

  // Sinkronkan status tombol dengan fullscreen elemen (Escape/native ditangani browser).
  const wasElementFullscreenRef = useRef(false);
  useEffect(() => {
    if (!isElementFullscreen || !mounted) return;
    const element = theaterShellRef.current;
    if (!element) return;
    const sync = () => {
      const active = document.fullscreenElement === element;
      setIsTheater(active);
      if (wasElementFullscreenRef.current && !active) {
        theaterButtonRef.current?.focus({ preventScroll: true });
      }
      wasElementFullscreenRef.current = active;
    };
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, [isElementFullscreen, mounted]);
  const [isPaused, setIsPaused] = useState(false);
  // Autoplay dimulai senyap (kebijakan browser mobile) → status awal muted.
  const [isMuted, setIsMuted] = useState(true);
  const [volume, setVolume] = useState(0.85);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isReady, setIsReady] = useState(false);
  const [showCenterIcon, setShowCenterIcon] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [volumeOpen, setVolumeOpen] = useState(false);
  const volumeGroupRef = useRef<HTMLDivElement>(null);
  const volumeButtonRef = useRef<HTMLButtonElement>(null);
  const volumePanelId = useId();

  useEffect(() => {
    if (!volumeOpen) return;
    const closeOnOutside = (event: PointerEvent) => {
      if (!volumeGroupRef.current?.contains(event.target as Node)) {
        setVolumeOpen(false);
      }
    };
    document.addEventListener('pointerdown', closeOnOutside);
    return () => document.removeEventListener('pointerdown', closeOnOutside);
  }, [volumeOpen]);

  const iconTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const controlsTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [toastMsg, setToastMsg] = useState('');
  const [showToast, setShowToast] = useState(false);

  // Reset transient playback state when the player is pointed at another video.
  const [loadedVideoId, setLoadedVideoId] = useState(youtubeId);
  if (loadedVideoId !== youtubeId) {
    setLoadedVideoId(youtubeId);
    setCurrentTime(0);
    setIsReady(false);
  }

  const showToastNotification = (msg: string) => {
    setToastMsg(msg);
    setShowToast(true);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setShowToast(false), 2200);
  };

  const wakeControls = useCallback(() => {
    setControlsVisible(true);
    clearTimeout(controlsTimer.current);
    controlsTimer.current = setTimeout(() => {
      if (!playerRef.current?.paused) {
        setControlsVisible(false);
      }
    }, 3500);
  }, []);

  const handleTapScreen = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (
      target.closest('.player-controls-bottom') ||
      target.closest('button') ||
      target.closest('input')
    ) {
      return;
    }

    // Tap layar TIDAK mengubah suara — unmute hanya lewat tombol khusus.
    if (!playerRef.current) return;
    if (playerRef.current.paused) {
      playerRef.current.play().catch(() => {});
      setIsPaused(false);
    } else {
      playerRef.current.pause();
      setIsPaused(true);
    }
    setShowCenterIcon(true);
    clearTimeout(iconTimer.current);
    iconTimer.current = setTimeout(() => setShowCenterIcon(false), 700);
    wakeControls();
  }, [wakeControls]);

  const togglePlayPause = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (!playerRef.current) return;
    if (playerRef.current.paused) {
      // Optimistis: ikon langsung ganti agar tap terasa responsif.
      setIsPaused(false);
      playerRef.current.play().catch(() => {
        // Gagal mulai (mis. diblokir browser) → kembalikan ikon.
        setIsPaused(true);
        showToastNotification('Ketuk sekali lagi untuk memutar.');
      });
    } else {
      setIsPaused(true);
      playerRef.current.pause();
    }
    wakeControls();
  };

  // Tombol unmute khusus: autoplay dimulai senyap (kebijakan Chrome/Brave
  // mobile), suara hanya menyala lewat tombol ini — bukan tap layar/play.
  const handleUnmute = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!playerRef.current) return;
    playerRef.current.muted = false;
    setIsMuted(false);
    if (playerRef.current.paused) {
      setIsPaused(false);
      playerRef.current.play().catch(() => {
        setIsPaused(true);
        showToastNotification('Ketuk sekali lagi untuk memutar.');
      });
    }
    wakeControls();
  };

  const toggleMute = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (!playerRef.current) return;
    const nextMuted = !isMuted;
    playerRef.current.muted = nextMuted;
    setIsMuted(nextMuted);
    if (!nextMuted) {
      // Unmute dari tombol = juga gestur; pastikan pemutaran lanjut.
      if (playerRef.current.paused) playerRef.current.play().catch(() => {});
    }
    wakeControls();
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVol = parseFloat(e.target.value);
    setVolume(newVol);
    if (playerRef.current) {
      playerRef.current.volume = newVol;
      if (newVol === 0) {
        playerRef.current.muted = true;
        setIsMuted(true);
      } else if (isMuted) {
        playerRef.current.muted = false;
        setIsMuted(false);
      }
    }
    wakeControls();
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const seekTo = parseFloat(e.target.value);
    setCurrentTime(seekTo);
    if (playerRef.current) {
      playerRef.current.currentTime = seekTo;
    }
    wakeControls();
  };

  const toggleTheater = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setVolumeOpen(false);
    wakeControls();

    // Landscape/Showroom → fullscreen elemen. 16:9 mengisi layar; Escape dan
    // tombol keluar milik browser dipakai apa adanya, state disinkronkan lewat
    // event fullscreenchange.
    if (isElementFullscreen) {
      const element = theaterShellRef.current;
      if (!element) return;
      try {
        if (document.fullscreenElement === element) {
          await document.exitFullscreen();
        } else {
          if (document.fullscreenElement) await document.exitFullscreen();
          await element.requestFullscreen();
        }
      } catch {
        showToastNotification('Browser menolak fullscreen. Gunakan F11.');
      }
      return;
    }

    if (isTheater) {
      exitTheater();
      return;
    }
    theaterActiveRef.current = true;
    setIsTheater(true);
    const isDesktop = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches;
    if (isDesktop && !isStandalone && !document.fullscreenElement && document.fullscreenEnabled) {
      try {
        await document.documentElement.requestFullscreen();
        if (theaterActiveRef.current) {
          ownsFullscreenRef.current = true;
        } else if (document.fullscreenElement === document.documentElement) {
          await document.exitFullscreen();
        }
      } catch {
        // Keep the viewport theater layout if browser fullscreen is denied.
        if (theaterActiveRef.current) showToastNotification('Mode Theater aktif. Gunakan F11 untuk fullscreen browser.');
      }
    }
  };

  const toggleFitMode = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    let nextMode: FitMode = 'fit';
    let label = '';
    if (fitMode === 'fit') {
      nextMode = 'fill';
      label = 'Mode: Isi Penuh (9:16)';
    } else if (fitMode === 'fill') {
      nextMode = 'standard';
      label = 'Mode: 9:16 Standar';
    } else {
      nextMode = 'fit';
      label = 'Mode: Pas Video (Tanpa Black Box)';
    }
    persistFitMode(nextMode);
    setVolumeOpen(false);
    showToastNotification(label);
    wakeControls();
  };

  const formatTime = (secs: number) => {
    if (isNaN(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const effectivePoster = poster || (directSrc ? '' : `https://i.ytimg.com/vi/${youtubeId}/hqdefault.jpg`);
  const mediaSrc = directSrc || `https://www.youtube.com/watch?v=${youtubeId}&controls=0&modestbranding=1&rel=0`;

  if (!mounted) {
    return (
      <div className={`video-player-wrapper ${isVertical ? 'vertical-player' : 'horizontal-player'}`}>
        <div className="player-inner-container skeleton-player">
          {poster && (
            <img src={poster} alt={title} className="skeleton-player-img" />
          )}
          <div className="player-loading-spinner">
            <div className="spinner-ring" />
            <span>Menyiapkan pemutar video...</span>
          </div>
        </div>
      </div>
    );
  }

  // Dynamic styles for the container based on fitMode
  const getContainerStyle = (): React.CSSProperties => {
    if (!isVertical) return {};
    if (fitMode === 'fit') {
      return {
        aspectRatio: '3 / 4',
        maxWidth: 'calc(min(84vh, 760px) * (3 / 4))',
      };
    }
    // fill or standard
    return {
      aspectRatio: '9 / 16',
      maxWidth: 'calc(min(84vh, 800px) * (9 / 16))',
    };
  };

  // Dynamic styles for media-player based on fitMode
  const getPlayerStyle = (): React.CSSProperties => {
    const base: React.CSSProperties = {
      width: '100%',
      height: '100%',
      pointerEvents: 'none',
      transition: 'transform 0.25s ease',
    };
    if (isVertical && fitMode === 'fill') {
      return {
        ...base,
        transform: 'scale(1.334)',
        transformOrigin: 'center center',
      };
    }
    return base;
  };

  const effectiveRatio = isVertical ? (fitMode === 'fit' ? '3/4' : '9/16') : '16/9';

  return (
    <div
      ref={theaterShellRef}
      className={`video-player-wrapper ${isVertical ? 'vertical-player' : 'horizontal-player'}${isTheater && !isElementFullscreen ? ' theater-mode' : ''}`}
      role={isTheater && !isElementFullscreen ? 'dialog' : undefined}
      aria-modal={isTheater && !isElementFullscreen ? true : undefined}
      aria-label={isTheater ? (isElementFullscreen ? `Fullscreen: ${title}` : `Mode Theater: ${title}`) : undefined}
      style={{ '--theater-ratio': isVertical ? (fitMode === 'fit' ? 3 / 4 : 9 / 16) : 16 / 9 } as React.CSSProperties}
    >
      <div
        ref={wrapperRef}
        className={`player-inner-container ${isVertical ? `vertical-player-container ratio-${fitMode}` : 'horizontal-player-container'}`}
        style={getContainerStyle()}
        onClick={handleTapScreen}
        // onMouseMove hanya terpicu di desktop (pointer: fine). onPointerMove
        // mencakup keduanya — mouse di desktop dan jari di mobile — sehingga
        // wakeControls juga terpanggil di layar sentuh.
        onPointerMove={wakeControls}
        // Satu sentuhan (tanpa geser) sudah cukup memunculkan kontrol;
        // onPointerMove tidak terpicu bila jari tidak bergerak.
        onTouchStart={wakeControls}
      >
        {/* Vidstack Media Player */}
        {/* muted + autoplay: Chrome/Brave mobile menolak autoplay dengan suara,
            dan penolakan itu bisa membuat player macet di status "hang" sehingga
            tombol play terasa mati. Mulai senyap lalu unmute hanya ketika user
            berinteraksi (gestur) = pola yang diterima semua browser mobile. */}
        <MediaPlayer
          ref={playerRef}
          title={title}
          src={mediaSrc}
          poster={effectivePoster}
          aspectRatio={effectiveRatio}
          autoplay
          muted
          playsInline
          style={getPlayerStyle() as PlayerStyle}
          onCanPlay={() => setIsReady(true)}
          onPlay={() => { setIsPaused(false); wakeControls(); }}
          onPause={() => { setIsPaused(true); wakeControls(); }}
          onTimeUpdate={(detail) => setCurrentTime(detail.currentTime)}
          onDurationChange={(detail) => setDuration(detail)}
        >
          <MediaProvider />
        </MediaPlayer>

        {/* Loading Spinner Indicator */}
        {!isReady && (
          <div className="player-loading-spinner">
            <div className="spinner-ring" />
            <span>Memuat video...</span>
          </div>
        )}

        {/* Center Tap Play/Pause Icon Pop */}
        {showCenterIcon && (
          <div className="center-tap-icon animate-pop">
            {isPaused ? '▶' : '⏸'}
          </div>
        )}

        {/* Tombol unmute khusus — tampil selama video masih senyap. */}
        {isMuted && (
          <button
            type="button"
            className="unmute-pill"
            onClick={handleUnmute}
            aria-label="Nyalakan suara"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" />
              <line x1="23" y1="9" x2="17" y2="15" />
              <line x1="17" y1="9" x2="23" y2="15" />
            </svg>
            <span>Nyalakan suara</span>
          </button>
        )}

        {/* Bottom Floating Control Bar */}
        <div className={`player-controls-bottom ${controlsVisible || isPaused || volumeOpen ? 'visible' : ''}`}>
          {/* Scrubber Progress Slider */}
          <div className="scrubber-row">
            <input
              type="range"
              min={0}
              max={duration || 100}
              step={0.1}
              value={currentTime}
              onChange={handleSeek}
              className="video-seek-slider"
              style={{
                background: `linear-gradient(to right, #f43f5e ${(currentTime / (duration || 1)) * 100}%, rgba(255,255,255,0.2) ${(currentTime / (duration || 1)) * 100}%)`
              }}
              aria-label="Posisi Video"
            />
            <div className="time-display">
              <span>{formatTime(currentTime)}</span>
              <span>/</span>
              <span>{formatTime(duration)}</span>
            </div>
          </div>

          {/* Control Buttons Row */}
          <div className="buttons-row">
            <div className="left-controls">
              <button
                type="button"
                className="ctrl-btn primary-ctrl"
                onClick={togglePlayPause}
                title={isPaused ? 'Putar' : 'Jeda'}
                aria-label="Putar atau Jeda"
              >
                {isPaused ? (
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <polygon points="5 3 19 12 5 21 5 3" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="4" width="4" height="16" />
                    <rect x="14" y="4" width="4" height="16" />
                  </svg>
                )}
              </button>

              <div
                className="volume-control-group"
                ref={volumeGroupRef}
                onBlur={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) setVolumeOpen(false);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Escape' && volumeOpen) {
                    event.preventDefault();
                    event.stopPropagation();
                    setVolumeOpen(false);
                    volumeButtonRef.current?.focus();
                  }
                }}
              >
                <button
                  ref={volumeButtonRef}
                  type="button"
                  className={`ctrl-btn ${volumeOpen ? 'active' : ''}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setVolumeOpen(!volumeOpen);
                    wakeControls();
                  }}
                  title="Atur volume"
                  aria-label={`Atur volume, ${isMuted ? 0 : Math.round(volume * 100)} persen`}
                  aria-expanded={volumeOpen}
                  aria-controls={volumePanelId}
                >
                  {isMuted || volume === 0 ? (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" />
                      <line x1="23" y1="9" x2="17" y2="15" />
                      <line x1="17" y1="9" x2="23" y2="15" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" />
                      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                    </svg>
                  )}
                </button>
                {volumeOpen && (
                  <div id={volumePanelId} className="volume-panel" role="group" aria-label="Pengaturan volume">
                    <button type="button" className="volume-mute-btn" onClick={toggleMute} aria-pressed={isMuted}>
                      {isMuted ? 'Unmute' : 'Mute'}
                    </button>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={isMuted ? 0 : volume}
                      onChange={handleVolumeChange}
                      className="volume-slider"
                      style={{ '--volume-level': `${(isMuted ? 0 : volume) * 100}%` } as React.CSSProperties}
                      aria-label="Volume"
                      aria-valuetext={`${isMuted ? 0 : Math.round(volume * 100)} persen`}
                    />
                    <span className="volume-value">{isMuted ? 0 : Math.round(volume * 100)}%</span>
                  </div>
                )}
              </div>
            </div>

            <div className="right-controls">
              {isVertical && (
                <button
                  type="button"
                  className={`ctrl-btn ratio-btn ${fitMode !== 'standard' ? 'active' : ''}`}
                  onClick={toggleFitMode}
                  title={`Mode Rasio: ${fitMode === 'fit' ? 'Pas Video 3:4 (Tanpa Black Box)' : fitMode === 'fill' ? 'Isi Penuh 9:16' : '9:16 Standar'} (Klik untuk ganti)`}
                  aria-label={`Ganti Mode Rasio, saat ini ${fitMode === 'fit' ? 'FIT' : fitMode === 'fill' ? 'FILL' : '9:16'}`}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <rect x="3" y="4" width="18" height="16" rx="2" />
                    <line x1="8" y1="4" x2="8" y2="20" strokeDasharray="1.5 1.5" />
                    <line x1="16" y1="4" x2="16" y2="20" strokeDasharray="1.5 1.5" />
                  </svg>
                  <span>{fitMode === 'fit' ? 'FIT' : fitMode === 'fill' ? 'FILL' : '9:16'}</span>
                </button>
              )}

              <button
                type="button"
                ref={theaterButtonRef}
                className={`ctrl-btn theater-btn${isTheater ? ' active' : ''}`}
                onClick={toggleTheater}
                title={isElementFullscreen
                  ? (isTheater ? 'Keluar Fullscreen (Esc)' : 'Fullscreen')
                  : (isTheater ? 'Keluar Mode Theater (Esc)' : 'Mode Theater')}
                aria-label={isElementFullscreen
                  ? (isTheater ? 'Keluar Fullscreen' : 'Masuk Fullscreen')
                  : (isTheater ? 'Keluar Mode Theater' : 'Mode Theater')}
                aria-pressed={isTheater}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {isElementFullscreen ? (
                    isTheater
                      ? <path d="M9 3H5a2 2 0 0 0-2 2v4m12-6h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4m12 6h4a2 2 0 0 0 2-2v-4" />
                      : <path d="M3 9V5a2 2 0 0 1 2-2h4m10 6V5a2 2 0 0 0-2-2h-4M3 15v4a2 2 0 0 0 2 2h4m10-6v4a2 2 0 0 1-2 2h-4" />
                  ) : (
                    <>
                      <rect x="2" y="4" width="20" height="16" rx="2" />
                      {isTheater ? <path d="m9 9 6 6m0-6-6 6" /> : <path d="M2 8h20M2 16h20" />}
                    </>
                  )}
                </svg>
              </button>
            </div>
          </div>
        </div>

        {/* Toast Inside Player */}
        <div className={`player-toast ${showToast ? 'show' : ''}`}>
          {toastMsg}
        </div>
      </div>
    </div>
  );
}