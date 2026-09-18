const delay = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const target = await fetch('http://localhost:9222/json/new?http://localhost:3000/watch/R8pnx79dyDQ', {method:'PUT'}).then(r=>r.json());
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r=>ws.addEventListener('open', r, {once:true}));
  let id=0; const pending=new Map();
  ws.addEventListener('message', e=>{const m=JSON.parse(e.data); if(m.id){const p=pending.get(m.id); pending.delete(m.id); m.error?p.reject(m.error):p.resolve(m.result);}});
  const send=(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  try {
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,mobile:true,deviceScaleFactor:1});
    await delay(3000);
    const measure=`(() => ({innerWidth,clientWidth:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth, elements:['.navbar','.nav-container','.brand','.nav-menu','.nav-actions','.bot-pill'].map(s=>{const e=document.querySelector(s),c=getComputedStyle(e),r=e.getBoundingClientRect();return {selector:s,width:r.width,right:r.right,display:c.display,position:c.position,flex:c.flex,flexWrap:c.flexWrap,minWidth:c.minWidth,overflowX:c.overflowX,gap:c.gap};})}))()`;
    console.log('BEFORE',JSON.stringify(await evaluate(measure)));
    await evaluate(`{const style=document.createElement('style');style.textContent='@media(max-width: 900px){.nav-container{height:auto;min-height:72px;flex-wrap:wrap;padding-top:12px;padding-bottom:12px;gap:12px}.nav-menu{order:3;flex-basis:100%;justify-content:center;flex-wrap:wrap}.nav-actions .bot-pill,.brand-badge{display:none}.nav-link{padding:8px 10px}.brand{min-width:0}.brand-logo{flex-shrink:0}.brand-title{font-size:1rem}}';document.head.append(style);}`);
    await delay(700);
    const after=await evaluate(measure); console.log('AFTER',JSON.stringify(after));
    if(after.innerWidth!==390||after.scrollWidth!==390)throw Error('Viewport still overflows');
  } finally {ws.close();await fetch('http://localhost:9222/json/close/'+target.id);}
})().catch(e=>{console.error(e);process.exitCode=1;});
