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
  for (const width of [1440, 390]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: width === 390 });
    for (const route of ['/', '/members', '/about', '/admin/status']) {
      await send('Page.navigate', { url: 'http://localhost:3000' + route });
      const expectedPath = route === '/admin/status' ? '/login' : route;
      for (let i = 0; i < 100; i++) { await delay(250); if (await evaluate(`document.readyState === 'complete' && location.pathname === ${JSON.stringify(expectedPath)} && !!document.querySelector('h1')`)) break; }
      await delay(600);
      const state = await evaluate(`({ title: document.querySelector('h1')?.textContent, path: location.pathname, width: innerWidth, scrollWidth: document.documentElement.scrollWidth, text: document.body.innerText, links: [...document.querySelectorAll('header a, footer a')].map(a => a.getAttribute('href')) })`);
      assert(state.title, 'Missing page content'); assert(state.scrollWidth <= state.width + 1, JSON.stringify(state));
      assert.doesNotMatch(state.text, /Bot Standby|Status Engine Bot|Detail Channel YouTube|hls_confirmed/);
      assert(!state.links.includes('/status') && !state.links.includes('/admin'));
      if (route === '/admin/status') assert.equal(state.path, '/login');
      if (route === '/') { assert.match(state.title, /Momen favorit/); const shot = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(join(import.meta.dirname, `portal-${width}.png`), Buffer.from(shot.data, 'base64')); }
      console.log(`PASS browser ${width}px ${route} -> ${state.path}`);
    }
  }
} finally { ws.close(); await fetch('http://localhost:9222/json/close/' + target.id); }
