// Owner-confirmed stage intents; never an execution permit.
import {opsState} from "./ops.mjs";
import {exportIntent} from "./intake.mjs";
const fail=(status,error)=>({status,body:{error}});
export async function requestStage(storage,d,now=Date.now()){
 if(d?.confirm!==true||typeof d.id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(d.id)||typeof d.article!=="string"||typeof d.stage!=="string"||!Number.isSafeInteger(d.observedAt)||!Number.isSafeInteger(d.modeRevision)||!Number.isSafeInteger(d.version))return fail(400,"invalid_stage_request");
 return storage.transaction(async tx=>{
  const key="production:job:"+d.id,job=await tx.get(key),ops=await opsState(tx);
  const mode=ops.requestedMode==="assisted"?"semi_autonomous":ops.requestedMode;
  if(!exportIntent(job)||ops.stopped||ops.version!==d.version||d.modeRevision!==(ops.modeRevision??0)||job.origin==="schedule"&&(await tx.get("core:settings"))?.paused!==false)return fail(409,"stage_control_changed");
  const r=job.executorReceipt;
  if(!r||r.effectiveMode!==mode||r.modeRevision!==d.modeRevision||r.observedAt!==d.observedAt)return fail(409,"stage_receipt_changed");
  const article=r.articles.find(a=>a.id===d.article),next=article?.stages.find(s=>s.status!=="passed");
  if(!next||next.id!==d.stage||!(mode==="manual"||mode==="semi_autonomous"&&["images","package"].includes(d.stage)))return fail(409,"stage_not_available");
  const prior=job.stageIntent;
  const command={id:"stage:"+d.modeRevision+":"+d.article+":"+d.stage,article:d.article,stage:d.stage,mode,modeRevision:d.modeRevision};
  if(prior?.id===command.id)return {status:200,body:{intent:prior,replay:true,executionEnabled:false}};
  if(prior&&prior.modeRevision===d.modeRevision&&job.stageAcknowledgment?.id!==prior.id)return fail(409,"stage_intent_pending");
  const intent={...command,createdAt:now};
  await tx.put(key,{...job,stageIntent:intent});
  return {status:200,body:{intent,replay:false,executionEnabled:false}};
 });
}
export function pendingStage(job,ops){
 const c=job?.stageIntent;
 return c&&c.modeRevision===(ops.modeRevision??0)&&job.stageAcknowledgment?.id!==c.id?c:null;
}
