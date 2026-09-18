# Laporan Problems (ESLint + TypeScript) — c:\Sendy\jkt48-live\web

## Status akhir
- `npx eslint .` → exit 0, **0 temuan** (sebelumnya 39: 25 error, 14 warning di 8 file).
- `npx tsc --noEmit` → exit 0, 0 error.
- `npm run build` → sukses.

## Cara memeriksa ulang (salin apa adanya)
```powershell
cd c:\Sendy\jkt48-live\web
npx eslint .            # 0 temuan
npx tsc --noEmit        # 0 error
npm run build           # sukses
```
Panel Problems VS Code = gabungan ESLint + TypeScript, jadi hasilnya identik dengan perintah di atas.

## Daftar temuan awal dan penyelesaiannya

### app/watch/[id]/page.tsx — 6 warning
- `Share2`, `Sparkles`, `User`, `Video` tidak terpakai → impor dihapus (4).
- 2× `no-img-element` (thumbnail/avatar pihak ketiga) → aturan dinonaktifkan dengan alasan, lihat bawah.

### components/VideoCard.tsx — 3 warning
- `User` tidak terpakai → impor dihapus.
- 2× `no-img-element` → sama seperti di atas.

### components/VideoPlayer.tsx — 4 error + 1 warning
- 3× `react-hooks/set-state-in-effect`:
  - `setMounted(true)` + baca localStorage → diganti `useSyncExternalStore` (snapshot server `fit`, snapshot klien dari localStorage) sehingga hidrasi tetap konsisten tanpa setState di effect.
  - reset `currentTime`/`isReady` saat `youtubeId` berubah → pola resmi React "menyesuaikan state saat prop berubah" di dalam render.
  - `useEffect(() => wakeControls(), [isPaused])` → dihapus; timer auto-hide kini memeriksa `playerRef.current?.paused` sehingga tidak lagi bergantung pada state basi, dan `wakeControls` dipanggil dari `onPlay`/`onPause`.
- `@typescript-eslint/no-explicit-any` pada `style={... as any}` → tipe `PlayerStyle` (CSSProperties + variabel CSS) tanpa `any`.
- 1 warning `no-img-element` → alasan sama.

### lib/db.ts — 10 error + 1 warning
- `require('fs')` → `import { existsSync } from 'node:fs'`.
- `formatDuration` tidak terpakai → dihapus.
- 9× `any` → diganti tipe eksplisit: `VideoRow`, `VideoDetailRow`, `StreamerRow`/`Streamer`, `ActiveSessionRow`/`ActiveSession`, `YouTubeChannelRow`/`YouTubeChannelStat`, `SystemStats`; cast memakai `as unknown as ...`. Kolom SQL nullable (mis. `MIN(started_at)`) ditangani eksplisit (`|| ''`, `|| undefined`).

### types/node-sqlite.d.ts — 6 error
- `any` pada API `node:sqlite` → `unknown` dan tipe opsi konkret. Deklarasi ini hanya shim untuk modul eksperimental Node.

### verify/*.cjs — 6 error + 3 warning
- `no-require-imports` dan `no-unused-expressions` pada skrip otomasi CommonJS → override ESLint khusus `verify/**` (skrip Node biasa, bukan kode aplikasi). Logika tes tidak diubah.

## Dua aturan yang sengaja dinonaktifkan (dengan alasan)

1. `@next/next/no-img-element` untuk `app/**` dan `components/**`.
   `<img>` memang disengaja: thumbnail berasal dari `img.youtube.com` dan avatar dari `ui-avatars.com`, dengan rantai fallback `onError`. Optimasi `next/image` memerlukan `sharp` yang belum terpasang, sehingga migrasi sekarang berisiko membuat gambar gagal render di produksi.

2. `no-require-imports` / `no-unused-expressions` untuk `verify/**`.
   Skrip verifikasi dijalankan langsung dengan `node`, bukan bundel Next.js, jadi `require` sah di sana.

Keduanya terlihat di `eslint.config.mjs` lengkap dengan komentar alasannya.

## Arsip temuan awal (semuanya sudah diperbaiki di atas)

- [WARN ] baris 6:34  @typescript-eslint/no-unused-vars
    'Share2' is defined but never used.
- [WARN ] baris 6:42  @typescript-eslint/no-unused-vars
    'Sparkles' is defined but never used.
- [WARN ] baris 6:52  @typescript-eslint/no-unused-vars
    'User' is defined but never used.
- [WARN ] baris 6:58  @typescript-eslint/no-unused-vars
    'Video' is defined but never used.
- [WARN ] baris 100:17  @next/next/no-img-element
    Using `<img>` could result in slower LCP and higher bandwidth. Consider using `<Image />` from `next/image` or a custom image loader to automatically optimize images. This may incur additional usage or cost from your provider. See: https://nextjs.org/docs/messages/no-img-element
- [WARN ] baris 151:21  @next/next/no-img-element
    Using `<img>` could result in slower LCP and higher bandwidth. Consider using `<Image />` from `next/image` or a custom image loader to automatically optimize images. This may incur additional usage or cost from your provider. See: https://nextjs.org/docs/messages/no-img-element

## C:\Sendy\jkt48-live\web\components\VideoCard.tsx

- [WARN ] baris 4:26  @typescript-eslint/no-unused-vars
    'User' is defined but never used.
- [WARN ] baris 30:9  @next/next/no-img-element
    Using `<img>` could result in slower LCP and higher bandwidth. Consider using `<Image />` from `next/image` or a custom image loader to automatically optimize images. This may incur additional usage or cost from your provider. See: https://nextjs.org/docs/messages/no-img-element
- [WARN ] baris 60:11  @next/next/no-img-element
    Using `<img>` could result in slower LCP and higher bandwidth. Consider using `<Image />` from `next/image` or a custom image loader to automatically optimize images. This may incur additional usage or cost from your provider. See: https://nextjs.org/docs/messages/no-img-element

## C:\Sendy\jkt48-live\web\components\VideoPlayer.tsx

- [ERROR] baris 133:5  react-hooks/set-state-in-effect
    Error: Calling setState synchronously within an effect can trigger cascading renders  Effects are intended to synchronize state between React and external systems such as manually updating the DOM, state management libraries, or other platform APIs. In general, the body of an effect should do one or both of the following: * Update external systems with the latest state from React. * Subscribe for updates from some external system, calling setState in a callback function when external state changes.  Calling setState synchronously within an effect body causes cascading renders that can hurt performance, and is not recommended. (https://react.dev/learn/you-might-not-need-an-effect).  C:\Sendy\jkt48-live\web\components\VideoPlayer.tsx:133:5   131 |   132 |   useEffect(() => { > 133 |     setMounted(true);       |     ^^^^^^^^^^ Avoid calling setState() directly within an effect   134 |     try {   135 |       const savedFit = localStorage.getItem('jkt48_player_fit_mode') as FitMode | null;   136 |       if (savedFit && ['fit', 'fill', 'standard'].includes(savedFit)) {
- [ERROR] baris 146:5  react-hooks/set-state-in-effect
    Error: Calling setState synchronously within an effect can trigger cascading renders  Effects are intended to synchronize state between React and external systems such as manually updating the DOM, state management libraries, or other platform APIs. In general, the body of an effect should do one or both of the following: * Update external systems with the latest state from React. * Subscribe for updates from some external system, calling setState in a callback function when external state changes.  Calling setState synchronously within an effect body causes cascading renders that can hurt performance, and is not recommended. (https://react.dev/learn/you-might-not-need-an-effect).  C:\Sendy\jkt48-live\web\components\VideoPlayer.tsx:146:5   144 |   useEffect(() => {   145 |     if (!mounted) return; > 146 |     setCurrentTime(0);       |     ^^^^^^^^^^^^^^ Avoid calling setState() directly within an effect   147 |     setIsReady(false);   148 |   }, [youtubeId, mounted]);   149 |
- [ERROR] baris 168:5  react-hooks/set-state-in-effect
    Error: Calling setState synchronously within an effect can trigger cascading renders  Effects are intended to synchronize state between React and external systems such as manually updating the DOM, state management libraries, or other platform APIs. In general, the body of an effect should do one or both of the following: * Update external systems with the latest state from React. * Subscribe for updates from some external system, calling setState in a callback function when external state changes.  Calling setState synchronously within an effect body causes cascading renders that can hurt performance, and is not recommended. (https://react.dev/learn/you-might-not-need-an-effect).  C:\Sendy\jkt48-live\web\components\VideoPlayer.tsx:168:5   166 |   167 |   useEffect(() => { > 168 |     wakeControls();       |     ^^^^^^^^^^^^ Avoid calling setState() directly within an effect   169 |   }, [isPaused, wakeControls]);   170 |   171 |   const handleTapScreen = useCallback((e: React.MouseEvent) => {
- [WARN ] baris 308:13  @next/next/no-img-element
    Using `<img>` could result in slower LCP and higher bandwidth. Consider using `<Image />` from `next/image` or a custom image loader to automatically optimize images. This may incur additional usage or cost from your provider. See: https://nextjs.org/docs/messages/no-img-element
- [ERROR] baris 383:38  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.

## C:\Sendy\jkt48-live\web\lib\db.ts

- [ERROR] baris 18:20  @typescript-eslint/no-require-imports
    A `require()` style import is forbidden.
- [WARN ] baris 73:10  @typescript-eslint/no-unused-vars
    'formatDuration' is defined but never used.
- [ERROR] baris 131:17  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 160:58  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 211:76  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 281:37  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 298:30  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 312:35  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 321:46  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 345:19  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 350:34  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.

## C:\Sendy\jkt48-live\web\types\node-sqlite.d.ts

- [ERROR] baris 3:45  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 9:20  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 9:28  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 10:20  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 10:28  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.
- [ERROR] baris 11:20  @typescript-eslint/no-explicit-any
    Unexpected any. Specify a different type.

## C:\Sendy\jkt48-live\web\verify\navbar-debug.cjs

- [WARN ] baris 7:124  @typescript-eslint/no-unused-expressions
    Expected an assignment or function call and instead saw an expression.

## C:\Sendy\jkt48-live\web\verify\theater-check.cjs

- [ERROR] baris 1:16  @typescript-eslint/no-require-imports
    A `require()` style import is forbidden.
- [ERROR] baris 2:12  @typescript-eslint/no-require-imports
    A `require()` style import is forbidden.
- [ERROR] baris 3:14  @typescript-eslint/no-require-imports
    A `require()` style import is forbidden.
- [WARN ] baris 16:5  @typescript-eslint/no-unused-expressions
    Expected an assignment or function call and instead saw an expression.

## C:\Sendy\jkt48-live\web\verify\volume-check.cjs

- [ERROR] baris 1:12  @typescript-eslint/no-require-imports
    A `require()` style import is forbidden.
- [WARN ] baris 9:134  @typescript-eslint/no-unused-expressions
    Expected an assignment or function call and instead saw an expression.
- [ERROR] baris 54:22  @typescript-eslint/no-require-imports
    A `require()` style import is forbidden.

