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

import {readPrivateImage} from "./snapshot.mjs";
test("image endpoint rejects unsigned access and missing read-only credential",async()=>{
 const req=raw=>new Request("https://example.test/api/image",{method:"POST",body:JSON.stringify({initData:raw,revision:"a".repeat(40),path:"routerai-validation/45395283968/science/images/image-01.jpg"})});
 assert.equal((await worker.fetch(req(signed(124)),env)).status,401);
 assert.equal((await worker.fetch(req(signed()),env)).status,503);
});
test("image proxy refuses traversal and non-raster files before network",async()=>{
 const original=globalThis.fetch;globalThis.fetch=async()=>{throw Error("must not call");};
 try{
  assert.equal(await readPrivateImage("fake","a".repeat(40),"routerai-validation/45395283968/../secret.jpg"),null);
  assert.equal(await readPrivateImage("fake","a".repeat(40),"routerai-validation/45395283968/science/images/image-01.svg"),null);
 }finally{globalThis.fetch=original;}
});
test("private image proxy validates repository and returns raster bytes",async()=>{
 const original=globalThis.fetch;let calls=0;
 globalThis.fetch=async(url,opts)=>{
  calls++;assert.ok(url.startsWith("https://api.github.com/repos/kaokshi/zen-editorial-review"));assert.equal(opts.redirect,"manual");
  return calls===1?Response.json({full_name:"kaokshi/zen-editorial-review",private:true,visibility:"private"}):new Response(new Uint8Array([255,216,255,0,0]));
 };
 try{
  const result=await readPrivateImage("fake","a".repeat(40),"routerai-validation/45395283968/science/images/image-01.jpg");
  assert.equal(result.type,"image/jpeg");assert.equal(calls,2);
 }finally{globalThis.fetch=original;}
});

import {ReviewJournal,validateDecision} from "./journal.mjs";
const decision={action:"revise",channel:"science",revision:"a".repeat(40),run:"45395283968",id:"9b00a000-1111-4222-8333-444444444444",note:"Проверить научный факт",target:"article"};
const review={revision:decision.revision,run:decision.run,articles:[{channel:"science",text:"Черновик",assembled:false,textQA:"passed",imageQA:"failed",images:[{path:"routerai-validation/45395283968/science/images/image-01.jpg"}]}]};
test("review decision is bound to exact revision and ready material",()=>{
 assert.ok(validateDecision(decision,review).entry);
 assert.equal(validateDecision({...decision,revision:"b".repeat(40)},review).error,"stale_revision");
 assert.equal(validateDecision({...decision,action:"approve"},review).error,"not_ready");
 assert.equal(validateDecision({...decision,note:"  "},review).error,"note_required");
 assert.equal(validateDecision({...decision,target:"unknown"},review).error,"invalid_target");
 assert.ok(validateDecision({...decision,target:review.articles[0].images[0].path},review).entry);
 assert.equal(validateDecision({...decision,action:"publish"},review).status,400);
 const ready={...review,articles:[{...review.articles[0],assembled:true,assembledVerified:true,imageQA:"passed"}]};
 assert.ok(validateDecision({...decision,action:"approve"},ready).entry);
});
test("journal is persistent and idempotent, conflicting replay rejected",async()=>{
 const store=new Map();
 const storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),list:async({prefix})=>new Map([...store].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage)};
 const obj=new ReviewJournal({storage});
 const entry=validateDecision(decision,review).entry;
 const req=e=>new Request("https://journal.internal/",{method:"POST",body:JSON.stringify(e)});
 assert.equal((await obj.fetch(req(entry))).status,200);
 assert.equal((await obj.fetch(req({...entry,at:new Date(0).toISOString()}))).status,200);
 assert.equal((await obj.fetch(req({...entry,note:"Other"}))).status,409);
 const restarted=new ReviewJournal({storage});
 assert.deepEqual((await (await restarted.fetch(new Request("https://journal.internal/"))).json()).entries,[entry]);
});
test("journal and decision endpoints reject non-owner before storage access",async()=>{
 for(const path of ["/api/journal","/api/review"]){
  const r=await worker.fetch(new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})}),{...env,REVIEW_JOURNAL:{idFromName(){throw Error("must not touch");}}});
  assert.equal(r.status,401);
 }
});
