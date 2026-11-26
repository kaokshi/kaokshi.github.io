// Two independent Cloudflare sessions, synthetic state only. Never opens Dzen.
const { chromium } = require('playwright-core');
const id=process.env.CF_ACCOUNT_ID,token=process.env.CF_API_TOKEN;
if(!/^[a-f0-9]{32}$/i.test(id||'')||!token) { console.error('CONFIG_MISSING');process.exit(1); }
const root='https://api.cloudflare.com/client/v4/accounts/'+id+'/browser-run/devtools/browser';
const headers={Authorization:'Bearer '+token};
const origin='https://example.invalid';
const daily=480000,allowance=90000;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function call(url,method='GET',timeout=5000){
  const response=await fetch(url,{method,headers,redirect:'error',signal:AbortSignal.timeout(timeout)});
  if(!response.ok)throw Error('HTTP_'+response.status);
  return response.json();
}
async function history(){
  const rows=[],seen=new Set();
  for(let offset=0;offset<10000;offset+=100){
    const data=await call(root.replace(/\/browser$/,'/session')+'?limit=100&offset='+offset);
    const list=Array.isArray(data)?data:data.result;
    if(!Array.isArray(list))throw Error('INVALID_HISTORY');
    for(const row of list){
      if(typeof row.sessionId!=='string'||seen.has(row.sessionId))throw Error('INVALID_HISTORY');
      seen.add(row.sessionId);rows.push(row);
    }
    if(list.length<100)return rows;
  }
  throw Error('HISTORY_INCOMPLETE');
}
async function admit(count){
  const rows=await history(),now=Date.now(),day=new Date(now);
  day.setUTCHours(0,0,0,0);
  let used=0;
  for(const r of rows){
    if(!Number.isFinite(r.startTime)||r.startTime<1e12||r.startTime>now)throw Error('INVALID_START');
    if(!Number.isFinite(r.endTime)||r.endTime<r.startTime||r.endTime>now+5000)throw Error('ACTIVE_OR_INVALID_SESSION');
    used+=Math.max(0,Math.min(now,r.endTime)-Math.max(day.getTime(),r.startTime));
  }
  console.log('BROWSER_SESSION_USAGE_MS='+used);
  if(used+allowance*count>daily||now+allowance*count>=day.getTime()+86400000)throw Error('BUDGET_STOP');
}
const {confirmClose}=require('./zen_browser_close.cjs');
async function closeSession(sid){
  await confirmClose({sid,root,call,pause});
  console.log('EXPLICIT_CLOSE_CONFIRMED');
}
async function session(state,first){
  let sid,browser,watchdog,emergency=false,closing;
  const close=()=>closing||(closing=closeSession(sid));
  try{
    const data=await call(root+'?keep_alive=10000','POST',15000);
    const result=data.result||data;
    if(!/^[a-f0-9-]{36}$/i.test(result.sessionId||''))throw Error('SESSION_ID_MISSING');
    sid=result.sessionId;
    watchdog=setTimeout(()=>{emergency=true;close().catch(()=>{});},60000);
    watchdog.unref();
    browser=await chromium.connectOverCDP(root.replace('https:','wss:')+'/'+sid,{headers,timeout:15000});
    const ctx=await browser.newContext(first?{}:{storageState:state});
    const page=await ctx.newPage();
    await page.route('**/*',r=>r.request().url()===origin+'/'?r.fulfill({status:200,contentType:'text/html',body:'<!doctype html><title>Synthetic test</title>'}):r.abort());
    await page.goto(origin+'/',{waitUntil:'domcontentloaded',timeout:5000});
    if(first){
      await ctx.addCookies([{name:'synthetic_state',value:'test-only',url:origin}]);
      await page.evaluate(()=>localStorage.setItem('synthetic_state','test-only'));
      const saved=await ctx.storageState();
      // Reject unexpected state before keeping it in memory.
      if(saved.cookies.length!==1||saved.cookies[0].name!=='synthetic_state'||saved.cookies[0].domain!=='example.invalid'||saved.origins.length!==1||saved.origins[0].origin!==origin)throw Error('UNEXPECTED_STATE');
      if(emergency)throw Error('WATCHDOG');
      return saved;
    }
    const cookies=await ctx.cookies(origin);
    const local=await page.evaluate(()=>localStorage.getItem('synthetic_state'));
    if(!cookies.some(c=>c.name==='synthetic_state'&&c.value==='test-only')||local!=='test-only'||emergency)throw Error('RESTORE_FAILED');
    console.log('SYNTHETIC_COOKIE_AND_LOCAL_STORAGE_RESTORED');
  }finally{
    if(watchdog)clearTimeout(watchdog);
    if(sid)await close();
    if(browser)await browser.close().catch(()=>{});
    if(emergency)throw Error('WATCHDOG');
  }
}
(async()=>{
  await admit(2);
  const state=await session(null,true);
  await admit(1);
  await session(state,false);
  console.log('TWO_SESSION_STATE_RESTORE_PASS');
})().catch(e=>{console.error('PROBE_FAILED_'+(/^[A-Z0-9_]+$/.test(e.message)?e.message:'UNEXPECTED'));process.exitCode=1;});
