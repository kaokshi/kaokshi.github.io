// Authenticated executor reports are informational; they never authorize work or release.
import {exportIntent} from "./intake.mjs";
import {STAGES} from "./production.mjs";
const stages=STAGES.filter(s=>s.id!=="rss").map(s=>s.id);
const statuses=new Set(["not_started","passed","failed","blocked"]);
const money=v=>typeof v==="string"&&/^\d{1,9}(?:\.\d{1,12})?$/.test(v)&&Number.isFinite(Number(v));
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
   clean.push({id:s.id,status:s.status});
  }
  articles.push({id,meteredRub:a.meteredRub,stages:clean});
 }
 const b=d.budget;
 if(!b||!/^\d{4}-\d{2}-\d{2}$/.test(b.day)||b.timezone!=="Europe/Moscow"||
 !["mainDailyRub","reserveDailyRub","mainRemainingRub","reserveRemainingRub","meteredRub"].every(k=>money(b[k]))||
 Number(b.mainDailyRub)!==300||Number(b.reserveDailyRub)!==50||
 Number(b.mainRemainingRub)>300||Number(b.reserveRemainingRub)>50||typeof b.unsettledRequest!=="boolean")return null;
 const budget=Object.fromEntries(["day","timezone","mainDailyRub","reserveDailyRub","mainRemainingRub","reserveRemainingRub","meteredRub","unsettledRequest"].map(k=>[k,b[k]]));
 return {schema:d.schema,jobId:d.jobId,observedAt:d.observedAt,articles,budget,source:"executor_report",executionEnabled:false,publicationAuthorized:false,rssEnabled:false};
}
export async function recordReceipt(storage,d,now=Date.now()){
 if(typeof d?.jobId!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(d.jobId))return bad(400,"invalid_job_id");
 return storage.transaction(async tx=>{
  const key="production:job:"+d.jobId,job=await tx.get(key);
  if(!job)return bad(404,"job_missing");
  const receipt=cleanReport(d,job,now);
  if(!receipt)return bad(409,"invalid_or_stale_receipt");
  const old=job.executorReceipt;
  if(old&&receipt.observedAt<old.observedAt)return bad(409,"stale_receipt");
  if(old&&receipt.observedAt===old.observedAt)return JSON.stringify(old)===JSON.stringify(receipt)?{status:200,body:{accepted:true,replay:true}}:bad(409,"receipt_conflict");
  if(old&&old.articles.some((a,i)=>a.stages.some((s,j)=>s.status==="passed"&&receipt.articles[i].stages[j].status!=="passed")))return bad(409,"passed_stage_regression");
  await tx.put(key,{...job,executorReceipt:receipt});
  return {status:200,body:{accepted:true,replay:false}};
 });
}
