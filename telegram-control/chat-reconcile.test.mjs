// Synthetic fixtures only: no real chat text, generation ids or provider calls.
import {test} from "node:test";
import assert from "node:assert/strict";
import {AgentHandler,MODEL,dayKey} from "./agent.mjs";
import {CoreHandler} from "./core.mjs";
import {claimProduction} from "./account.mjs";

const ID="9b00a000-1111-4222-8333-444444444444",GEN="rai-synthetic-0001",START=Date.UTC(2026,10,28,9,0,0);
function fixture({ledger={reserved:2,calls:1,confirmedRub:0,opusAccountedRub:0},hold={},message={}}={}){
 const store=new Map();
 const storage={get:async k=>store.get(k),put:async(k,v)=>store.set(k,v),delete:async k=>store.delete(k),
  list:async({prefix})=>new Map([...store].filter(([k])=>k.startsWith(prefix))),transaction:async fn=>fn(storage),
  getAlarm:async()=>null,setAlarm:async()=>{},deleteAlarm:async()=>{}};
 const m={id:ID,text:"synthetic",status:"failed",cost:null,reserved:2,reply:"lost",proposal:null,...message};
 store.set("agent:charge-hold",{id:ID,startedAt:START,status:"unknown",billingLane:"shared",generationId:GEN,...hold});
 store.set("agent:generation:"+ID,{schema:"zen-core/provider-generation-v1",id:ID,model:MODEL,generationId:GEN,observedAt:START});
 store.set("agent:id:"+ID,m);
 store.set("agent:message:"+String(START).padStart(15,"0")+":"+ID,m);
 store.set("agent:budget:"+dayKey(START),ledger);
 return {store,storage,agent:new AgentHandler({storage},{ROUTERAI_API_KEY:"shared-key",ROUTERAI_CHAT_API_KEY:"chat-key"},new CoreHandler({storage}))};
}
async function withProvider(row,fn){
 const original=globalThis.fetch,calls=[];
 globalThis.fetch=async(url,opts)=>{calls.push({url,opts});
  if(row instanceof Error)throw row;
  return Response.json({data:row});};
 try{return await fn(calls);}finally{globalThis.fetch=original;}
}
const good={id:GEN,model:MODEL,total_cost:1.142495,created_at:"2026-11-28T09:00:04Z"};

test("a matching provider receipt settles the unknown chat charge and releases the hold", async()=>{
 const {store,storage,agent}=fixture();
 assert.equal((await claimProduction(storage,{id:"science",operation:"science/1/plan"},START)).status,409);
 assert.equal((await agent.view(START)).chargeHoldId,ID);
 await withProvider(good,async calls=>{
  const r=await agent.reconcile({id:ID,confirm:true},START+1);
  assert.equal(r.status,200);
  assert.deepEqual(r.body,{reconciled:true,costRub:1.142495,overrun:false,resent:false});
  assert.equal(calls.length,1,"one read-only receipt lookup, no resend");
  assert.equal(calls[0].opts.method,"GET");
  assert.ok(calls[0].url.endsWith("/generation?id="+GEN));
  assert.equal(calls[0].opts.headers.Authorization,"Bearer shared-key");
 });
 assert.equal(store.has("agent:charge-hold"),false);
 const ledger=store.get("agent:budget:"+dayKey(START));
 assert.ok(Math.abs(ledger.reserved-1.142495)<1e-9);
 assert.ok(Math.abs(ledger.confirmedRub-1.142495)<1e-9);
 const m=store.get("agent:id:"+ID);
 assert.equal(m.costVerified,true);assert.equal(m.status,"failed");assert.equal(m.cost,1.142495);
 assert.equal(store.get("agent:message:"+String(START).padStart(15,"0")+":"+ID).costVerified,true);
 assert.equal((await claimProduction(storage,{id:"science",operation:"science/1/plan"},START)).status,200);
 const view=await agent.view(START);
 assert.equal(view.chargeHold,false);assert.equal(view.chargeHoldId,null);
 assert.equal(JSON.stringify(view).includes(GEN),false,"generation id stays private");
});

test("the dedicated chat lane looks the receipt up with the dedicated key", async()=>{
 const {agent}=fixture({hold:{billingLane:"verified_dedicated_chat"}});
 await withProvider(good,async calls=>{
  assert.equal((await agent.reconcile({id:ID,confirm:true},START+1)).status,200);
  assert.equal(calls[0].opts.headers.Authorization,"Bearer chat-key");
 });
});

for(const [name,row,error] of [
 ["another generation",{...good,id:"rai-synthetic-9999"},"provider_receipt_unverified"],
 ["another model",{...good,model:"qwen/qwen3.6-plus"},"provider_receipt_unverified"],
 ["missing cost",{...good,total_cost:null},"provider_receipt_unverified"],
 ["negative cost",{...good,total_cost:-1},"provider_receipt_unverified"],
 ["provider unavailable",new Error("offline"),"provider_receipt_unavailable"]]){
 test("the hold stays closed on "+name, async()=>{
  const {store,agent}=fixture();
  await withProvider(row,async()=>{
   const r=await agent.reconcile({id:ID,confirm:true},START+1);
   assert.equal(r.body.error,error);
  });
  assert.equal(store.get("agent:charge-hold").status,"unknown");
  assert.equal(store.get("agent:budget:"+dayKey(START)).reserved,2);
 });
}

test("no confirmation, wrong id, pending hold or missing receipt make no provider call", async()=>{
 for(const [setup,d,error] of [
  [{},{id:ID},"invalid_confirmation"],
  [{},{id:"9b00a000-1111-4222-8333-000000000000",confirm:true},"charge_hold_mismatch"],
  [{hold:{status:"pending"}},{id:ID,confirm:true},"charge_hold_not_unknown"],
  [{hold:{generationId:"rai-other"}},{id:ID,confirm:true},"generation_receipt_missing"]]){
  const {store,agent}=fixture(setup);
  await withProvider(good,async calls=>{
   assert.equal((await agent.reconcile(d,START+1)).body.error,error);
   assert.equal(calls.length,0);
  });
  assert.equal(store.has("agent:charge-hold"),true);
 }
 const {store,agent}=fixture();store.delete("agent:generation:"+ID);
 await withProvider(good,async calls=>{
  assert.equal((await agent.reconcile({id:ID,confirm:true},START+1)).body.error,"generation_receipt_missing");
  assert.equal(calls.length,0);
 });
});

test("a confirmed overrun is recorded but keeps the day's chat budget blocked", async()=>{
 const {store,agent}=fixture({ledger:{reserved:10,calls:1,confirmedRub:0,opusAccountedRub:0}});
 await withProvider({...good,total_cost:3.5},async()=>{
  const r=await agent.reconcile({id:ID,confirm:true},START+1);
  assert.equal(r.status,200);assert.equal(r.body.overrun,true);
 });
 const ledger=store.get("agent:budget:"+dayKey(START));
 assert.equal(ledger.reserved,10);assert.equal(ledger.confirmedRub,3.5);
 assert.equal(store.get("agent:reconciled:"+ID).overrun,true);
});

test("a legacy ledger without confirmed spend stays unconfirmed", async()=>{
 const {store,agent}=fixture({ledger:{reserved:2,calls:3,confirmedRub:null}});
 await withProvider(good,async()=>assert.equal((await agent.reconcile({id:ID,confirm:true},START+1)).status,200));
 assert.equal(store.get("agent:budget:"+dayKey(START)).confirmedRub,null);
});

test("the route is reachable through the agent handler", async()=>{
 const {agent}=fixture();
 await withProvider(good,async()=>{
  const r=await agent.fetch(new Request("https://core.internal/core/agent/reconcile",{method:"POST",body:JSON.stringify({id:ID,confirm:true})}));
  assert.equal(r.status,200);assert.equal((await r.json()).reconciled,true);
 });
});
