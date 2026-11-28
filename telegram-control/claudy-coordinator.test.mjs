import {test} from "node:test";
import assert from "node:assert/strict";
import {reserveClaudySlot,observeClaudyTerminal,readClaudyCoordinator,reserveClaudyShadowBudget,recordClaudyResponseEvidence} from "./claudy-coordinator.mjs";
function store(){
 const map=new Map();let tail=Promise.resolve();
 const tx={get:async k=>structuredClone(map.get(k)),put:async(k,v)=>{map.set(k,structuredClone(v));}};
 return {get:tx.get,transaction:f=>{const next=tail.then(()=>f(tx));tail=next.catch(()=>{});return next;},map};
}
test("parallel admissions preserve all reserved role capacity",async()=>{
 const s=store();
 const decisions=await Promise.all(Array.from({length:10},(_,i)=>reserveClaudySlot(s,{role:"production",id:"p"+i,now:100})));
 assert.equal(decisions.filter(x=>x.allowed).length,3);
 assert.equal((await reserveClaudySlot(s,{role:"chat",id:"c",now:100})).allowed,true);
 assert.equal((await reserveClaudySlot(s,{role:"development",id:"d",now:100})).allowed,true);
 assert.deepEqual(await readClaudyCoordinator(s),{active:5,roles:{production:3,chat:1,development:1},blockedUntil:0,liveEnabled:false});
});
test("terminal observations release slots but do not settle finance or permit replay",async()=>{
 const s=store();s.map.set("agent:charge-hold",{unknown:true});
 await reserveClaudySlot(s,{role:"chat",id:"c",now:100});
 assert.equal((await observeClaudyTerminal(s,{id:"c",terminal:false,now:200})).released,false);
 assert.equal((await readClaudyCoordinator(s)).active,1);
 assert.deepEqual(await observeClaudyTerminal(s,{id:"c",terminal:true,now:200,retryAfterSeconds:5}),{released:true,costVerified:false});
 assert.equal((await reserveClaudySlot(s,{role:"chat",id:"c",now:6000})).reason,"duplicate_operation");
 assert.equal((await reserveClaudySlot(s,{role:"development",id:"d",now:5199})).reason,"provider_backoff");
 assert.equal((await reserveClaudySlot(s,{role:"development",id:"d",now:5200})).allowed,true);
 assert.deepEqual(s.map.get("agent:charge-hold"),{unknown:true});
});

test("parallel shadow budget reservations cannot exceed shared balance or key cap",async()=>{
 const s=store(),preflight={budgetEligible:true,estimatedUpperRub:5};
 const status={status:"ok",observedAt:100,balanceRub:6,keyLimit:{consistent:true,leftRub:6},accountLimit:null};
 const attempts=await Promise.all(Array.from({length:5},(_,i)=>reserveClaudyShadowBudget(s,{role:["chat","production","development"][i%3],id:"p"+i,now:100,preflight,status})));
 assert.equal(attempts.filter(x=>x.reserved).length,1);
 assert.ok(attempts.every(x=>x.dispatchAuthorized===false));
 const first=s.map.get("telemetry:claudy:shadow-reservations:v1")[0];
 await reserveClaudySlot(s,{role:first.role,id:first.id,now:100});
 await observeClaudyTerminal(s,{id:first.id,terminal:true,now:200});
 assert.equal(s.map.get("telemetry:claudy:shadow-reservations:v1").length,1);
});
test("evidence survives malformed response and contains neither text nor credentials",async()=>{
 const s=store(),id="case1",response={id:"msg1",model:"unexpected",usage:{input_tokens:90000,output_tokens:"invalid"},content:[{text:"secret body"}]};
 assert.deepEqual(await recordClaudyResponseEvidence(s,{id,headers:new Headers({"X-Request-ID":"req1","Authorization":"secret key"}),response,now:100}),{recorded:true,costVerified:false});
 const evidence=s.map.get("telemetry:claudy:response-evidence:"+id);
 assert.equal(evidence.requestId,"req1");assert.equal(evidence.usage.input_tokens,90000);assert.equal(evidence.usage.output_tokens,null);
 assert.equal(JSON.stringify(evidence).includes("secret"),false);
 assert.equal((await recordClaudyResponseEvidence(s,{id,response,now:200})).recorded,false);
});
