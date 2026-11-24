import test from 'node:test';
import assert from 'node:assert/strict';
import worker,{authorized,command} from './worker.mjs';
const env={TELEGRAM_BOT_TOKEN:"test",TELEGRAM_OWNER_ID:"123",TELEGRAM_WEBHOOK_SECRET:"a".repeat(32)};
const message={chat:{type:"private",id:123},from:{id:123,is_bot:false},text:"/start"};
test("owner only, private chat",()=>{
 assert.equal(authorized(message,env),true);
 assert.equal(authorized({...message,from:{id:124,is_bot:false}},env),false);
 assert.equal(authorized({...message,chat:{type:"group",id:123}},env),false);
 assert.equal(authorized(message,{...env,TELEGRAM_OWNER_ID:""}),false);
});
test("webhook rejects wrong secret",async()=>{
 const r=await worker.fetch(new Request("https://example.test/telegram",{method:"POST",body:"{}"}),env);
 assert.equal(r.status,403);
});
test("unauthorized updates ignored without upstream calls",async()=>{
 const r=await worker.fetch(new Request("https://example.test/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":env.TELEGRAM_WEBHOOK_SECRET},body:JSON.stringify({message:{...message,from:{id:124,is_bot:false}}})}),env);
 assert.equal(r.status,200);
});
test("no paid or publishing command",async()=>{
 assert.match(await command("/publish",env),/не поддерживается/);
 assert.match(await command("/generate",env),/не поддерживается/);
 assert.match(await command("/balance",env),/не настроен/);
});
test("models are explicitly planned",async()=>assert.match(await command("/models",env),/не подтверждение/));

test("owner start sends menu with Cloudflare-supported manual redirects",async()=>{
 const original=globalThis.fetch;
 let calls=0;
 globalThis.fetch=async(url,options)=>{
  calls++;
  assert.equal(options.redirect,"manual");
  assert.match(url,/api\.telegram\.org\/bot.*\/sendMessage$/);
  const body=JSON.parse(options.body);
  assert.equal(body.chat_id,"123");
  assert.match(body.text,/режим только просмотра/i);
  assert.ok(body.reply_markup.keyboard.length);
  return Response.json({ok:true,result:{message_id:1}});
 };
 try {
  const r=await worker.fetch(new Request("https://example.test/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":env.TELEGRAM_WEBHOOK_SECRET},body:JSON.stringify({message})}),env);
  assert.equal(r.status,200);
  assert.equal(calls,1);
 } finally {globalThis.fetch=original;}
});

import {createHmac} from 'node:crypto';
import {verifyInitData} from './miniapp.mjs';
function signed(id=123,stamp=Math.floor(Date.now()/1000)){
 const p=new URLSearchParams({auth_date:String(stamp),user:JSON.stringify({id,is_bot:false}),query_id:"test"});
 const data=[...p.entries()].sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,v])=>k+"="+v).join("\n");
 const key=createHmac("sha256","WebAppData").update(env.TELEGRAM_BOT_TOKEN).digest();
 p.set("hash",createHmac("sha256",key).update(data).digest("hex"));
 return p.toString();
}
test("Mini App rejects altered, foreign, expired and duplicate Telegram initData",async()=>{
 const good=signed();
 assert.equal(await verifyInitData(good,env),true);
 assert.equal(await verifyInitData(good.replace("test","fake"),env),false);
 assert.equal(await verifyInitData(signed(124),env),false);
 assert.equal(await verifyInitData(signed(123,Math.floor(Date.now()/1000)-3700),env),false);
 assert.equal(await verifyInitData(good+"&user=x",env),false);
});
test("public shell has no balance, unsigned API returns 401",async()=>{
 const page=await worker.fetch(new Request("https://example.test/app"),env);
 assert.equal(page.status,200);
 assert.match(await page.text(),/ZEN CONTROL/);
 const result=await worker.fetch(new Request("https://example.test/api/dashboard",{method:"POST",body:JSON.stringify({initData:"fake"})}),env);
 assert.equal(result.status,401);
});
test("signed owner gets balance only, without credentials",async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async(url,opts)=>{
  assert.equal(url,"https://routerai.ru/api/v1/credits");
  assert.equal(opts.redirect,"manual");
  return Response.json({data:{credits:99.98}});
 };
 try{
  const result=await worker.fetch(new Request("https://example.test/api/dashboard",{method:"POST",body:JSON.stringify({initData:signed()})}),{...env,ROUTERAI_API_KEY:"not-real"});
  assert.equal(result.status,200);
  const d=await result.json();
  assert.equal(d.balance,99.98);
  assert.equal(d.mode,"read_only");
  assert.equal(Object.hasOwn(d,"token"),false);
 }finally{globalThis.fetch=original;}
});

import {runInNewContext} from "node:vm";
import {JS as appScript} from "./ui.mjs";
test("client recovers signed launch data from hash without Telegram SDK",async()=>{
 const raw=signed();
 let submitted=null;
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{classList:{add(){},remove(){}},addEventListener(){}});return elements.get(id);};
 const context={window:{},location:{hash:"#tgWebAppData="+encodeURIComponent(raw),search:""},document:{getElementById:get,querySelectorAll:()=>[]},URLSearchParams,Intl,Date,JSON,setTimeout,fetch:async(url,options)=>{
  submitted=JSON.parse(options.body).initData;
  assert.equal(url,"/api/dashboard");
  return {ok:true,json:async()=>({balance:99.98,checkedAt:new Date().toISOString()})};
 }};
 runInNewContext(appScript,context);
 await new Promise(r=>setImmediate(r));
 assert.equal(submitted,raw);
 assert.equal(get("auth").textContent,"Владелец");
});

import {snapshotBindings} from "./snapshot.mjs";
import {readSnapshot} from "./miniapp.mjs";
test("private review snapshot handles UTF-8 and rejects incomplete bindings",()=>{
 const sample={run:"45395283968",articles:[{text:"Мистика — "+ "кириллица ".repeat(1000)}],models:[],total:61.98};
 const entries=snapshotBindings(sample);
 const bindings=Object.fromEntries(entries.map(x=>[x.name,x.text]));
 assert.deepEqual(readSnapshot(bindings),sample);
 assert.equal(readSnapshot({...bindings,REVIEW_SNAPSHOT_1:undefined}),null);
});
test("unsigned browser cannot access private snapshot, signed owner can",async()=>{
 const sample={run:"45395283968",articles:[{text:"Приватный черновик"}],models:[],total:61.98};
 const bindings=Object.fromEntries(snapshotBindings(sample).map(x=>[x.name,x.text]));
 const fullEnv={...env,...bindings};
 const publicPage=await worker.fetch(new Request("https://example.test/app"),fullEnv);
 assert.doesNotMatch(await publicPage.text(),/Приватный черновик/);
 const denied=await worker.fetch(new Request("https://example.test/api/dashboard",{method:"POST",body:JSON.stringify({initData:signed(124)})}),fullEnv);
 assert.equal(denied.status,401);
 const allowed=await worker.fetch(new Request("https://example.test/api/dashboard",{method:"POST",body:JSON.stringify({initData:signed()})}),fullEnv);
 assert.deepEqual((await allowed.json()).review,sample);
});
