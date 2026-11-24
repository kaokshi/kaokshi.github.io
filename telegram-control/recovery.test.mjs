import test from "node:test";
import assert from "node:assert/strict";
import {recoverTimer} from "./recovery.mjs";
import {initial,nextSlots,CoreHandler} from "./core.mjs";
import worker from "./worker.mjs";
const now=Date.parse("2026-11-24T15:00:00Z");
function fixture({paused=false,stopped=false,alarm=null,cursor=now-600000}={}){
 const settings={...initial(),paused,version:2,channels:[{channel:"cinema",enabled:false,times:[]},{channel:"legends",enabled:true,times:["23:30"]},{channel:"science",enabled:false,times:[]}]};
 const map=new Map([["core:settings",settings],["ops:state",{stopped}],["core:cursor",cursor]]);let tail=Promise.resolve(),writes=0;
 const s={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),getAlarm:async()=>alarm,setAlarm:async n=>{alarm=n;writes++;},transaction:fn=>{const p=tail.then(()=>fn(s));tail=p.catch(()=>{});return p;},map,writes:()=>writes};return s;
}
const run=(s,t=now)=>recoverTimer(s,initial,nextSlots,t);
test("missing or overdue dry-run alarm is rearmed atomically without changing cursor",async()=>{
 for(const alarm of [null,now-600000]){
  const s=fixture({alarm});const [a,b]=await Promise.all([run(s),run(s)]);assert.equal(a.status,"rearmed");assert.equal(b.status,"healthy");assert.equal(s.writes(),1);assert.equal(await s.getAlarm(),now+1000);assert.equal(await s.get("core:cursor"),now-600000);assert.equal(a.productionEnabled,false);
 }
});
test("pause, healthy timer and missing/invalid cursor prevent recovery",async()=>{
 for(const options of [{paused:true},{stopped:true},{alarm:now+60000},{cursor:null},{cursor:now+1},{cursor:now-3*86400000}]){
  const s=fixture(options);assert.notEqual((await run(s)).status,"rearmed");assert.equal(s.writes(),0);
 }
});
test("watchdog preserves recovery cooldown and does not retry external operations",async()=>{
 const s=fixture();await run(s);await s.setAlarm(null);assert.equal((await run(s,now+1000)).reason,"cooldown");assert.equal(s.writes(),2);
});
test("scheduled worker calls only internal watchdog; public requests cannot invoke it",async()=>{
 let calls=0;const env={REVIEW_JOURNAL:{idFromName:n=>n,get:()=>({fetch:async(url,opts)=>{calls++;assert.equal(url,"https://core.internal/core/watchdog");assert.equal(opts.method,"POST");return Response.json({status:"healthy"});}})}};
 await worker.scheduled({},env);assert.equal(calls,1);
 const r=await worker.fetch(new Request("https://example.test/core/watchdog",{method:"POST",body:"{}"}),env);assert.equal(r.status,404);assert.equal(calls,1);
});
test("real internal core watchdog uses persisted state",async()=>{const s=fixture();const c=new CoreHandler({storage:s});const result=await c.watchdog(now);assert.equal(result.status,"rearmed");assert.equal(await s.getAlarm(),now+1000);});
