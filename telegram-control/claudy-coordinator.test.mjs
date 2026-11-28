import {test} from "node:test";
import assert from "node:assert/strict";
import {reserveClaudySlot,observeClaudyTerminal,readClaudyCoordinator} from "./claudy-coordinator.mjs";
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
