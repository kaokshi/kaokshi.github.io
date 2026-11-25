import {test} from "node:test";
import assert from "node:assert/strict";
import {buildDryRun,previewJob} from "./adapter.mjs";
// Synthetic input; test the adapter independently of external services.
const stages=[{id:"plan",model:"fixture",requires:[]},{id:"draft",model:"fixture",requires:["plan"]},{id:"package",model:null,requires:["draft"]},{id:"rss",model:null,requires:["package"]}];
const validateJob=p=>p&&Array.isArray(p.items)&&p.items.every(i=>Number.isInteger(i.count)&&i.count>0&&i.count<=9)?p:null;
const job={id:"agent:test",plan:{items:[{channel:"legends",count:2},{channel:"science",count:1}]},status:"blocked_prelaunch",executed:false,publicationAuthorized:false};
function store(initial=job){const map=new Map([["production:job:"+job.id,initial]]);let tail=Promise.resolve();const s={get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),transaction:fn=>{const next=tail.then(()=>fn(s));tail=next.catch(()=>{});return next;}};return s;}
const run=(s,id=job.id,now=0)=>previewJob(s,id,now,validateJob,stages);
test("unique article tasks and dependencies; no release or calls",()=>{const p=buildDryRun(job,validateJob,stages);assert.equal(p.articles.length,3);assert.equal(p.taskCount,9);assert.equal(new Set(p.articles.flatMap(a=>a.tasks.map(t=>t.id))).size,9);assert.ok(p.articles.every(a=>a.tasks[1].requires[0]===a.tasks[0].id));assert.ok(p.articles.every(a=>a.tasks.every(t=>t.stage!=="rss"&&t.status==="planned_only")));assert.equal(p.providerCalls,0);assert.equal(p.productionExecuted,false);assert.equal(p.publicationAuthorized,false);});
test("cinema enters the same queue and invalid graphs are rejected",()=>{assert.equal(buildDryRun({...job,plan:{items:[{channel:"cinema",count:1}]}},validateJob,stages).articles[0].channel,"cinema");assert.equal(buildDryRun(job,validateJob,[stages[1],stages[2]]),null);});
test("concurrent previews idempotent, status unchanged",async()=>{const s=store();const [a,b]=await Promise.all([run(s),run(s,job.id,1000)]);assert.equal(a.status,200);assert.deepEqual(a,b);const saved=await s.get("production:job:"+job.id);assert.equal(saved.status,"blocked_prelaunch");assert.equal(saved.executed,false);assert.deepEqual(saved.preview,a.body.preview);});
test("stop, cancellation and missing job fail closed",async()=>{const s=store();await s.put("ops:state",{stopped:true});assert.equal((await run(s)).body.error,"emergency_paused");await s.put("ops:state",{stopped:false});await s.put("production:job:"+job.id,{...job,status:"cancelled"});assert.equal((await run(s)).body.error,"job_not_previewable");assert.equal((await run(s,"agent:missing")).status,404);assert.equal((await run(s,"../bad")).status,400);});

test("production readiness uses the current config while execution stays guarded", async t => {
 const {productionReadiness}=await import("./production.mjs");
 const configs={
  "site.json":{publication_enabled:false,auto_release_active_channels:["legends","science"]},
  "ai_revision_policy.json":{future_rss_delivery:{project_launched:false}},
  "zen_core.json":{version:1,main_daily_rub:"300",rolling_30_day_cap_rub:"9000",execution_enabled:false,publication_enabled:false,rss_enabled:false}
 };
 const fetchMock=t.mock.method(globalThis,"fetch",async url=>{
  const name=new URL(url).pathname.split("/").at(-1);
  assert.ok(Object.hasOwn(configs,name),name);
  return Response.json(configs[name]);
 });
 const readiness=await productionReadiness();
 assert.equal(fetchMock.mock.callCount(),3);
 assert.equal(readiness.verified,true);
 assert.equal(readiness.adapterConnected,true);
 assert.equal(readiness.configuredDailyRub,300);
 assert.equal(readiness.privateExecutionConfigured,false);
 assert.equal(readiness.canExecute,false);
});
