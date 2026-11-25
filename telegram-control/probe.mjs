// Isolated diagnostic namespace only. Never route a probe to the production scheduler.
import {saveJob,validateJob,STAGES} from "./production.mjs";
import {previewJob} from "./adapter.mjs";
import {queueView,intakeView,controlView} from "./intake.mjs";
import {recordReceipt} from "./receipt.mjs";
export const probeId=v=>typeof v==="string"&&/^[0-9]{1,20}-[0-9]{1,3}$/.test(v);
const fail=(status,error)=>({status,body:{error}});
export async function runProbe(storage,d,now=Date.now()){
 if(!probeId(d?.run)||!["start","queue","intake","control","receipt","verify","cleanup"].includes(d.action))return fail(400,"invalid_probe");
 const id="probe:"+d.run;
 const key="production:job:"+id;
 const marker=await storage.get("probe:marker");
 if(marker&&marker.run!==d.run)return fail(409,"probe_scope_mismatch");
 if(d.action==="start"){
  // Fixed synthetic fixture. Never accept caller-controlled text, date, stage or channel.
  await storage.transaction(async tx=>{
   const current=await tx.get("probe:marker");
   if(!current)await tx.put("probe:marker",{run:d.run,createdAt:now});
   await saveJob(tx,id,{items:[{channel:"legends",count:1},{channel:"science",count:1}],day:null,instructions:"Synthetic connection probe; no article generation"},current?.createdAt??now,"owner");
  });
  const p=await previewJob(storage,id,now,validateJob,STAGES);
  return p.status===200?{status:200,body:{id,mode:"intents_only",executionEnabled:false,publicationAuthorized:false,rssEnabled:false}}:p;
 }
 if(!marker)return fail(404,"probe_missing");
 if(d.action==="queue")return queueView(storage);
 if(d.action==="intake")return intakeView(storage,id);
 if(d.action==="control")return controlView(storage,id,now);
 if(d.action==="receipt"){
  const r=d.receipt;
  if(r?.jobId!==id||r?.articles?.some(a=>Number(a.meteredRub)!==0||a.stages?.some(s=>s.status!=="not_started"))||Number(r?.budget?.meteredRub)!==0||r?.budget?.unsettledRequest!==false)return fail(400,"probe_nonzero_or_executed");
  const result=await recordReceipt(storage,r,now);
  return {status:result.status,body:result.body}; // Never call Telegram notifications.
 }
 if(d.action==="verify"){
  const j=await storage.get(key);
  return {status:200,body:{accepted:Boolean(j?.executorReceipt),articles:j?.executorReceipt?.articles.length??0,executionEnabled:false,publicationAuthorized:false,rssEnabled:false}};
 }
 await storage.transaction(async tx=>{
  await tx.delete(key);
  await tx.delete("production:index:"+String(marker.createdAt).padStart(15,"0")+":"+id);
  await tx.delete("budget:executor-report");
  await tx.delete("probe:marker");
 });
 return {status:200,body:{cleaned:true}};
}
