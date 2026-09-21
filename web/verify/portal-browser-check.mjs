import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
const target = await fetch('http://localhost:9222/json/new?about:blank', { method: 'PUT' }).then(r => r.json());
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map();
ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); if (m.error) p.reject(m.error); else p.resolve(m.result); } });
const send = (method, params = {}) => new Promise((resolve, reject) => { pending.set(++id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async expression => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
const delay = ms => new Promise(r => setTimeout(r, ms));
try {
  await send('Page.enable');
  const widths = [360, 390, 768, 1024, 1440];
  for (const width of widths) {
    const height = width < 768 ? 844 : 900;
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 768 });
    for (const route of ['/', '/members', '/about', '/tiktok', '/admin/status']) {
      await send('Page.navigate', { url: 'http://localhost:3101' + route });
      const expectedPath = route === '/admin/status' ? '/login' : route;
      for (let i = 0; i < 100; i++) { await delay(250); if (await evaluate(`document.readyState === 'complete' && location.pathname === ${JSON.stringify(expectedPath)} && !!document.querySelector('h1')`)) break; }
      await delay(600);
      // Latar hero diambil dari YouTube (maxresdefault) sehingga bisa lambat.
      // Beri waktu tunggu terbatas: uji tetap gagal bila gambar memang tidak
      // pernah termuat, tapi tidak lagi gagal hanya karena jaringan lambat.
      for (let i = 0; i < 20; i++) {
        const heroImgReady = await evaluate(`(() => { const el = document.querySelector('.hero-feature-img'); return !el || (el.complete && el.naturalWidth > 0); })()`);
        if (heroImgReady) break;
        await delay(250);
      }
      const state = await evaluate(`({
        title: document.querySelector('h1')?.textContent,
        path: location.pathname,
        width: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        text: document.body.innerText,
        brand: document.querySelector('.brand-title')?.textContent,
        logo48: !!document.querySelector('.brand-logo, .brand-mark'),
        navToggleVisible: (() => { const el = document.querySelector('.nav-toggle'); if (!el) return null; return getComputedStyle(el).display !== 'none'; })(),
        navMenuDisplay: (() => { const el = document.querySelector('.nav-menu'); if (!el) return null; return getComputedStyle(el).display; })(),
        heroH1: (() => { const el = document.querySelector('.hero-feature h1'); if (!el) return null; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return { text: el.textContent, size: s.fontSize, overflows: r.width > innerWidth + 1 }; })(),
        heroFeature: (() => { const el = document.querySelector('.hero-feature'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width), rectHeight: Math.round(r.height) }; })(),
        heroBody: (() => { const el = document.querySelector('.hero-feature-body'); if (!el) return null; const r = el.getBoundingClientRect(); return { rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        heroImg: (() => { const el = document.querySelector('.hero-feature-img'); if (!el) return null; return { loaded: el.complete && el.naturalWidth > 0, naturalWidth: el.naturalWidth }; })(),
        sectionH2: (() => { const el = document.querySelector('.section-heading h2'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        catalogFilterBox: (() => { const el = document.querySelector('.catalog-filters'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        searchInputBox: (() => { const el = document.querySelector('.catalog-search input'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        links: [...document.querySelectorAll('header a, footer a')].map(a => a.getAttribute('href')),
        tiktokShell: (() => { const el = document.querySelector('.tiktok-shell'); if (!el) return null; const r = el.getBoundingClientRect(); const cols = getComputedStyle(el).gridTemplateColumns; return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width), columns: cols === 'none' ? 1 : cols.split(' ').filter(Boolean).length, accounts: !!document.querySelector('.tiktok-accounts'), stage: !!document.querySelector('.tiktok-stage'), posts: !!document.querySelector('.tiktok-posts'), panelsOverflow: [...document.querySelectorAll('.tiktok-panel, .tiktok-stage')].some(p => Math.round(p.getBoundingClientRect().right) > Math.round(innerWidth) + 1) }; })(),
      })`);
      assert(state.title, 'Missing page content');
      assert(state.scrollWidth <= state.width + 1, JSON.stringify(state));
      assert.doesNotMatch(state.text, /Bot Standby|Status Engine Bot|Detail Channel YouTube|hls_confirmed/);
      assert.doesNotMatch(state.text, /JKT48 Live Archive|JKT48 LIVE ARCHIVE/);
      // Anti-regresi nada bahasa: halaman publik memakai sapaan "kamu" dan
      // istilah "replay". Kata formal "Anda" dan istilah lama "siaran ulang"
      // tidak boleh balik lagi ke UI publik.
      if (route === '/' || route === '/members' || route === '/about' || route === '/tiktok') {
        assert.doesNotMatch(state.text, /\bAnda\b/, `Kata "Anda" terlalu formal di ${state.path}`);
        assert.doesNotMatch(state.text, /siaran ulang/i, `Istilah "siaran ulang" tidak dipakai di ${state.path}`);
      }
      // Halaman /tiktok: tata letak 3 kolom (kiri akun · tengah pemutar ·
      // kanan daftar arsip) di desktop, 2 kolom ≤1100px, 1 kolom ≤820px —
      // kolomnya pun tidak boleh meluber keluar viewport.
      if (route === '/tiktok') {
        assert(state.tiktokShell, 'Halaman /tiktok harus memuat .tiktok-shell');
        assert(state.tiktokShell.accounts && state.tiktokShell.stage && state.tiktokShell.posts,
          `Sidebar kiri/tengah/kanan wajib ada: ${JSON.stringify(state.tiktokShell)}`);
        assert(!state.tiktokShell.overflows, `Tata letak TikTok meluber di ${width}px`);
        assert(!state.tiktokShell.panelsOverflow, `Panel TikTok keluar viewport di ${width}px`);
        const expectedColumns = width >= 1101 ? 3 : width >= 821 ? 2 : 1;
        assert.equal(state.tiktokShell.columns, expectedColumns,
          `Tata letak TikTok harus ${expectedColumns} kolom di ${width}px: ${JSON.stringify(state.tiktokShell)}`);
        assert.match(state.title, /Arsip TikTok member JKT48/);
      }
      if (state.brand) { assert.match(state.brand, /JKT48\s*REPLAY/); assert(!state.logo48, 'Logo 48 lama masih tampil'); }
      if (state.navToggleVisible !== null) {
        if (width <= 900) {
          assert.equal(state.navToggleVisible, true, 'Hamburger harus tampil di mobile');
          assert.equal(state.navMenuDisplay, 'none', 'Menu harus tertutup default di mobile');
        } else {
          assert.equal(state.navToggleVisible, false, 'Hamburger harus sembunyi di desktop');
        }
      }
      if (state.heroH1) assert(!state.heroH1.overflows, `Judul hero meluber di ${width}px`);
      if (state.sectionH2) assert(!state.sectionH2.overflows, `Section H2 meluber di ${width}px ${state.path}: ${JSON.stringify(state.sectionH2)}`);
      if (state.catalogFilterBox) assert(!state.catalogFilterBox.overflows, `Filter katalog meluber di ${width}px ${state.path}: ${JSON.stringify(state.catalogFilterBox)}`);
      // Hero poster (full-bleed): panel, isi, dan latar gambarnya harus utuh.
      if (state.heroFeature) assert(!state.heroFeature.overflows, `Hero poster meluber di ${width}px ${state.path}: ${JSON.stringify(state.heroFeature)}`);
      if (state.heroFeature && state.heroBody) assert(state.heroBody.rectRight <= state.heroFeature.rectRight + 1, `Teks hero keluar dari panel di ${width}px: ${JSON.stringify(state.heroBody)}`);
      if (state.heroImg) assert(state.heroImg.loaded, `Latar hero gagal dimuat di ${width}px: ${JSON.stringify(state.heroImg)}`);
      if (state.searchInputBox) assert(!state.searchInputBox.overflows, `Input pencarian meluber di ${width}px ${state.path}: ${JSON.stringify(state.searchInputBox)}`);
      assert(!state.links.includes('/status') && !state.links.includes('/admin'));
      if (route === '/admin/status') assert.equal(state.path, '/login');
      // Halaman depan: hero poster wajib tampil (dan menampilkan judul replay
      // asli) selama masih ada replay terbit — angka diambil dari ringkasan
      // hasil katalog, jadi uji ini tetap benar walau isi arsip berubah.
      if (route === '/') {
        const totalReplays = Number((state.text.match(/(\d+)\s+replay siap ditonton/) || [])[1] || 0);
        if (totalReplays > 0) {
          assert(state.heroFeature, `Hero poster wajib tampil saat ada ${totalReplays} replay`);
          assert.match(state.title, /^LIVE (IDN|SHOWROOM)/, `Judul hero harus judul replay asli: ${state.title}`);
        } else {
          assert.match(state.title, /Replay terbaru/);
        }
        const shot = await send('Page.captureScreenshot', { format: 'png' });
        writeFileSync(join(import.meta.dirname, `portal-${width}.png`), Buffer.from(shot.data, 'base64'));
      }
      console.log(`PASS browser ${width}px ${route} -> ${state.path}`);
    }
  }
} finally { ws.close(); await fetch('http://localhost:9222/json/close/' + target.id); }
