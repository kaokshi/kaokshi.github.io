// Synthetic fixtures only: fake keys, fake counters, fake replies. No network, no paid calls.
import {test} from "node:test";
import assert from "node:assert/strict";
import {AgentHandler,dayKey} from "./agent.mjs";
import {CoreHandler} from "./core.mjs";
import {claimProduction} from "./account.mjs";
import {writeAgentSettings,readAgentSettings,validateAgentSettings} from "./agent-settings.mjs";
import {claudyCounterSettlement,counterSnapshot,CLAUDY_IN_FLIGHT_MS} from "./claudy-chat.mjs";

const SONNET="anthropic/claude-sonnet-5.5";
const ENV={CLAUDY_SONNET_API_KEY:"synthetic-claudy-chat-key",ROUTERAI_API_KEY:"synthetic-router-key"};
const id=n=>"9b00a000-1111-4222-8333-"+String(n).padStart(12,"0");
function fixture(env=ENV){
 const store=new Map();
 const storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),delete:async k=>store.delete(k),
  list:async({prefix})=>new Map([...store].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),
  getAlarm:async()=>null,setAlarm:async()=>{},deleteAlarm:async()=>{}};
 const core=new CoreHandler({storage},env);
 return {store,storage,agent:new AgentHandler({storage},env,core)};
}
async function useClaudy(storage,extra={}){
 const r=await writeAgentSettings(storage,{confirm:true,version:(await readAgentSettings(storage)).version,provider:"claudy",model:SONNET,reasoning:"low",dailyRub:20,perCallRub:5,opusDailyRub:0,...extra});
 assert.equal(r.status,200);
}
const me=spent=>({balance_rub:900,key:{monthly_limit:1500,spent_this_month:spent,left:Math.max(0,1500-spent)}});
const reply={reply:"Синтетический ответ",proposal:null};
const message=(text=JSON.stringify(reply))=>({id:"msg_synthetic",model:"claude-sonnet-5-5",stop_reason:"end_turn",
 content:[{type:"text",text}],usage:{input_tokens:900,output_tokens:40,cache_read_input_tokens:0,cache_creation_input_tokens:0}});
// counters: spent values returned by successive /v1/me reads (null = failed read).
async function withClaudy({counters,send=()=>Response.json(message()),onSend=null},fn){
 const original=globalThis.fetch,calls={me:0,count:0,messages:0,router:0};
 globalThis.fetch=async(url,opts={})=>{
  url=String(url);
  if(url.startsWith("https://routerai.ru")){calls.router++;throw Error("router must not be called");}
  if(url.endsWith("/v1/me")){
   const v=counters[Math.min(calls.me,counters.length-1)];calls.me++;
   return v===null?new Response("{}",{status:502}):Response.json(me(v));
  }
  if(url.endsWith("/v1/messages/count_tokens")){calls.count++;return Response.json({input_tokens:1200});}
  if(url.endsWith("/v1/messages")){calls.messages++;if(onSend)await onSend();return send(opts);}
  throw Error("unexpected "+url);
 };
 try{return await fn(calls);}finally{globalThis.fetch=original;}
}
const ledgerOf=(store,now)=>store.get("agent:budget:"+dayKey(now));

test("settlement rule: settled only for a fresh, attributable delta within the bound",()=>{
 const t=Date.UTC(2026,10,28,9,0,0);
 const s=(spent,at=t)=>({status:"ok",observedAt:at,keyLimit:{limitRub:1500,spentRub:spent,leftRub:1500-spent,consistent:true}});
 assert.deepEqual(claudyCounterSettlement({before:s(10),after:s(10.4,t+5000),boundRub:1,responded:true,outputTokens:40}),
  {outcome:"settled",costRub:0.4,costVerified:true,source:"claudy_monthly_counter_delta"});
 assert.equal(claudyCounterSettlement({before:s(10),after:s(10,t+5000),boundRub:1,responded:true,outputTokens:0}).costRub,0);
 assert.deepEqual(claudyCounterSettlement({before:s(10),after:s(12.5,t+5000),boundRub:1,responded:true}),{outcome:"overrun",observedRub:2.5,costVerified:false});
 const reason=args=>claudyCounterSettlement({before:s(10),after:s(10.4,t+5000),boundRub:1,responded:true,outputTokens:40,...args}).reason;
 assert.equal(reason({after:{status:"unavailable",observedAt:t+5000}}),"counter_read_failed");
 assert.equal(reason({before:null}),"counter_read_failed");
 assert.equal(reason({after:s(10.4,t+200000)}),"counter_reads_stale");
 assert.equal(reason({after:s(9,t+5000)}),"counter_reset_or_adjustment");
 assert.equal(reason({after:{...s(10.4,t+5000),keyLimit:{limitRub:3000,spentRub:10.4,leftRub:2989.6,consistent:true}}}),"counter_reset_or_adjustment");
 assert.equal(reason({after:{...s(10.4,t+5000),keyLimit:{limitRub:1500,spentRub:10.4,leftRub:5,consistent:false}}}),"counter_read_failed");
 assert.equal(reason({overlap:true}),"overlapping_operation");
 assert.equal(reason({responded:false}),"transport_unconfirmed");
 assert.equal(reason({after:s(10,t+5000)}),"counter_not_updated");
 assert.equal(reason({boundRub:Number.NaN}),"bound_invalid");
 // Owner reconcile may use an older baseline, but never an unbounded or reset counter.
 assert.equal(claudyCounterSettlement({before:s(10),after:s(10.4,t+3600000),boundRub:1,responded:true,outputTokens:40,maxWindowMs:null}).outcome,"settled");
 assert.equal(counterSnapshot({status:"ok",observedAt:t,keyLimit:{limitRub:1,spentRub:Number.NaN,leftRub:1,consistent:true}}),null);
});

test("agent settings: RouterAI stays default, Claudy only serves the Claude models it maps",async()=>{
 const {storage}=fixture();
 assert.equal((await readAgentSettings(storage)).provider,"routerai");
 assert.equal(validateAgentSettings({provider:"claudy",model:"anthropic/claude-haiku-5.5",reasoning:"low",dailyRub:10,perCallRub:1}).error,"model_not_served_by_provider");
 assert.equal(validateAgentSettings({provider:"other",model:SONNET,reasoning:"low",dailyRub:10,perCallRub:1}).error,"unknown_agent_provider");
 await useClaudy(storage);
 // An older client that sends no provider keeps Claudy; an unserved model is refused.
 assert.equal((await writeAgentSettings(storage,{confirm:true,version:1,model:"anthropic/claude-opus-5.5",reasoning:"low",dailyRub:20,perCallRub:5})).status,200);
 assert.equal((await readAgentSettings(storage)).provider,"claudy");
 assert.equal((await writeAgentSettings(storage,{confirm:true,version:2,model:"qwen/qwen3.6-plus",reasoning:"low",dailyRub:20,perCallRub:5})).body.error,"model_not_served_by_provider");
 const view=await new CoreHandler({storage},ENV).fetch(new Request("https://core.internal/core/agent/settings"));
 const body=await view.json();
 assert.deepEqual(body.providers.map(p=>[p.id,p.configured]),[["routerai",true],["claudy",true]]);
 assert.deepEqual(body.providers[1].models,[SONNET,"anthropic/claude-opus-5.5"]);
});

test("Claudy chat settles from the counter delta and shows the answering provider",async()=>{
 const {store,storage,agent}=fixture();
 await useClaudy(storage);
 const now=Date.now();
 await withClaudy({counters:[10,10,10.37]},async calls=>{
  const r=await agent.chat({id:id(1),text:"Синтетический вопрос",consent:true},now);
  assert.equal(r.status,200);
  const m=r.body.message;
  assert.equal(m.status,"complete");assert.equal(m.reply,"Синтетический ответ");
  assert.equal(m.provider,"claudy");assert.equal(m.cost,0.37);assert.equal(m.costVerified,true);
  assert.equal(m.costSource,"claudy_monthly_counter_delta");
  assert.deepEqual([calls.messages,calls.router],[1,0]);
 });
 assert.equal(store.get("agent:charge-hold"),undefined);
 const ledger=ledgerOf(store,now);
 assert.ok(Math.abs(ledger.reserved-0.37)<1e-9);assert.equal(ledger.confirmedRub,0.37);assert.equal(ledger.calls,1);
 assert.equal(store.get("telemetry:claudy:response-evidence:"+id(1)).messageId,"msg_synthetic");
 const view=await agent.view(now);
 assert.equal(view.provider,"claudy");assert.equal(view.enabled,true);assert.equal(view.chargeHold,false);
});

test("a counter delta above the reserved bound is an overrun and blocks the rest of the day",async()=>{
 const {store,storage,agent}=fixture();
 await useClaudy(storage);
 const now=Date.now();
 await withClaudy({counters:[10,10,60]},async calls=>{
  const m=(await agent.chat({id:id(2),text:"Синтетический вопрос",consent:true},now)).body.message;
  assert.equal(m.status,"failed");assert.equal(m.costVerified,false);assert.equal(m.cost,50);
  assert.equal(m.costSource,"claudy_counter_overrun");assert.equal(m.proposal,null);
  assert.equal(calls.messages,1);
 });
 const ledger=ledgerOf(store,now);
 assert.ok(ledger.reserved>=20);assert.equal(ledger.confirmedRub,null);
 await withClaudy({counters:[60]},async calls=>{
  const next=await agent.chat({id:id(3),text:"Ещё",consent:true},now+1000);
  assert.equal(next.status,409);assert.equal(calls.messages,0);
 });
});

test("a failed counter read after the request keeps the hold unknown and the reservation",async()=>{
 const {store,storage,agent}=fixture();
 await useClaudy(storage);
 const now=Date.now();
 await withClaudy({counters:[10,10,null]},async calls=>{
  const m=(await agent.chat({id:id(4),text:"Синтетический вопрос",consent:true},now)).body.message;
  assert.equal(m.status,"failed");assert.equal(m.cost,null);assert.equal(m.costVerified,false);
  assert.equal(m.settlementReason,"counter_read_failed");assert.equal(calls.messages,1);
 });
 const hold=store.get("agent:charge-hold");
 assert.equal(hold.status,"unknown");assert.equal(hold.billingLane,"claudy_chat");assert.equal(hold.counterBefore.keyLimit.spentRub,10);
 assert.ok(ledgerOf(store,now).reserved>0);
 const view=await agent.view(now);
 assert.equal(view.chargeHoldId,id(4));assert.equal(view.chargeHoldProvider,"claudy");assert.equal(view.enabled,false);
 // A Claudy hold lives on another account and does not block RouterAI production claims.
 assert.equal((await claimProduction(storage,{id:"science",operation:"science/1/plan"},now)).status,200);
});

test("a failed counter read before the request means it is never sent and costs nothing",async()=>{
 const {store,storage,agent}=fixture();
 await useClaudy(storage);
 const now=Date.now();
 await withClaudy({counters:[10,null]},async calls=>{
  const m=(await agent.chat({id:id(5),text:"Синтетический вопрос",consent:true},now)).body.message;
  assert.equal(m.cost,0);assert.equal(m.costVerified,true);assert.equal(m.costSource,"not_sent");
  assert.equal(calls.messages,0);
 });
 assert.equal(store.get("agent:charge-hold"),undefined);
 assert.equal(ledgerOf(store,now).reserved,0);
});

test("another admission on the key during the request is overlapping spend and stays unknown",async()=>{
 const {store,storage,agent}=fixture();
 await useClaudy(storage);
 const now=Date.now();
 const onSend=async()=>{
  const state=store.get("telemetry:claudy:admission:v1");
  store.set("telemetry:claudy:admission:v1",{...state,starts:[...state.starts,Date.now()+1]});
 };
 await withClaudy({counters:[10,10,10.3],onSend},async()=>{
  const m=(await agent.chat({id:id(6),text:"Синтетический вопрос",consent:true},now)).body.message;
  assert.equal(m.status,"failed");assert.equal(m.settlementReason,"overlapping_operation");assert.equal(m.cost,null);
 });
 assert.equal(store.get("agent:charge-hold").status,"unknown");
});

test("a transport failure is never resent and stays unknown",async()=>{
 const {store,storage,agent}=fixture();
 await useClaudy(storage);
 const now=Date.now();
 await withClaudy({counters:[10,10,10.2],send:()=>{throw Error("synthetic network failure");}},async calls=>{
  const m=(await agent.chat({id:id(7),text:"Синтетический вопрос",consent:true},now)).body.message;
  assert.equal(m.settlementReason,"transport_unconfirmed");assert.equal(calls.messages,1);
 });
 assert.equal(store.get("agent:charge-hold").status,"unknown");
 assert.equal(store.get("telemetry:claudy:admission:v1").active.length,1,"the slot stays until the owner reconciles");
});

test("the per-call limit applies to Claudy exactly as to RouterAI",async()=>{
 const {storage,agent}=fixture();
 await useClaudy(storage,{perCallRub:0.01});
 await withClaudy({counters:[10]},async calls=>{
  const r=await agent.chat({id:id(8),text:"Синтетический вопрос",consent:true});
  assert.equal(r.status,409);assert.equal(r.body.error,"agent_call_exceeds_limit");assert.equal(calls.messages,0);
 });
});

test("the Opus daily limit applies to Claudy",async()=>{
 const {storage,agent}=fixture();
 await useClaudy(storage,{model:"anthropic/claude-opus-5.5",opusDailyRub:0});
 await withClaudy({counters:[10]},async calls=>{
  const r=await agent.chat({id:id(9),text:"Синтетический вопрос",consent:true});
  assert.equal(r.status,409);assert.equal(r.body.error,"budget_or_duplicate");assert.equal(calls.messages,0);
 });
});

test("without the Claudy chat key the provider is unavailable",async()=>{
 const {storage,agent}=fixture({ROUTERAI_API_KEY:"synthetic-router-key"});
 await useClaudy(storage);
 const r=await agent.chat({id:id(10),text:"Синтетический вопрос",consent:true});
 assert.equal(r.status,503);assert.equal(r.body.error,"claudy_unavailable");
 assert.equal((await agent.view()).enabled,false);
});

// Reconcile fixtures: an unknown Claudy hold with a persisted baseline.
function unknownHold(store,{dispatchAgo=CLAUDY_IN_FLIGHT_MS+5000,bound=1.2,extraStarts=[],baseline=10}={}){
 const start=Date.now()-dispatchAgo-1000,slotAt=start+10,hold=id(20);
 const m={id:hold,text:"synthetic",status:"failed",cost:null,reserved:bound,provider:"claudy",model:SONNET,reply:"lost",proposal:null};
 store.set("agent:charge-hold",{id:hold,startedAt:start,status:"unknown",billingLane:"claudy_chat",provider:"claudy",model:SONNET,
  counterBefore:{status:"ok",observedAt:slotAt,keyLimit:{limitRub:1500,spentRub:baseline,leftRub:1500-baseline,consistent:true}},
  boundRub:bound,slotAt,dispatchAt:Date.now()-dispatchAgo,outputTokens:40,reason:"counter_read_failed"});
 store.set("agent:id:"+hold,m);store.set("agent:message:"+String(start).padStart(15,"0")+":"+hold,m);
 store.set("agent:budget:"+dayKey(start),{reserved:bound,calls:1,confirmedRub:0,opusAccountedRub:0});
 store.set("telemetry:claudy:admission:v1",{starts:[slotAt,...extraStarts],active:[{id:hold,role:"chat"}],blockedUntil:0});
 store.set("telemetry:claudy:operation:"+hold,{role:"chat",status:"reserved",createdAt:slotAt});
 return {hold,start};
}

test("owner reconcile settles a Claudy hold only from a fresh, bounded, attributable counter read",async()=>{
 const {store,agent}=fixture();
 const {hold,start}=unknownHold(store);
 await withClaudy({counters:[10.45]},async calls=>{
  const r=await agent.reconcile({id:hold,confirm:true});
  assert.equal(r.status,200);
  assert.deepEqual(r.body,{reconciled:true,costRub:0.45,overrun:false,provider:"claudy",source:"claudy_monthly_counter_delta",resent:false});
  assert.deepEqual([calls.me,calls.messages],[1,0]);
 });
 assert.equal(store.get("agent:charge-hold"),undefined);
 const ledger=store.get("agent:budget:"+dayKey(start));
 assert.ok(Math.abs(ledger.reserved-0.45)<1e-9);assert.equal(ledger.confirmedRub,0.45);
 assert.equal(store.get("agent:id:"+hold).costVerified,true);
 assert.equal(store.get("telemetry:claudy:admission:v1").active.length,0);
});

test("owner reconcile refuses when the counter cannot prove that no other spend happened",async()=>{
 const cases=[
  [{counters:[13]},{},"claudy_counter_exceeds_bound"],
  [{counters:[null]},{},"claudy_counter_unverified"],
  [{counters:[9]},{},"claudy_counter_unverified"],
  [{counters:[10]},{},"claudy_counter_unverified"],
  [{counters:[10.2]},{extraStarts:[Date.now()]},"claudy_counter_unverified"],
  [{counters:[10.2]},{dispatchAgo:1000},"claudy_request_may_be_in_flight"],
 ];
 for(const [provider,holdOptions,error] of cases){
  const {store,agent}=fixture();
  const {hold}=unknownHold(store,holdOptions);
  await withClaudy(provider,async calls=>{
   const r=await agent.reconcile({id:hold,confirm:true});
   assert.equal(r.body.error,error,JSON.stringify(provider)+JSON.stringify(holdOptions));
   assert.equal(calls.messages,0);
  });
  assert.equal(store.get("agent:charge-hold").status,"unknown");
 }
});

test("owner reconcile releases a Claudy hold that was never dispatched at zero cost",async()=>{
 const {store,agent}=fixture();
 const {hold}=unknownHold(store);
 const h=store.get("agent:charge-hold");delete h.counterBefore;delete h.dispatchAt;
 await withClaudy({counters:[10]},async calls=>{
  const r=await agent.reconcile({id:hold,confirm:true});
  assert.equal(r.status,200);assert.equal(r.body.costRub,0);assert.equal(r.body.source,"not_sent");assert.equal(calls.me,0);
 });
 assert.equal(store.get("agent:charge-hold"),undefined);
});

test("the RouterAI default path is unchanged and never touches Claudy",async()=>{
 const {store,agent}=fixture();
 const original=globalThis.fetch,urls=[];
 globalThis.fetch=async(url,opts)=>{
  urls.push(String(url));
  if(String(url).includes("claudy"))throw Error("claudy must not be called");
  if(url.endsWith("/models"))return Response.json({data:[{id:SONNET,pricing_units:{prompt:"token",completion:"token"},pricing:{prompt:"0.00001",completion:"0.00004"}}]});
  if(url.endsWith("/credits"))return Response.json({data:{credits:100}});
  const body=JSON.parse(opts.body);
  assert.equal(body.max_tokens,700);
  return Response.json({model:SONNET,usage:{cost:0.02},choices:[{finish_reason:"stop",message:{content:JSON.stringify(reply)}}]});
 };
 try{
  const m=(await agent.chat({id:id(30),text:"Синтетический вопрос",consent:true})).body.message;
  assert.equal(m.status,"complete");assert.equal(m.cost,0.02);assert.equal(m.provider,undefined);
 }finally{globalThis.fetch=original;}
 assert.ok(urls.every(u=>u.startsWith("https://routerai.ru")));
 assert.equal(store.get("agent:charge-hold"),undefined);
 assert.equal((await agent.view()).provider,"routerai");
});
