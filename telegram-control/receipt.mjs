// Authenticated executor reports are informational; they never authorize work or release.
import {exportIntent} from "./intake.mjs";
import {STAGES} from "./production.mjs";
import {reportEvents} from "./notifications.mjs";
import {checkReportAgainstPin} from "./settings-sync.mjs";
const stages=STAGES.filter(s=>s.id!=="rss").map(s=>s.id);
const statuses=new Set(["not_started","passed","failed","blocked"]);
const money=v=>typeof v==="string"&&/^\d{1,9}(?:\.\d{1,28})?$/.test(v)&&Number.isFinite(Number(v));
const bad=(status,error)=>({status,body:{error}});
function cleanReport(d,job,now){
 const intent=exportIntent(job);
 if(!intent||d?.schema!=="zen-core/receipt-v1"||d.jobId!==intent.id||
 d.executionEnabled!==false||d.publicationAuthorized!==false||d.rssEnabled!==false||
 !Number.isSafeInteger(d.observedAt)||d.observedAt>now+30000||d.observedAt<now-300000)return null;
 const expected=intent.plan.items.flatMap(i=>Array.from({length:i.count},(_,n)=>intent.id+"/"+i.channel+"/"+(n+1)));
 if(!Array.isArray(d.articles)||d.articles.length!==expected.length)return null;
 const articles=[];
 for(const id of expected){
  const matches=d.articles.filter(a=>a?.id===id);if(matches.length!==1)return null;
  const a=matches[0];if(!money(a.meteredRub)||!Array.isArray(a.stages)||a.stages.length!==stages.length)return null;
  const clean=[];
  for(let n=0;n<stages.length;n++){
   const s=a.stages[n];if(s?.id!==stages[n]||!statuses.has(s.status))return null;
   if(s.status!=="not_started"&&clean.some(p=>p.status!=="passed"))return null;
   if(s.reason!==undefined&&(typeof s.reason!=="string"||! /^[a-z0-9_]{1,120}$/.test(s.reason)||!["blocked","failed"].includes(s.status)))return null;
   clean.push({id:s.id,status:s.status,...(s.reason?{reason:s.reason}:{})});
  }
  const slots=a.acceptedImageSlots;
  if(slots!==undefined&&(!Array.isArray(slots)||slots.length>8||slots.some((x,i)=>!Number.isInteger(x)||x<0||x>7||i>0&&x<=slots[i-1])))return null;
  articles.push({id,meteredRub:a.meteredRub,stages:clean,...(slots!==undefined?{acceptedImageSlots:slots}:{})});
 }
 const b=d.budget;
 if(!b||!/^\d{4}-\d{2}-\d{2}$/.test(b.day)||b.timezone!=="Europe/Moscow"||
 !["mainDailyRub","reserveDailyRub","mainRemainingRub","reserveRemainingRub","meteredRub"].every(k=>money(b[k]))||
 Number(b.mainDailyRub)!==300||Number(b.reserveDailyRub)!==50||
 Number(b.mainRemainingRub)>300||Number(b.reserveRemainingRub)>50||typeof b.unsettledRequest!=="boolean")return null;
 if(d.modeRevision!==undefined&&(!Number.isSafeInteger(d.modeRevision)||d.modeRevision<0||!["manual","semi_autonomous","autonomous"].includes(d.effectiveMode)))return null;
 const rolling=["rollingStart","rollingCapRub","rollingRemainingRub","rollingMeteredRub","rollingOverrun","overrun"];
 if(rolling.some(k=>Object.hasOwn(b,k))){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(b.rollingStart)||![b.rollingCapRub,b.rollingRemainingRub,b.rollingMeteredRub].every(money)||Number(b.rollingCapRub)!==9000||Number(b.rollingRemainingRub)>9000||typeof b.rollingOverrun!=="boolean"||typeof b.overrun!=="boolean")return null;
 }
 if(d.dispatchReason!==undefined&&(typeof d.dispatchReason!=="string"||! /^[a-z0-9_]{1,120}$/.test(d.dispatchReason)))return null;
 const reasons=d.reserveReasons;
 if(reasons!==undefined&&(!Array.isArray(reasons)||reasons.length>12||
    new Set(reasons.map(x=>x?.reason)).size!==reasons.length||reasons.some(x=>!x||! /^[a-z0-9_]{1,80}$/.test(x.reason||"")||!money(x.rub)||Number(x.rub)>50)))return null;
 const ack=d.stageAcknowledgment;
 if(ack!==undefined&&(!ack||ack.status!=="recorded_only"||ack.id!==job.stageIntent?.id||ack.modeRevision!==job.stageIntent?.modeRevision||ack.modeRevision!==d.modeRevision))return null;
 const budget=Object.fromEntries(["day","timezone","mainDailyRub","reserveDailyRub","mainRemainingRub","reserveRemainingRub","meteredRub","unsettledRequest",...rolling.filter(k=>Object.hasOwn(b,k))].map(k=>[k,b[k]]));
 return {schema:d.schema,jobId:d.jobId,observedAt:d.observedAt,...(d.modeRevision!==undefined?{modeRevision:d.modeRevision,effectiveMode:d.effectiveMode}:{}),articles,budget,...(d.dispatchReason?{dispatchReason:d.dispatchReason}:{}),...(reasons?{reserveReasons:reasons.map(x=>({reason:x.reason,rub:x.rub}))}:{}),...(ack?{stageAcknowledgment:{id:ack.id,modeRevision:ack.modeRevision,status:ack.status}}:{}),source:"executor_report",executionEnabled:false,publicationAuthorized:false,rssEnabled:false};
}
export async function recordReceipt(storage,d,now=Date.now()){
 if(typeof d?.jobId!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(d.jobId))return bad(400,"invalid_job_id");
 return storage.transaction(async tx=>{
  const key="production:job:"+d.jobId,job=await tx.get(key);
  if(!job)return bad(404,"job_missing");
  const receipt=cleanReport(d,job,now);
  if(!receipt)return bad(409,"invalid_or_stale_receipt");
  if(job.settingsPin){
   const verified=await checkReportAgainstPin(tx,d.editorialSettingsReport,job.settingsPin,now);
   if(!verified.ok)return bad(409,verified.error);
   receipt.editorialSettingsReport={models:{revision:verified.acks.models.revision,hash:verified.acks.models.hash}};
  }else if(d.editorialSettingsReport!==undefined)return bad(409,"unexpected_settings_report");
  const old=job.executorReceipt;
  const ops=await tx.get("ops:state");
  if(Number.isSafeInteger(ops?.modeRevision)&&ops.modeRevision>0&&(receipt.modeRevision!==ops.modeRevision||receipt.effectiveMode!==(ops.requestedMode==="assisted"?"semi_autonomous":ops.requestedMode)))return bad(409,"mode_sync_pending");
  if(old&&(receipt.modeRevision??0)<(old.modeRevision??0))return bad(409,"stale_mode_receipt");
  if(old&&receipt.observedAt<old.observedAt)return bad(409,"stale_receipt");
  if(old&&receipt.observedAt===old.observedAt)return JSON.stringify(old)===JSON.stringify(receipt)?{status:200,body:{accepted:true,replay:true}}:bad(409,"receipt_conflict");
  if(old&&old.articles.some((a,i)=>a.stages.some((s,j)=>s.status==="passed"&&receipt.articles[i].stages[j].status!=="passed")))return bad(409,"passed_stage_regression");
  if(old&&old.articles.some((a,i)=>a.acceptedImageSlots?.some(s=>!receipt.articles[i].acceptedImageSlots?.includes(s))))return bad(409,"accepted_image_regression");
  await tx.put(key,{...job,executorReceipt:receipt,...(receipt.stageAcknowledgment?{stageAcknowledgment:receipt.stageAcknowledgment}:{})});
  const latest=await tx.get("budget:executor-report");
  if(!latest||receipt.observedAt>latest.observedAt)await tx.put("budget:executor-report",{observedAt:receipt.observedAt,budget:receipt.budget,reserveReasons:receipt.reserveReasons??[]});
  return {status:200,body:{accepted:true,replay:false},receipt,events:reportEvents(old,receipt)};
 });
}
