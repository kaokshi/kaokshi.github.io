import test from "node:test";
import assert from "node:assert/strict";
import {reportEvents,notifyReceipt} from "./notifications.mjs";
const now=Date.parse("2026-11-24T15:00:00Z");
const base={jobId:"job-1",observedAt:now,budget:{day:"2026-11-24",mainRemainingRub:"300",reserveRemainingRub:"50",unsettledRequest:false},articles:[{id:"job-1/science/1",stages:[{id:"plan",status:"not_started"},{id:"draft",status:"not_started"}]}]};
const changed={...base,observedAt:now+1000,budget:{...base.budget,mainRemainingRub:"0",unsettledRequest:true},articles:[{...base.articles[0],stages:[{id:"plan",status:"passed"},{id:"draft",status:"failed"}]}]};
function storage(){const map=new Map();let pending=Promise.resolve();const s={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),transaction:fn=>{const p=pending.then(()=>fn(s));pending=p.catch(()=>{});return p;},map};return s;}
test("notify only changed stage statuses and budget threshold transitions",()=>{
 const events=reportEvents(base,changed);assert.equal(events.length,4);assert.deepEqual(reportEvents(changed,{...changed,observedAt:now+2000}),[]);
 assert.equal(reportEvents(changed,{...changed,budget:{...changed.budget,day:"2026-11-25",reserveRemainingRub:"0"}}).length,2);
});
test("executor notifications require owner opt-in, survive pause for alerts, deduplicate concurrent delivery",async()=>{
 const s=storage(),env={TELEGRAM_BOT_TOKEN:"fixture",TELEGRAM_OWNER_ID:"123"};let sends=0;const old=globalThis.fetch;
 globalThis.fetch=async(url,opts)=>{sends++;assert.match(url,/api\.telegram\.org/);const body=JSON.parse(opts.body);assert.equal(body.chat_id,"123");assert.match(body.text,/несверенное списание/);assert.match(body.text,/Публикация и платное исполнение не разрешены/);return Response.json({ok:true});};
 try{
  await notifyReceipt(s,env,changed,reportEvents(base,changed),now);assert.equal(sends,0);
  await s.put("ops:state",{notifications:true,stopped:true});
  await Promise.all([notifyReceipt(s,env,changed,reportEvents(base,changed),now+1000),notifyReceipt(s,env,changed,reportEvents(base,changed),now+2000)]);
  assert.equal(sends,1);assert.equal([...s.map.values()].find(x=>x.kind==="executor_report").status,"sent");
 }finally{globalThis.fetch=old;}
});
test("ambiguous Telegram delivery stays unconfirmed and is not retried",async()=>{
 const s=storage(),env={TELEGRAM_BOT_TOKEN:"fixture",TELEGRAM_OWNER_ID:"123"};let sends=0;const old=globalThis.fetch;
 globalThis.fetch=async()=>{sends++;throw Error("fixture timeout");};
 try{
  await s.put("ops:state",{notifications:true});
  await notifyReceipt(s,env,changed,reportEvents(base,changed),now);await notifyReceipt(s,env,changed,reportEvents(base,changed),now+2000);
  assert.equal(sends,1);assert.equal([...s.map.values()].find(x=>x.kind==="executor_report").status,"unconfirmed");
 }finally{globalThis.fetch=old;}
});
