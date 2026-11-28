import {test} from "node:test";
import assert from "node:assert/strict";
import {developerView,developerAction} from "./developer.mjs";
function storage(){const m=new Map();let tail=Promise.resolve();const tx={get:async k=>structuredClone(m.get(k)),put:async(k,v)=>m.set(k,structuredClone(v)),list:async({prefix})=>new Map([...m].filter(([k])=>k.startsWith(prefix)))};return {...tx,transaction:f=>{const p=tail.then(()=>f(tx));tail=p.catch(()=>{});return p;}};}
const task={action:"create",id:"12345678-1234-4234-8234-123456789abc",text:"Проверить код",reasoning:"high",budget:{limitKop:1000},version:0,confirm:true};
test("developer task is owner-confirmed, idempotent and never executes",async()=>{
 const s=storage();
 assert.equal((await developerAction(s,{...task,confirm:false})).status,400);
 assert.equal((await developerAction(s,{...task,budget:{limitKop:1001}})).status,409);
 const r=await developerAction(s,task,100);
 assert.equal(r.task.status,"queued_blocked");assert.equal(r.task.executed,false);
 assert.deepEqual(await developerAction(s,task,101),r);
 assert.equal((await developerAction(s,{...task,text:"other"})).status,409);
 const v=await developerView(s);assert.equal(v.executionEnabled,false);assert.equal(v.paidDispatchAuthorized,false);assert.equal(v.tasks.length,1);
 assert.equal((await developerAction(s,{action:"cancel",id:task.id,confirm:true})).task.status,"cancelled");
});
test("developer limits are versioned and bounded",async()=>{
 const s=storage(),d={action:"policy",confirm:true,version:0,settings:{perTaskKop:500,dailyKop:2000,monthlyKop:150000}};
 assert.equal((await developerAction(s,{...d,settings:{...d.settings,monthlyKop:150001}})).status,400);
 assert.equal((await developerAction(s,d)).policy.version,1);
 assert.equal((await developerAction(s,d)).status,409);
 assert.equal((await developerAction(s,{...task,version:1})).status,409);
});
