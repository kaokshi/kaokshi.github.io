import {test} from "node:test";
import assert from "node:assert/strict";
import {runProbe} from "./probe.mjs";
import {bridgeRequest} from "./intake.mjs";
const run="123456-1",id="probe:"+run,now=Date.now();
function fixture(){
 const m=new Map();
 const s={get:async k=>m.get(k),put:async(k,v)=>m.set(k,v),delete:async k=>m.delete(k),transaction:async f=>f(s),list:async o=>new Map([...m].filter(([k])=>k.startsWith(o.prefix)).sort(([a],[b])=>a.localeCompare(b)).slice(0,o.limit))};
 return {m,s};
}
test("isolated synthetic queue and receipt lifecycle, no paid actions",async()=>{
 const {m,s}=fixture();const call=(action,extra={})=>runProbe(s,{run,action,...extra},now);
 assert.equal((await call("start")).status,200);
 assert.equal((await call("start")).status,200);
 assert.deepEqual((await call("queue")).body.ids,[id]);
 const intake=(await call("intake")).body;
 assert.equal(intake.intent.id,id);assert.equal(intake.intent.plan.items.length,2);
 assert.equal((await call("control")).body.executionEnabled,false);
 const stages=["plan","draft","edit","visual_plan","images","qa","package"].map(id=>({id,status:"not_started"}));
 const day=new Date(now+10800000).toISOString().slice(0,10);
 const receipt={schema:"zen-core/receipt-v1",jobId:id,observedAt:now,modeRevision:0,effectiveMode:"manual",executionEnabled:false,publicationAuthorized:false,rssEnabled:false,articles:["legends","science"].map(c=>({id:id+"/"+c+"/1",meteredRub:"0",stages})),budget:{day,timezone:"Europe/Moscow",mainDailyRub:"300",reserveDailyRub:"50",mainRemainingRub:"300",reserveRemainingRub:"50",meteredRub:"0",unsettledRequest:false}};
 assert.equal((await call("receipt",{receipt:{...receipt,budget:{...receipt.budget,meteredRub:"1"}}})).status,400);
 assert.equal((await call("receipt",{receipt})).body.accepted,true);
 assert.equal((await call("receipt",{receipt})).body.replay,true);
 assert.deepEqual((await call("queue")).body.ids,[]);
 assert.equal((await call("verify")).body.articles,2);
 assert.equal((await runProbe(s,{run:"123456-2",action:"cleanup"},now)).status,409);
 assert.equal((await call("cleanup")).body.cleaned,true);assert.equal(m.size,0);
});
test("authenticated probe is routed only to its separate namespace",async()=>{
 const secret="a".repeat(64),names=[];
 const env={ZEN_CORE_INTAKE_SECRET:secret,REVIEW_JOURNAL:{idFromName:n=>{names.push(n);return n;},get:()=>({fetch:async(url,opts)=>{assert.equal(new URL(url).pathname,"/core/probe");assert.equal(JSON.parse(opts.body).run,run);return Response.json({ok:true});}})}};
 const req=(key,body)=>new Request("https://fixture.invalid/internal/zen-core/probe",{method:"POST",headers:key?{Authorization:"Bearer "+key}:{},body:JSON.stringify(body)});
 assert.equal((await bridgeRequest(req(null,{run,action:"start"}),env)).status,401);
 assert.equal((await bridgeRequest(req(secret,{run:"zen-core-scheduler-v1",action:"start"}),env)).status,400);
 assert.equal((await bridgeRequest(req(secret,{run,action:"start"}),env)).status,200);
 assert.deepEqual(names,["zen-core-probe-v1:"+run]);
});
