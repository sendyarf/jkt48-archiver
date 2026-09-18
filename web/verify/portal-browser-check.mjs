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
    for (const route of ['/', '/members', '/about', '/admin/status']) {
      await send('Page.navigate', { url: 'http://localhost:3101' + route });
      const expectedPath = route === '/admin/status' ? '/login' : route;
      for (let i = 0; i < 100; i++) { await delay(250); if (await evaluate(`document.readyState === 'complete' && location.pathname === ${JSON.stringify(expectedPath)} && !!document.querySelector('h1')`)) break; }
      await delay(600);
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
        heroH1: (() => { const el = document.querySelector('.catalog-hero h1'); if (!el) return null; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return { size: s.fontSize, overflows: r.width > innerWidth + 1 }; })(),
        sectionH2: (() => { const el = document.querySelector('.section-heading h2'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        catalogFilterBox: (() => { const el = document.querySelector('.catalog-filters'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        heroNoteBox: (() => { const el = document.querySelector('.hero-note'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        searchInputBox: (() => { const el = document.querySelector('.catalog-search input'); if (!el) return null; const r = el.getBoundingClientRect(); return { overflows: Math.round(r.right) > Math.round(innerWidth) + 1, rectRight: Math.round(r.right), rectWidth: Math.round(r.width) }; })(),
        links: [...document.querySelectorAll('header a, footer a')].map(a => a.getAttribute('href')),
      })`);
      assert(state.title, 'Missing page content');
      assert(state.scrollWidth <= state.width + 1, JSON.stringify(state));
      assert.doesNotMatch(state.text, /Bot Standby|Status Engine Bot|Detail Channel YouTube|hls_confirmed/);
      assert.doesNotMatch(state.text, /JKT48 Live Archive|JKT48 LIVE ARCHIVE/);
      if (state.brand) { assert.match(state.brand, /JKT48\s*REPLAY/); assert(!state.logo48, 'Logo 48 lama masih tampil'); }
      if (state.navToggleVisible !== null) {
        if (width <= 900) {
          assert.equal(state.navToggleVisible, true, 'Hamburger harus tampil di mobile');
          assert.equal(state.navMenuDisplay, 'none', 'Menu harus tertutup default di mobile');
        } else {
          assert.equal(state.navToggleVisible, false, 'Hamburger harus sembunyi di desktop');
        }
      }
      if (state.heroH1) assert(!state.heroH1.overflows, `Hero H1 meluber di ${width}px`);
      if (state.sectionH2) assert(!state.sectionH2.overflows, `Section H2 meluber di ${width}px ${state.path}: ${JSON.stringify(state.sectionH2)}`);
      if (state.catalogFilterBox) assert(!state.catalogFilterBox.overflows, `Filter katalog meluber di ${width}px ${state.path}: ${JSON.stringify(state.catalogFilterBox)}`);
      if (state.heroNoteBox) assert(!state.heroNoteBox.overflows, `Hero note meluber di ${width}px ${state.path}: ${JSON.stringify(state.heroNoteBox)}`);
      if (state.searchInputBox) assert(!state.searchInputBox.overflows, `Input pencarian meluber di ${width}px ${state.path}: ${JSON.stringify(state.searchInputBox)}`);
      assert(!state.links.includes('/status') && !state.links.includes('/admin'));
      if (route === '/admin/status') assert.equal(state.path, '/login');
      if (route === '/') { assert.match(state.title, /Momen favorit/); const shot = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(join(import.meta.dirname, `portal-${width}.png`), Buffer.from(shot.data, 'base64')); }
      console.log(`PASS browser ${width}px ${route} -> ${state.path}`);
    }
  }
} finally { ws.close(); await fetch('http://localhost:9222/json/close/' + target.id); }
