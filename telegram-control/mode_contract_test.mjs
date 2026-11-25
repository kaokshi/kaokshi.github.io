// Cross-language three-mode handshake: actual JS state machine and Python SQLite executor.
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {CoreHandler} from "./core.mjs";
import {saveJob,validateJob,STAGES} from "./production.mjs";
import {previewJob} from "./adapter.mjs";
import {queueView,intakeView,controlView} from "./intake.mjs";
import {recordReceipt} from "./receipt.mjs";
import {opsState,updateOps} from "./ops.mjs";
import {requestStage} from "./stage-intent.mjs";
import {initial} from "./core.mjs";
const temp=mkdtempSync(join(tmpdir(),"zen-mode-"));
const db=join(temp,"mode.sqlite");
const id="agent:mode-fixture",now=Date.now();
const m=new Map();
const storage={get:async k=>m.get(k),put:async(k,v)=>m.set(k,v),delete:async k=>m.delete(k),transaction:async f=>f(storage),list:async o=>new Map([...m].filter(([k])=>k.startsWith(o.prefix)&&(!o.startAfter||k>o.startAfter)).sort(([a],[b])=>a.localeCompare(b)).slice(0,o.limit))};
const py=`import json,sys
sys.path.insert(0,"telegram-control")
from src.editorial_zen_core import Core
from intake_client import accept,build_receipt
c=Core(sys.argv[1])
try:
 d=json.load(sys.stdin)
 accept(c,d["envelope"],d["id"])
 if d["mark_plan"]:
  c.stage_result(d["id"]+"/science/1","plan",{"status":"passed"})
 print(json.dumps(build_receipt(c,d["id"],d["now"],command_id=(d["envelope"].get("stageIntent") or {}).get("id"))))
finally:
 c.db.close()
`;
function executor(envelope,mark_plan,t){
 const r=spawnSync("python",["-c",py,db],{input:JSON.stringify({id,envelope,mark_plan,now:t}),encoding:"utf8",timeout:15000});
 assert.equal(r.status,0,r.stderr);
 return JSON.parse(r.stdout);
}
try{
 await saveJob(storage,id,{items:[{channel:"science",count:1}],day:null,instructions:"Synthetic mode fixture"},now,"owner");
 assert.equal((await previewJob(storage,id,now,validateJob,STAGES)).status,200);
 const original=(await intakeView(storage,id)).body;
 const first=executor(original,true,now);
 assert.equal(first.effectiveMode,"manual");
 assert.equal((await recordReceipt(storage,first,now)).status,200);
 assert.equal((await controlView(storage,id,now)).body.controlClear,true);
 const request={id,article:id+"/science/1",stage:"draft",observedAt:now,modeRevision:0,version:0,confirm:true};
 assert.equal((await requestStage(storage,{...request,stage:"images"},now)).status,409);
 assert.equal((await requestStage(storage,{...request,confirm:false},now)).status,400);
 assert.equal((await requestStage(storage,request,now)).status,200);
 assert.equal((await requestStage(storage,request,now)).body.replay,true);
 assert.deepEqual((await queueView(storage)).body.ids,[id]);
 const cmdEnvelope=(await intakeView(storage,id)).body;
 const cmdReport=executor(cmdEnvelope,false,now+100);
 assert.equal(cmdReport.stageAcknowledgment.status,"recorded_only");
 assert.equal(cmdReport.articles[0].stages[1].status,"not_started");
 assert.equal((await recordReceipt(storage,{...cmdReport,stageAcknowledgment:{...cmdReport.stageAcknowledgment,id:"forged"}},now+100)).status,409);
 assert.equal((await recordReceipt(storage,cmdReport,now+100)).status,200);
 assert.deepEqual((await queueView(storage)).body.ids,[]);
 console.log("STAGE_INTENT_RECORDED_NO_GENERATION");
 let last=cmdReport;
 for(const [i,mode,effective] of [[1,"assisted","semi_autonomous"],[2,"autonomous","autonomous"],[3,"manual","manual"]]){
  const t=now+i*1000,old=await opsState(storage);
  assert.equal((await updateOps(storage,{action:"mode",mode,version:old.version,confirm:true},t,initial)).status,200);
  assert.equal((await storage.get("core:settings")).paused,true);
  assert.equal((await controlView(storage,id,t)).body.reason,"mode_sync_pending");
  assert.deepEqual((await queueView(storage)).body.ids,[id]);
  assert.equal((await recordReceipt(storage,last,t)).status,409);
  const envelope=(await intakeView(storage,id)).body;
  assert.deepEqual(envelope.intent,original.intent);
  const report=executor(envelope,false,t);
  assert.equal(report.effectiveMode,effective);
  assert.equal(report.modeRevision,i);
  assert.equal(report.articles[0].stages[0].status,"passed");
  assert.equal(report.budget.meteredRub,"0");
  assert.equal((await recordReceipt(storage,report,t)).status,200);
  assert.equal((await recordReceipt(storage,report,t)).body.replay,true);
  assert.deepEqual((await queueView(storage)).body.ids,[]);
  const ctl=(await controlView(storage,id,t)).body;
  assert.equal(ctl.controlClear,true);assert.equal(ctl.executionEnabled,false);
  assert.equal(ctl.rssEnabled,false);assert.equal(ctl.publicationAuthorized,false);
  last=report;
  console.log("MODE_SYNC_OK "+effective);
 }
 const old=await opsState(storage);
 assert.equal((await updateOps(storage,{action:"stop",version:old.version,confirm:true},now+4000,initial)).status,200);
 assert.equal((await controlView(storage,id,now+4000)).body.reason,"emergency_paused");
 assert.deepEqual((await queueView(storage)).body.ids,[]);
 assert.equal((await intakeView(storage,id)).status,409);
 assert.equal((await updateOps(storage,{action:"mode",mode:"autonomous",version:old.version+1,confirm:true},now+5000,initial)).status,409);
 assert.equal((await updateOps(storage,{action:"resume",version:old.version+1,confirm:true},now+5000,initial)).status,200);
 assert.equal((await storage.get("core:settings")).paused,true);
 console.log("MODE_PAUSE_AND_RESUME_OK_NO_GENERATION");
}finally{rmSync(temp,{recursive:true,force:true});}
