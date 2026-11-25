import {test} from "node:test";
import assert from "node:assert/strict";
import {recordReceipt} from "./receipt.mjs";
import {STAGES} from "./production.mjs";
const now=1795536000000;
const job={id:"agent:fixture",status:"blocked_prelaunch",executed:false,publicationAuthorized:false,preview:{mode:"dry_run"},plan:{items:[{channel:"science",count:1}],day:null,instructions:"Fixture"},releaseAt:null};
const report={schema:"zen-core/receipt-v1",jobId:job.id,observedAt:now,executionEnabled:false,publicationAuthorized:false,rssEnabled:false,articles:[{id:job.id+"/science/1",meteredRub:"0",stages:STAGES.filter(s=>s.id!=="rss").map(s=>({id:s.id,status:"not_started"}))}],budget:{day:"2026-11-24",timezone:"Europe/Moscow",mainDailyRub:"300",reserveDailyRub:"50",mainRemainingRub:"300",reserveRemainingRub:"50",meteredRub:"0",unsettledRequest:false}};
function storage(){const m=new Map([["production:job:"+job.id,job]]);const s={get:async k=>m.get(k),put:async(k,v)=>m.set(k,v),transaction:async f=>f(s)};return s;}
test("receipt is informational and repeat-safe",async()=>{const s=storage();assert.equal((await recordReceipt(s,report,now)).status,200);assert.equal((await recordReceipt(s,report,now)).body.replay,true);const j=await s.get("production:job:"+job.id);assert.equal(j.executed,false);assert.equal(j.publicationAuthorized,false);assert.equal(j.executorReceipt.budget.mainRemainingRub,"300");assert.equal((await recordReceipt(s,{...report,observedAt:now-1},now)).status,409);});
test("reject unsafe, missing, stale or impossible stage reports",async()=>{const s=storage();for(const change of [{executionEnabled:true},{rssEnabled:true},{publicationAuthorized:true},{observedAt:now-400000},{articles:[]},{budget:{...report.budget,mainRemainingRub:"301"}},{articles:[{...report.articles[0],stages:report.articles[0].stages.map((st,i)=>({...st,status:i===2?"passed":"not_started"}))}]}])assert.equal((await recordReceipt(s,{...report,...change},now)).status,409);});
test("passed stage cannot regress or conflict on replay",async()=>{const s=storage();const r=structuredClone(report);r.articles[0].stages[0].status="passed";assert.equal((await recordReceipt(s,r,now)).status,200);assert.equal((await recordReceipt(s,report,now)).status,409);assert.equal((await recordReceipt(s,{...report,observedAt:now+1},now)).status,409);});

test("rolling budget report is bounded and latest snapshot does not rewind",async()=>{
 const s=storage(),budget={...report.budget,rollingStart:"2026-10-26",rollingCapRub:"9000",rollingRemainingRub:"8990",rollingMeteredRub:"10",rollingOverrun:false,overrun:false};
 assert.equal((await recordReceipt(s,{...report,budget},now)).status,200);
 assert.equal((await s.get("budget:executor-report")).budget.rollingRemainingRub,"8990");
 assert.equal((await recordReceipt(s,{...report,observedAt:now+1,budget:{...budget,rollingRemainingRub:"9001"}},now)).status,409);
});

test("recovery information is bounded and accepted illustrations never regress",async()=>{
 const s=storage(),r=structuredClone(report);
 r.articles[0].stages.slice(0,4).forEach(x=>x.status="passed");
 r.articles[0].stages[4]={id:"images",status:"blocked",reason:"cloud_storage_unavailable"};
 r.articles[0].acceptedImageSlots=[0,2,4];
 assert.equal((await recordReceipt(s,r,now)).status,200);
 const saved=(await s.get("production:job:"+job.id)).executorReceipt;
 assert.equal(saved.articles[0].stages[4].reason,"cloud_storage_unavailable");
 assert.deepEqual(saved.articles[0].acceptedImageSlots,[0,2,4]);
 for(const slots of [[0,2],[0,2,7],[0,0,2,4]]){const bad=structuredClone(r);bad.observedAt++;bad.articles[0].acceptedImageSlots=slots;assert.equal((await recordReceipt(s,bad,now)).status,409);}
 const bad=structuredClone(r);bad.observedAt++;bad.articles[0].stages[4].reason="private text / token";assert.equal((await recordReceipt(s,bad,now)).status,409);
 const good=structuredClone(r);good.observedAt++;good.articles[0].acceptedImageSlots=[0,1,2,4];assert.equal((await recordReceipt(s,good,now)).status,200);
});
