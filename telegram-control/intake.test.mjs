import {test} from "node:test";
import assert from "node:assert/strict";
import {bridgeRequest,exportIntent,intakeView,controlView,queueView} from "./intake.mjs";
const job={id:"agent:fixture",status:"blocked_prelaunch",executed:false,publicationAuthorized:false,preview:{mode:"dry_run"},plan:{items:[{channel:"legends",count:2},{channel:"science",count:1}],day:"2026-11-25",instructions:"Synthetic fixture only"},releaseAt:Date.parse("2026-11-25T09:00:00Z")};
test("maps exact PR85 contract and converts epoch milliseconds to ISO",()=>{assert.deepEqual(exportIntent(job),{id:job.id,plan:job.plan,releaseAt:"2026-11-25T09:00:00.000Z",mode:"manual"});for(const change of [{preview:null},{status:"cancelled"},{executed:true},{publicationAuthorized:true},{releaseAt:"bad"},{plan:{...job.plan,items:[{channel:"cinema",count:1}]}}])assert.equal(exportIntent({...job,...change}),null);});
test("intake only exports existing confirmed preview; pause prevents export",async()=>{const map=new Map([["production:job:"+job.id,job]]);const s={get:async k=>map.get(k),transaction:async fn=>fn(s)};const r=await intakeView(s,job.id);assert.equal(r.status,200);assert.equal(r.body.executionEnabled,false);assert.equal(r.body.publicationAuthorized,false);assert.equal(r.body.rssEnabled,false);assert.equal((await intakeView(s,"missing")).status,404);map.set("ops:state",{stopped:true});assert.equal((await intakeView(s,job.id)).status,409);});
test("dedicated bearer is mandatory before reaching storage",async()=>{const secret="a".repeat(64);let calls=0;const env={ZEN_CORE_INTAKE_SECRET:secret,REVIEW_JOURNAL:{idFromName:n=>n,get:()=>({fetch:async(url,opts)=>{calls++;assert.equal(JSON.parse(opts.body).id,job.id);return Response.json({mode:"intents_only"});}})}};const req=token=>new Request("https://fixture.invalid/internal/zen-core/intake",{method:"POST",headers:token?{Authorization:"Bearer "+token}:{},body:JSON.stringify({id:job.id})});for(const token of [undefined,"b".repeat(64),"short"])assert.equal((await bridgeRequest(req(token),env)).status,401);assert.equal(calls,0);assert.equal((await bridgeRequest(req(secret),{})).status,401);assert.equal((await bridgeRequest(req(secret),env)).status,200);assert.equal(calls,1);});

test("control reports pause, cancellation and changed mode without granting execution",async()=>{
 const j={...job,preview:{executorMode:"autonomous"}},m=new Map([["production:job:"+j.id,j],["ops:state",{version:1,requestedMode:"autonomous",stopped:false}]]);
 const s={get:async k=>m.get(k),transaction:async f=>f(s)};
 const first=await controlView(s,j.id);assert.equal(first.body.controlClear,true);assert.equal(first.body.executionEnabled,false);
 m.set("ops:state",{version:2,requestedMode:"manual",stopped:false});
 assert.equal((await controlView(s,j.id)).body.reason,"mode_changed");
 m.set("ops:state",{version:3,requestedMode:"autonomous",stopped:true});
 assert.equal((await controlView(s,j.id)).body.reason,"emergency_paused");
 m.set("ops:state",{version:4,requestedMode:"autonomous",stopped:false});m.set("production:job:"+j.id,{...j,status:"cancelled"});
 assert.equal((await controlView(s,j.id)).body.reason,"job_cancelled");
});
test("control endpoint rejects wrong bearer before touching storage",async()=>{
 const req=new Request("https://fixture.invalid/internal/zen-core/control",{method:"POST",body:JSON.stringify({id:job.id})});
 const r=await bridgeRequest(req,{ZEN_CORE_INTAKE_SECRET:"a".repeat(64),REVIEW_JOURNAL:{idFromName(){throw Error("unexpected storage");}}});assert.equal(r.status,401);
});

test("queue scans old and new jobs, excludes acknowledged, unconfirmed, cancelled and mismatched jobs",async()=>{
 const m=new Map(), add=(n,changes={})=>{const id="job:"+n;const key="production:index:"+String(n).padStart(15,"0")+":"+id;m.set(key,id);m.set("production:job:"+id,{...job,id,...changes});return key;};
 add(1);add(2,{preview:null});add(3,{status:"cancelled"});add(4,{executorReceipt:{}});add(5,{preview:{executorMode:"autonomous"}});add(6,{origin:"schedule"});
 for(let n=7;n<=20;n++)add(n,{status:"cancelled"});add(21);
 const s={get:async k=>m.get(k),transaction:async f=>f(s),list:async o=>new Map([...m].filter(([k])=>k.startsWith(o.prefix)&&(!o.startAfter||k>o.startAfter)).sort(([a],[b])=>a.localeCompare(b)).slice(0,o.limit))};
 const a=await queueView(s);assert.deepEqual(a.body.ids,["job:1"]);assert.ok(a.body.nextCursor);
 assert.deepEqual((await queueView(s,a.body.nextCursor)).body.ids,["job:21"]);
 assert.equal((await queueView(s,"bad")).status,400);
 m.set("ops:state",{stopped:true});assert.deepEqual((await queueView(s)).body.ids,[]);
});
