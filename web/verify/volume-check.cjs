const fs = require('node:fs');
const delay = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const target = await fetch('http://localhost:9222/json/new?http://localhost:3000/watch/R8pnx79dyDQ', { method: 'PUT' }).then(r => r.json());
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } });
  const send = (method, params = {}) => new Promise((resolve, reject) => { pending.set(++id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async expression => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const assert = (ok, msg) => { if (!ok) throw Error(msg); };
  try {
    await send('Page.enable');
    for (let i = 0; i < 60; i++) { if (await evaluate("!!document.querySelector('[aria-label^=\"Atur volume\"]')")) break; await delay(500); }
    assert(await evaluate("!!document.querySelector('[aria-label^=\"Atur volume\"]')"), 'New volume control not loaded');
    const failures = [];
    let checks = 0;
    for (const width of [220, 280, 360, 440, 570]) {
      await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 950, deviceScaleFactor: 1, mobile: false });
      for (let mode = 0; mode < 3; mode++) {
        await evaluate(`document.querySelector('.player-inner-container').style.width = '${width}px'; document.querySelector('.ratio-btn').click(); document.querySelector('.player-toast').classList.add('show');`);
        await delay(350);
        for (const open of [false, true]) {
          await evaluate(`if (document.querySelector('[aria-label^="Atur volume"]').getAttribute('aria-expanded') !== '${open}') document.querySelector('[aria-label^="Atur volume"]').click();`);
          await delay(100);
          const result = await evaluate(`(() => {
            const p = document.querySelector('.player-inner-container'), b = p.getBoundingClientRect();
            const buttons = [...p.querySelectorAll('.buttons-row .ctrl-btn')];
            const elements = [...buttons, ...p.querySelectorAll('input, .volume-panel')];
            const clipped = elements.filter(e => { const r = e.getBoundingClientRect(); return r.width <= 0 || r.left < b.left || r.right > b.right + 1 || r.top < b.top || r.bottom > b.bottom; }).map(e => e.className);
            const tops = buttons.map(e => e.getBoundingClientRect().top);
            const overlap = (a, b) => a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1;
            const toast = p.querySelector('.player-toast');
            const toastIssue = toast && toast.classList.contains('show') && (['.buttons-row', '.volume-panel'].map(s => p.querySelector(s)).filter(t => t && overlap(toast.getBoundingClientRect(), t.getBoundingClientRect())).length || (r => r.left < b.left - 1 || r.right > b.right + 1 || r.top < b.top - 1)(toast.getBoundingClientRect())) ? 'overlaps' : '';
            return { mode: p.className, width: b.width, clipped, singleRow: Math.max(...tops) - Math.min(...tops) < 3, repeat: !!p.querySelector('[aria-label="Ulang Video"]'), toastIssue };
          })()`);
          if (result.clipped.length || !result.singleRow || result.repeat || result.toastIssue) failures.push(result);
          checks++;
        }
      }
    }
    assert(!failures.length, JSON.stringify(failures));
    await evaluate("document.querySelector('.player-inner-container').style.width = '280px'; document.querySelector('.ratio-btn').click()");
    await delay(350);
    await evaluate("document.querySelector('[aria-label^=\"Atur volume\"]').click(); document.querySelector('.player-inner-container').scrollIntoView({block:'center'})");
    await delay(400);
    const clip = await evaluate("(() => { const r = document.querySelector('.player-inner-container').getBoundingClientRect(); return {x:r.left+scrollX,y:r.top+scrollY,width:r.width,height:r.height,scale:1}; })()");
    const shot = await send('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true });
    fs.writeFileSync(require('node:path').join(__dirname, 'volume-redesign-check.png'), Buffer.from(shot.data, 'base64'));
    console.log(`PASS: ${checks} actual-page layouts; screenshot saved outside .next.`);
  } finally {
    ws.close();
    await fetch('http://localhost:9222/json/close/' + target.id);
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
