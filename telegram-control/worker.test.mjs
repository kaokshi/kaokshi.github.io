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

import {CoreHandler,initial,validateSchedule,slots,nextSlots} from "./core.mjs";
const plan={...initial(),paused:false,leadMinutes:360,channels:[{channel:"cinema",enabled:false,times:[]},{channel:"legends",enabled:true,times:["09:00","19:00"]},{channel:"science",enabled:true,times:["12:00"]}]};
test("schedule validates all channels and rejects duplicate or invalid times",()=>{
 assert.ok(validateSchedule(plan));
 assert.equal(validateSchedule({...plan,timezone:"UTC"}),null);
 assert.equal(validateSchedule({...plan,channels:[plan.channels[0],{...plan.channels[1],times:["09:00","09:00"]},plan.channels[2]]}),null);
 assert.equal(validateSchedule({...plan,channels:[plan.channels[0],{...plan.channels[1],times:["25:00"]},plan.channels[2]]}),null);
});
test("Moscow release and preparation slots cross midnight and pause safely",()=>{
 const n=Date.parse("2026-11-24T00:00:00Z");
 const list=slots(plan,n);
 assert.equal(list.find(x=>x.id==="legends:2026-11-24:09:00:release").at,Date.parse("2026-11-24T06:00:00Z"));
 assert.equal(list.find(x=>x.id==="legends:2026-11-24:09:00:prepare").at,n);
 assert.ok(nextSlots(plan,n).every(x=>x.at>n));
 assert.deepEqual(slots({...plan,paused:true},n),[]);
});
test("scheduler records blocked events idempotently, never performs production",async()=>{
 const store=new Map();let alarm=null;
 const storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),delete:async k=>store.delete(k),list:async({prefix})=>new Map([...store].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),getAlarm:async()=>alarm,setAlarm:async n=>alarm=n,deleteAlarm:async()=>alarm=null};
 const h=new CoreHandler({storage});
 const n=Date.parse("2026-11-24T06:00:00Z");
 await storage.put("core:settings",plan);await storage.put("core:cursor",n-1);
 await h.alarm(n);await h.alarm(n);
 const v=await h.view(n);
 assert.equal(v.events.length,1);assert.equal(v.events[0].status,"blocked_prelaunch");
 assert.equal(v.productionEnabled,false);assert.equal(v.rssEnabled,false);assert.ok(alarm>n);
});
test("core and RSS endpoints reject non-owner before external calls",async()=>{
 for(const path of ["/api/core","/api/core/schedule","/api/core/order","/api/rss"]){
  const r=await worker.fetch(new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})}),env);
  assert.equal(r.status,401);
 }
});

import {AgentHandler,estimate,parseReply,MODEL} from "./agent.mjs";
const priceRow={id:MODEL,pricing_units:{prompt:"token",completion:"token"},pricing:{prompt:"0.00001",completion:"0.00004"}};
test("Qwen chat refuses unknown prices and invalid proposals",()=>{
 assert.ok(estimate(priceRow,[{content:"тест"}])>0);
 assert.equal(estimate({...priceRow,pricing_units:{prompt:"million_tokens"}},[]),null);
 assert.throws(()=>parseReply({choices:[{finish_reason:"stop",message:{content:JSON.stringify({reply:"test",proposal:{publication:true}})}}]}));
 assert.equal(parseReply({choices:[{finish_reason:"stop",message:{content:JSON.stringify({reply:"test",proposal:null})}}]}).proposal,null);
});
test("Qwen chat consent, persistent receipt, duplicate protection and confirmed schedule",async()=>{
 const store=new Map();let alarm=null;
 const storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),delete:async k=>store.delete(k),list:async({prefix})=>new Map([...store].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),getAlarm:async()=>alarm,setAlarm:async n=>alarm=n,deleteAlarm:async()=>alarm=null};
 const core=new CoreHandler({storage}),agent=new AgentHandler({storage},{ROUTERAI_API_KEY:"fake"},core);
 const id="9b00a000-1111-4222-8333-444444444444",msg={id,text:"Тест расписания",consent:true};
 const notes=[{kind:"style",text:"Живой русский язык"}];await writeMemory(storage,{notes,version:0,confirm:true});
 const original=globalThis.fetch;let paid=0;
 globalThis.fetch=async(url,opts)=>{
  if(url.endsWith("/models"))return Response.json({data:[priceRow]});
  if(url.endsWith("/credits"))return Response.json({data:{credits:100}});
  assert.ok(url.endsWith("/chat/completions"));paid++;
  const payload=JSON.parse(opts.body);assert.equal(payload.model,MODEL);assert.equal(payload.max_tokens,700);assert.deepEqual(JSON.parse(payload.messages[1].content).editorialMemory,notes);
  return Response.json({model:MODEL,usage:{cost:0.01},choices:[{finish_reason:"stop",message:{content:JSON.stringify({reply:"Предлагаю расписание",proposal:plan})}}]});
 };
 try{
  assert.equal((await agent.chat({...msg,consent:false})).status,400);assert.equal(paid,0);
  const first=await agent.chat(msg);assert.equal(first.body.message.status,"complete");
  assert.equal((await agent.chat(msg)).status,200);assert.equal(paid,1);
  assert.equal((await core.state()).version,0);
  assert.equal((await agent.apply({id,confirm:false})).status,400);
  assert.equal((await agent.apply({id,confirm:true})).status,200);
  assert.equal((await core.state()).version,1);
  assert.equal((await agent.apply({id,confirm:true})).status,409);
  assert.equal((await agent.view()).messages.length,1);
  assert.equal((await core.view()).productionEnabled,false);
 }finally{globalThis.fetch=original;}
});
test("Qwen endpoints reject non-owner without touching storage or paid provider",async()=>{
 for(const path of ["/api/core/agent","/api/core/agent/chat","/api/core/agent/apply"]){
  const r=await worker.fetch(new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})}),env);
  assert.equal(r.status,401);
 }
});

import {validateJob,saveJob,cancelJob,jobsView,STAGES} from "./production.mjs";
test("production jobs are bounded, fixed-stage and never authorize release",async()=>{
 const plan=validateJob({items:[{channel:"science",count:3}],day:"2026-11-25",instructions:"Проверить факты"});
 assert.ok(plan);assert.equal(validateJob({...plan,items:[{channel:"science",count:10}]}),null);
 assert.equal(validateJob({...plan,day:"2026-02-30"}),null);
 const store=new Map(),storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),list:async({prefix})=>new Map([...store].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage)};
 const job=await saveJob(storage,"agent:test",plan,Date.now());
 assert.equal(job.executed,false);assert.equal(job.publicationAuthorized,false);assert.equal(job.stages.length,STAGES.length);
 assert.equal((await saveJob(storage,"agent:test",plan,Date.now())).id,job.id);
 assert.equal((await jobsView(storage)).jobs.length,1);
 assert.equal((await cancelJob(storage,job.id)).status,"cancelled");
 assert.equal((await saveJob(storage,"agent:test",plan,Date.now())).status,"cancelled");
});
test("Qwen job proposals cannot combine actions or bypass count limits",()=>{
 const job={items:[{channel:"legends",count:3}],day:null,instructions:"Истории"};
 const reply=d=>({choices:[{finish_reason:"stop",message:{content:JSON.stringify(d)}}]});
 assert.deepEqual(parseReply(reply({reply:"План",proposal:null,job})).job,job);
 assert.throws(()=>parseReply(reply({reply:"План",proposal:plan,job})));
 assert.throws(()=>parseReply(reply({reply:"План",proposal:null,job:{...job,items:[{channel:"legends",count:100}]}})));
});
test("production queue endpoints reject unauthorized access",async()=>{
 for(const path of ["/api/core/jobs","/api/core/jobs/cancel"]){
 const r=await worker.fetch(new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})}),env);
 assert.equal(r.status,401);
 }
});

test("offline preview image access permits only fixed raster paths",async()=>{
 const orig=globalThis.fetch;let calls=0;
 globalThis.fetch=async(url)=>{calls++;return calls===1?Response.json({full_name:"kaokshi/zen-editorial-review",private:true,visibility:"private"}):new Response(new Uint8Array([255,216,255,0,0]));};
 try{
  assert.equal(await readPrivateImage("fake","a".repeat(40),"routerai-validation/pr85-offline-preview/legends/images/../review.json"),null);
  assert.equal(await readPrivateImage("fake","a".repeat(40),"routerai-validation/pr85-offline-preview/science/images/slot-02.svg"),null);
  assert.equal(calls,0);
  const result=await readPrivateImage("fake","a".repeat(40),"routerai-validation/pr85-offline-preview/legends/images/slot-00.jpg");
  assert.equal(result.type,"image/jpeg");
 }finally{globalThis.fetch=orig;}
});

import {budgetDecision,initialBudget,validateBudget,readBudget,writeBudget} from "./budget.mjs";
test("reserve is only for required completion after main budget and at most 50 rubles",()=>{
 const p=initialBudget(),l={verified:true,mainKop:29900,reserveKop:0};
 const op={kind:"missing_image",started:true,required:true,attempt:1,boundKop:200};
 assert.deepEqual(budgetDecision(p,l,op),{allowed:true,mainKop:100,reserveKop:100,reason:"missing_image"});
 for(const change of [{started:false},{required:false},{kind:"new_article"},{attempt:4}]){
  assert.equal(budgetDecision(p,l,{...op,...change}).allowed,false);
 }
 assert.equal(budgetDecision(p,{...l,verified:false},op).allowed,false);
 assert.equal(budgetDecision(p,{...l,reserveKop:5000},op).allowed,false);
 assert.equal(validateBudget({...p,reserveKop:5100}),null);
});
test("budget update is owner-confirmed, revision checked and spend remains unknown",async()=>{
 const store=new Map(),storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),transaction:async fn=>fn(storage)};
 const d={budget:{mainKop:32000,reserveKop:5000},version:0,confirm:true};
 assert.equal((await writeBudget(storage,{...d,confirm:false})).status,400);
 assert.equal((await writeBudget(storage,d)).status,200);
 assert.equal((await writeBudget(storage,d)).status,409);
 const v=await readBudget(storage);
 assert.equal(v.totalKop,37000);assert.equal(v.production.reserveRemainingKop,null);assert.equal(v.executionEnabled,false);
});
test("budget endpoints deny non-owner",async()=>{
 for(const path of ["/api/core/budget","/api/core/budget/settings"]){
 const r=await worker.fetch(new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})}),env);
 assert.equal(r.status,401);
 }
});

import {opsState,updateOps,opsView,notifyTimer} from "./ops.mjs";
test("emergency pause persists and rejects new paid chat and timer reactivation",async()=>{
 const map=new Map();let alarm=null;
 const storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),delete:async k=>map.delete(k),list:async({prefix})=>new Map([...map].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),getAlarm:async()=>alarm,setAlarm:async n=>alarm=n,deleteAlarm:async()=>alarm=null};
 const core=new CoreHandler({storage});
 const req=(path,d)=>new Request("https://core.internal"+path,{method:"POST",body:JSON.stringify(d)});
 assert.equal((await core.fetch(req("/core/ops/settings",{action:"stop",version:0,confirm:false}))).status,400);
 await storage.put("core:settings",plan);
 assert.equal((await core.fetch(req("/core/ops/settings",{action:"stop",version:0,confirm:true}))).status,200);
 assert.equal((await core.state()).paused,true);assert.equal(await storage.getAlarm(),null);
 assert.equal((await opsState(storage)).stopped,true);
 assert.equal((await core.fetch(req("/core/schedule",{settings:plan,version:1}))).status,409);
 const agent=new AgentHandler({storage},{ROUTERAI_API_KEY:"fake"},core);
 const orig=globalThis.fetch;globalThis.fetch=async()=>{throw Error("must not contact provider");};
 try{assert.equal((await agent.chat({id:"9b00a000-1111-4222-8333-444444444444",text:"test",consent:true})).body.error,"emergency_paused");}finally{globalThis.fetch=orig;}
 assert.equal((await core.fetch(req("/core/ops/settings",{action:"resume",version:1,confirm:true}))).status,200);
 assert.equal((await core.state()).paused,true);assert.equal(await storage.getAlarm(),null);
 assert.equal((await opsView(storage)).events.filter(x=>x.type==="control").length,2);
});
test("timer notifications are opt-in and ambiguous delivery is not repeated",async()=>{
 const map=new Map(),storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),transaction:async fn=>fn(storage)};
 const env={TELEGRAM_BOT_TOKEN:"fake",TELEGRAM_OWNER_ID:"123"},batch=[{channel:"science",type:"prepare",id:"science:2026-11-24:09:00:prepare"}],now=Date.now();
 const orig=globalThis.fetch;let sends=0;globalThis.fetch=async()=>{sends++;return new Response("unavailable",{status:502});};
 try{
 await notifyTimer(storage,env,batch,now);assert.equal(sends,0);
 await updateOps(storage,{action:"notifications",enabled:true,version:0,confirm:true},now,initial);
 await notifyTimer(storage,env,batch,now);await notifyTimer(storage,env,batch,now);assert.equal(sends,1);
 assert.equal((await storage.get("ops:notification:"+String(now).padStart(15,"0"))).status,"unconfirmed");
 }finally{globalThis.fetch=orig;}
});
test("operational endpoints reject non-owner",async()=>{
 for(const path of ["/api/core/ops","/api/core/ops/settings"]){
 const r=await worker.fetch(new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})}),env);
 assert.equal(r.status,401);
 }
});

test("owner-confirmed preview uses real stage registry without production",async()=>{
 const map=new Map(),storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),transaction:async fn=>fn(storage)};
 const h=new CoreHandler({storage});
 const job=await saveJob(storage,"agent:preview-test",validateJob({items:[{channel:"legends",count:2},{channel:"science",count:1}],day:null,instructions:"Synthetic fixture"}),Date.now());
 const req=confirm=>new Request("https://core.internal/core/jobs/preview",{method:"POST",body:JSON.stringify({id:job.id,confirm})});
 const old=globalThis.fetch;globalThis.fetch=async()=>{throw Error("preview must not call network");};
 try{
  assert.equal((await h.fetch(req(false))).status,400);
  const res=await h.fetch(req(true));assert.equal(res.status,200);
  const {preview}=await res.json();
  assert.equal(preview.articles.length,3);assert.equal(preview.taskCount,3*(STAGES.length-1));
  assert.equal(preview.providerCalls,0);assert.equal(preview.publicationAuthorized,false);
  assert.ok(preview.articles.every(a=>a.tasks.every(t=>t.stage!=="rss")));
  assert.deepEqual(await (await h.fetch(req(true))).json(),{preview});
  assert.equal((await storage.get("production:job:"+job.id)).executed,false);
 }finally{globalThis.fetch=old;}
});
test("preview endpoint rejects non-owner before storage and never reaches dispatch",async()=>{
 const r=await worker.fetch(new Request("https://example.test/api/core/jobs/preview",{method:"POST",body:JSON.stringify({initData:signed(124),id:"agent:test",confirm:true})}),{...env,REVIEW_JOURNAL:{idFromName(){throw Error("must not touch storage");}}});
 assert.equal(r.status,401);
});

test("mode choice persists but does not enable production or the scheduler",async()=>{
 const map=new Map();let alarm=null;
 const storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),delete:async k=>map.delete(k),list:async({prefix})=>new Map([...map].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),getAlarm:async()=>alarm,setAlarm:async n=>alarm=n,deleteAlarm:async()=>alarm=null};
 const core=new CoreHandler({storage});const req=d=>new Request("https://core.internal/core/ops/settings",{method:"POST",body:JSON.stringify(d)});
 assert.equal((await opsState(storage)).requestedMode,"manual");
 await storage.put("core:settings",plan);await storage.setAlarm(Date.now()+60000);
 const choose={action:"mode",mode:"autonomous",version:0,confirm:true};
 assert.equal((await core.fetch(req({...choose,confirm:false}))).status,400);
 assert.equal((await core.fetch(req({...choose,mode:"live"}))).status,400);
 assert.equal((await core.fetch(req(choose))).status,200);
 assert.equal((await core.fetch(req(choose))).status,409);
 const view=await core.view();assert.equal(view.requestedMode,"autonomous");assert.equal(view.mode,"dry_run");assert.equal(view.productionEnabled,false);assert.equal(view.rssEnabled,false);assert.equal(view.settings.paused,true);assert.equal(await storage.getAlarm(),null);
 assert.equal((await opsView(storage)).autonomousExecutionEnabled,false);
 await core.fetch(req({action:"stop",version:1,confirm:true}));
 assert.equal((await core.fetch(req({action:"mode",mode:"assisted",version:2,confirm:true}))).status,409);
 assert.equal((await core.fetch(req({action:"mode",mode:"manual",version:2,confirm:true}))).status,200);
 assert.equal((await opsState(storage)).stopped,true);
 assert.equal((await new CoreHandler({storage}).view()).requestedMode,"manual");
});

import {readMemory,writeMemory} from "./memory.mjs";
test("owner memory is bounded, confirmed, persistent and revisioned",async()=>{
 const map=new Map(),storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),transaction:async fn=>fn(storage)};
 const notes=[{kind:"style",text:"Живой русский язык"}],d={notes,version:0,confirm:true};
 assert.equal((await readMemory(storage)).version,0);
 for(const change of [{confirm:false},{notes:[{kind:"unknown",text:"x"}]},{notes:[{kind:"style",text:"x".repeat(241)}]},{notes:[{kind:"style",text:"api key: fixture"}]},{notes:Array(9).fill(notes[0])}])assert.equal((await writeMemory(storage,{...d,...change})).status,400);
 assert.equal((await writeMemory(storage,d)).status,200);
 assert.equal((await writeMemory(storage,d)).status,409);
 assert.deepEqual((await readMemory(storage)).notes,notes);
 assert.equal((await writeMemory(storage,{notes:[],version:1,confirm:true})).status,200);
 assert.deepEqual((await readMemory(storage)).notes,[]);
 assert.equal(JSON.stringify([...map.values()]).includes("Живой русский язык"),false);
});
test("memory API rejects non-owner and stores only confirmed owner notes",async()=>{
 const map=new Map(),storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),transaction:async fn=>fn(storage)};
 const h=new CoreHandler({storage});
 for(const path of ["/api/core/memory","/api/core/memory/settings"]){
 const req=new Request("https://example.test"+path,{method:"POST",body:JSON.stringify({initData:signed(124)})});
 assert.equal((await worker.fetch(req,env)).status,401);
 }
 const d={version:0,confirm:true,notes:[{kind:"illustrations",text:"Проверять соответствие смыслу"}]};
 const req=new Request("https://core.internal/core/memory/settings",{method:"POST",body:JSON.stringify(d)});
 assert.equal((await h.fetch(req)).status,200);
 const get=await h.fetch(new Request("https://core.internal/core/memory"));
 assert.deepEqual((await get.json()).memory.notes,d.notes);
});

import {diagnose} from "./diagnostics.mjs";
test("diagnostics distinguishes expected safety locks from missing timer and conflicting budgets",()=>{
 const now=Date.parse("2026-11-24T15:00:00Z"),budget={policy:{mainKop:30000},production:{ledgerConnected:false},chat:{accountedRub:10,limitRub:10,calls:20}};
 const d=diagnose({ops:{requestedMode:"autonomous"},settings:{paused:false},next:[{}],alarmAt:null,budget,readiness:{verified:true,configuredDailyRub:150}},now);
 const codes=d.findings.map(f=>f.code);
 for(const code of ["production_locked","mode_not_effective","alarm_missing","budget_mismatch","ledger_disconnected","chat_limit"])assert.ok(codes.includes(code));
 assert.equal(d.findings[0].severity,"critical");assert.equal(d.productionEnabled,false);assert.equal(d.automaticRecoveryEnabled,false);
 const paused=diagnose({ops:{stopped:true},settings:{paused:true},next:[{}],budget},now);
 assert.ok(!paused.findings.some(f=>f.code==="alarm_missing"));
});
test("diagnostics flags stale evidence and uncertain charges without guessing root cause",()=>{
 const now=Date.parse("2026-11-24T15:00:00Z");
 const d=diagnose({jobs:[{id:"j",preview:{},executorReceipt:{observedAt:now-400000,budget:{day:"2026-11-24",unsettledRequest:true,mainRemainingRub:"0",reserveRemainingRub:"0"},articles:[{id:"j/science/1",stages:[{id:"images",status:"failed"}]}]}},{id:"cancelled",status:"cancelled"},{id:"missing",preview:{}}],messages:[{id:"chat",status:"failed",cost:null}]},now);
 for(const code of ["receipt_stale","charge_unsettled","main_exhausted","reserve_exhausted","stage_failed","receipt_missing","chat_charge_unknown"])assert.ok(d.findings.some(f=>f.code===code));
 assert.equal(d.findings.find(f=>f.code==="stage_failed").evidence.stale,true);
 assert.ok(!d.findings.some(f=>f.scope==="cancelled"));
 assert.equal(d.publicationAuthorized,false);
});
test("diagnostics endpoint rejects non-owner before config or storage access",async()=>{
 const r=await worker.fetch(new Request("https://example.test/api/core/diagnostics",{method:"POST",body:JSON.stringify({initData:signed(124)})}),{...env,REVIEW_JOURNAL:{idFromName(){throw Error("must not access");}}});
 assert.equal(r.status,401);
});

test("unknown Qwen charge persists across Moscow midnight and prevents another paid request",async()=>{
 const map=new Map(),storage={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),delete:async k=>map.delete(k),list:async({prefix})=>new Map([...map].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),getAlarm:async()=>null};
 const core=new CoreHandler({storage}),agent=new AgentHandler({storage},{ROUTERAI_API_KEY:"fake"},core);
 const now=Date.parse("2026-11-24T20:59:00Z"),id="9b00a000-1111-4222-8333-444444444444";
 const orig=globalThis.fetch;let paid=0;globalThis.fetch=async(url)=>{if(url.endsWith("/models"))return Response.json({data:[priceRow]});if(url.endsWith("/credits"))return Response.json({data:{credits:100}});paid++;throw Error("fixture timeout");};
 try{
 const first=await agent.chat({id,text:"fixture",consent:true},now);assert.equal(first.body.message.status,"failed");
 assert.equal((await storage.get("agent:charge-hold")).status,"unknown");
 assert.equal((await agent.chat({id:"9b00a000-1111-4222-8333-555555555555",text:"next",consent:true},now+120000)).body.error,"charge_reconciliation_required");
 assert.equal(paid,1);assert.equal((await agent.view(now+120000)).enabled,false);
 }finally{globalThis.fetch=orig;}
});

test("confirmed jobs in assisted or autonomous mode automatically get intake preview only",async()=>{
 const map=new Map(),s={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v)};
 const p=validateJob({items:[{channel:"science",count:2}],day:null,instructions:"Synthetic fixture"});
 await s.put("ops:state",{requestedMode:"assisted"});
 const job=await saveJob(s,"agent:auto-intake",p,Date.now(),"agent");
 assert.equal(job.preview.executorMode,"semi_autonomous");assert.equal(job.preview.providerCalls,0);assert.equal(job.executed,false);
 await s.put("ops:state",{requestedMode:"autonomous"});
 assert.equal((await saveJob(s,job.id,p,Date.now(),"agent")).preview.executorMode,"semi_autonomous");
 const scheduled=await saveJob(s,"scheduled:auto-intake",p,Date.now(),"schedule");
 assert.equal(scheduled.preview.executorMode,"autonomous");assert.equal(scheduled.publicationAuthorized,false);
 const cinema=await saveJob(s,"agent:cinema-intake",{...p,items:[{channel:"cinema",count:1}]},Date.now(),"agent");assert.equal(cinema.preview.executorMode,"autonomous");assert.equal(cinema.preview.articles[0].channel,"cinema");
 await s.put("ops:state",{requestedMode:"manual"});
 assert.equal((await saveJob(s,"agent:manual-intake",p,Date.now(),"agent")).preview,undefined);
});

test("owner chat is blocked by production charge hold before network access",async()=>{
 const map=new Map([["account:production-hold",{id:"fixture",operation:"fixture/science/1/plan",startedAt:1}]]),storage={get:async k=>map.get(k),list:async()=>new Map()};
 const core=new CoreHandler({storage}),agent=new AgentHandler({storage},{ROUTERAI_API_KEY:"fake"},core);
 const orig=globalThis.fetch;globalThis.fetch=async()=>{throw Error("must not contact provider");};
 try{assert.equal((await agent.chat({id:"9b00a000-1111-4222-8333-444444444444",text:"fixture",consent:true})).body.error,"account_charge_hold");assert.equal((await agent.view()).enabled,false);}finally{globalThis.fetch=orig;}
});

test("release metadata is owner-only and explicitly prelaunch",async()=>{
 const req=id=>new Request("https://example.test/api/release",{method:"POST",body:JSON.stringify({initData:signed(id)})});
 assert.equal((await worker.fetch(req(124),env)).status,401);
 const r=await worker.fetch(req(123),env);assert.equal(r.status,200);const d=await r.json();
 assert.equal(d.version,"1.0.0-rc.2");assert.equal(d.productionEnabled,false);assert.equal(d.publicationAuthorized,false);assert.equal(d.rssEnabled,false);
});

test("responsive mode controls are available in both Core and Settings without bypassing confirmation",async()=>{
 const {HTML,CSS,JS}=await import("./ui.mjs");
 assert.match(HTML,/id="coremode"/);
 assert.match(CSS,/\.mode-option\[aria-pressed=true\]/);
 assert.match(CSS,/@media\(max-width:600px\)/);
 assert.match(JS,/renderModeControl\(section,s\)/);
 assert.match(JS,/renderModeControl\(direct,s\)/);
 assert.match(JS,/confirm:true/);
 assert.match(JS,/version:s.version/);
 assert.match(JS,/selected===saved/);
 assert.doesNotThrow(()=>new Function(JS));
});
