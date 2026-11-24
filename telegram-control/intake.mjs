// Read-only bridge from confirmed Telegram jobs to the existing Python Core intake.
import {validateJob} from "./production.mjs";
import {opsState} from "./ops.mjs";
import {pendingStage} from "./stage-intent.mjs";
import {claimProduction,closeProduction,accountStatus} from "./account.mjs";
const encoder=new TextEncoder();
async function matches(secret,header){
 if(typeof secret!=="string"||! /^[a-fA-F0-9]{64}$/.test(secret)||typeof header!=="string"||!/^Bearer [a-fA-F0-9]{64}$/.test(header))return false;
 const key=await crypto.subtle.importKey("raw",encoder.encode(secret),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
 const a=new Uint8Array(await crypto.subtle.sign("HMAC",key,encoder.encode(header.slice(7))));
 const b=new Uint8Array(await crypto.subtle.sign("HMAC",key,encoder.encode(secret)));
 let diff=0;for(let i=0;i<a.length;i++)diff|=a[i]^b[i];return diff===0;
}
export function exportIntent(job){
 if(!job?.preview||job.status!=="blocked_prelaunch"||job.executed!==false||job.publicationAuthorized!==false||typeof job.id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(job.id))return null;
 const plan=validateJob(job.plan);
 if(!plan||plan.items.some(x=>x.channel==="cinema"))return null;
 let releaseAt=null;
 if(job.releaseAt!=null){if(!Number.isSafeInteger(job.releaseAt)||!Number.isFinite(new Date(job.releaseAt).getTime()))return null;releaseAt=new Date(job.releaseAt).toISOString();}
 const mode=job.preview.executorMode??"manual";
 if(!["manual","semi_autonomous","autonomous"].includes(mode))return null;
 return {id:job.id,plan,releaseAt,mode};
}
const modeFor=ops=>ops.requestedMode==="assisted"?"semi_autonomous":ops.requestedMode;
const transitionFor=ops=>Number.isSafeInteger(ops.modeRevision)&&ops.modeRevision>0?{mode:modeFor(ops),revision:ops.modeRevision}:null;
function deliverable(job,ops,settings){
 const intent=exportIntent(job);
 if(!intent||ops.stopped||(!transitionFor(ops)&&intent.mode!==modeFor(ops))||job.origin==="schedule"&&settings?.paused!==false)return null;
 return intent;
}
// Discover confirmed intents only. Pagination scans the durable index, not the UI's 40-row view.
export async function queueView(storage,cursor=null){
 if(cursor!==null&&(typeof cursor!=="string"||!/^production:index:\d{15}:[-a-zA-Z0-9:]{1,120}$/.test(cursor)))return {status:400,body:{error:"invalid_cursor"}};
 return storage.transaction(async tx=>{
  const ops=await opsState(tx),settings=await tx.get("core:settings");
  const base={schema:"zen-core/queue-v1",mode:"intents_only",executionEnabled:false,publicationAuthorized:false,rssEnabled:false,stopped:ops.stopped};
  if(ops.stopped)return {status:200,body:{...base,ids:[],nextCursor:null}};
  const rows=await tx.list({prefix:"production:index:",limit:20,...(cursor?{startAfter:cursor}:{})});
  const ids=[];
  for(const [,id] of rows){
   const job=await tx.get("production:job:"+id);
   const transition=transitionFor(ops);
   if(deliverable(job,ops,settings)&&(!job.executorReceipt||(transition&&job.executorReceipt.modeRevision!==transition.revision)||pendingStage(job,ops)))ids.push(id);
  }
  return {status:200,body:{...base,ids,nextCursor:rows.size===20?[...rows.keys()].at(-1):null}};
 });
}
export async function intakeView(storage,id){
 if(typeof id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(id))return {status:400,body:{error:"invalid_job_id"}};
 return storage.transaction(async tx=>{
  if((await tx.get("ops:state"))?.stopped===true)return {status:409,body:{error:"emergency_paused"}};
  const ops=await opsState(tx);
  const job=await tx.get("production:job:"+id),intent=deliverable(job,ops,await tx.get("core:settings"));
  return intent?{status:200,body:{schema:"zen-core/intake-v1",mode:"intents_only",intent,...(pendingStage(job,ops)?{stageIntent:pendingStage(job,ops)}:{}),...(transitionFor(ops)?{modeTransition:transitionFor(ops)}:{}),executionEnabled:false,publicationAuthorized:false,rssEnabled:false}}:{status:404,body:{error:"intent_not_available"}};
 });
}
// Fresh control observation, not an execution permit.
export async function controlView(storage,id,now=Date.now()){
 if(typeof id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(id))return {status:400,body:{error:"invalid_job_id"}};
 return storage.transaction(tx=>controlSnapshot(tx,id,now));
}
async function controlSnapshot(tx,id,now){
  const ops=await opsState(tx),job=await tx.get("production:job:"+id),intent=exportIntent(job);
  const mode=modeFor(ops),transition=transitionFor(ops),settings=await tx.get("core:settings");
  const effective=transition?job?.executorReceipt?.effectiveMode:intent?.mode;
  const policy=await tx.get("budget:policy");
  const mismatch=Boolean(policy&&(policy.mainKop!==30000||policy.reserveKop!==5000));
  const reason=ops.stopped?"emergency_paused":job?.status==="cancelled"?"job_cancelled":!intent?"job_unavailable":job.origin==="schedule"&&settings?.paused!==false?"schedule_paused":transition&&job.executorReceipt?.modeRevision!==transition.revision?"mode_sync_pending":effective!==mode?"mode_changed":mismatch?"budget_policy_mismatch":null;
  return {status:200,body:{schema:"zen-core/control-v1",jobId:id,observedAt:now,version:ops.version,mode,jobMode:effective??null,modeRevision:transition?.revision??0,stopped:ops.stopped,controlClear:reason===null,reason,executionEnabled:false,publicationAuthorized:false,rssEnabled:false}};
}
export async function accountView(storage,d,now=Date.now()){
 if(d?.action==="status")return storage.transaction(tx=>accountStatus(tx,now));
 if(!d||typeof d.id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(d.id)||!["claim","close"].includes(d.action))return {status:400,body:{error:"invalid_account_request"}};
 return storage.transaction(async tx=>{
  if(d.action==="close")return closeProduction(tx,d,now);
  const control=(await controlSnapshot(tx,d.id,now)).body;
  if(!control.controlClear||control.executionEnabled!==true)return {status:409,body:{error:"execution_disabled"}};
  return claimProduction(tx,d,now);
 });
}
export async function bridgeRequest(request,env){
 const path=new URL(request.url).pathname;
 if(!["/internal/zen-core/intake","/internal/zen-core/receipt","/internal/zen-core/control","/internal/zen-core/queue","/internal/zen-core/account","/internal/zen-core/probe"].includes(path))return null;
 const receipt=path.endsWith("/receipt"),route=path.split("/").pop();
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 if(request.method!=="POST")return Response.json({error:"method_not_allowed"},{status:405,headers});
 if(!await matches(env.ZEN_CORE_INTAKE_SECRET,request.headers.get("Authorization")))return Response.json({error:"unauthorized"},{status:401,headers});
 let d;try{const raw=await request.text();if(raw.length>(receipt||route==="probe"?16384:512))throw Error();d=JSON.parse(raw);}catch{return Response.json({error:"invalid_request"},{status:400,headers});}
 if(route==="probe"&&(typeof d?.run!=="string"||! /^[0-9]{1,20}-[0-9]{1,3}$/.test(d.run)))return Response.json({error:"invalid_probe"},{status:400,headers});
 const namespace=route==="probe"?"zen-core-probe-v1:"+d.run:"zen-core-scheduler-v1";
 const stub=env.REVIEW_JOURNAL?.get(env.REVIEW_JOURNAL.idFromName(namespace));
 if(!stub)return Response.json({error:"core_unavailable"},{status:503,headers});
 try{const r=await stub.fetch("https://core.internal/core/"+route,{method:"POST",body:JSON.stringify(receipt||route==="account"||route==="probe"?d:route==="queue"?{cursor:d?.cursor??null}:{id:d?.id})});return Response.json(await r.json(),{status:r.status,headers});}catch{return Response.json({error:"core_unavailable"},{status:503,headers});}
}
