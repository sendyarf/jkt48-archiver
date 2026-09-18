const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const target = await fetch('http://localhost:9222/json/new?http://localhost:3000/watch/R8pnx79dyDQ', { method: 'PUT' }).then(r => r.json());
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', e => {
    const message = JSON.parse(e.data);
    if (!message.id) return;
    const callback = pending.get(message.id);
    pending.delete(message.id);
    message.error ? callback.reject(message.error) : callback.resolve(message.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    pending.set(++id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression, userGesture = false) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture });
    if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const click = async () => {
    await evaluate("document.querySelector('.theater-btn').click()", true);
    await delay(600);
  };
  try {
    await send('Page.enable');
    for (let i = 0; i < 60; i++) {
      if (await evaluate("!!document.querySelector('.theater-btn')")) break;
      await delay(500);
    }
    assert(await evaluate("!!document.querySelector('.theater-btn')"), 'Theater button missing');
    await evaluate('(() => { window.originalPlayer = document.querySelector("[data-media-player]"); window.oldOverflow = document.body.style.overflow; window.oldScroll = scrollY; return !!window.originalPlayer; })()');
    await click();
    assert(await evaluate('document.fullscreenElement === document.documentElement'), 'Desktop document fullscreen not entered');
    assert(await evaluate("!!document.querySelector('.theater-mode')"));
    await evaluate('document.exitFullscreen()');
    await delay(400);
    assert(await evaluate("!document.querySelector('.theater-mode')"), 'Browser fullscreen exit should close theater');
    // Exercise rejection without changing the application implementation.
    await evaluate("window.originalRequest = document.documentElement.requestFullscreen; document.documentElement.requestFullscreen = () => Promise.reject(new Error('Test denial'))");
    let checks = 0;
    for (const [width, height, mobile] of [[1440, 900, false], [390, 844, true], [844, 390, true]]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height, mobile, deviceScaleFactor: 1 });
      await send('Emulation.setTouchEmulationEnabled', { enabled: mobile, maxTouchPoints: 5 });
      await click();
      for (let mode = 0; mode < 3; mode++) {
        await evaluate("document.querySelector('.ratio-btn').click()");
        await delay(300);
        const result = await evaluate(`(() => {
          const shell = document.querySelector('.theater-mode');
          const rect = shell.getBoundingClientRect();
          const p = shell.querySelector('.player-inner-container').getBoundingClientRect();
          return { viewport: Math.abs(rect.width-innerWidth)<2 && Math.abs(rect.height-innerHeight)<2 && Math.abs(rect.top)<2,
            dimensions: { width: rect.width, height: rect.height, top: rect.top, innerWidth, innerHeight },
            bounds: p.left>=-1 && p.top>=-1 && p.right<=innerWidth+1 && p.bottom<=innerHeight+1,
            samePlayer: originalPlayer === document.querySelector('[data-media-player]'),
            inert: document.querySelector('.watch-details-card').inert,
            locked: document.body.style.overflow === 'hidden' };
        })()`);
        if (!result.viewport) console.log('VIEWPORT DEBUG', await evaluate(`JSON.stringify({
          clientWidth: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
          viewport: document.querySelector('meta[name="viewport"]')?.content,
          visual: { width: visualViewport.width, height: visualViewport.height, scale: visualViewport.scale },
          grid: getComputedStyle(document.querySelector('.watch-layout')).gridTemplateColumns,
          overflowing: [...document.querySelectorAll('body *')].map(e => ({ tag: e.tagName, class: e.className, left: e.getBoundingClientRect().left, right: e.getBoundingClientRect().right, width: e.getBoundingClientRect().width })).filter(r => r.width && (r.right > document.documentElement.clientWidth + 2 || r.left < -2)).slice(0, 30)
        })`));
        assert(Object.values(result).every(Boolean), JSON.stringify({width, height, mode, result}));
        checks++;
      }
      await evaluate("document.querySelector('[aria-label^=\"Atur volume\"]').focus(); document.querySelector('[aria-label^=\"Atur volume\"]').click()");
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      await delay(200);
      assert(await evaluate("!document.querySelector('.volume-panel') && !!document.querySelector('.theater-mode')"), 'First Escape should only close volume');
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      await delay(200);
      assert(await evaluate("!document.querySelector('.theater-mode') && !document.querySelector('.watch-details-card').inert && document.body.style.overflow === oldOverflow"), 'Escape cleanup failed');
      assert(await evaluate("document.activeElement === document.querySelector('.theater-btn')"), 'Focus not restored');
    }
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, mobile: false, deviceScaleFactor: 1 });
    await click();
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(__dirname, 'theater-check.png'), Buffer.from(shot.data, 'base64'));
    await click();
    assert(await evaluate("!document.querySelector('.theater-mode')"), 'Toggle exit failed');
    console.log(`PASS: desktop fullscreen and browser exit, denied-fullscreen fallback, ${checks} responsive layouts, player identity, volume Escape, focus and cleanup.`);
  } finally {
    ws.close();
    await fetch('http://localhost:9222/json/close/' + target.id);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
