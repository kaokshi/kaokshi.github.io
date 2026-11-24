// Offline adapter: build a deterministic, per-article execution plan only.
// No network, provider calls, article output, or publication authorization.
export function buildDryRun(job,validateJob,stages){
 const plan=validateJob(job?.plan);
 if(!plan||typeof job.id!=="string")return null;
 if(plan.items.some(item=>item.channel==="cinema"))return null;
 const graph=stages.filter(s=>s.id!=="rss");
 const seen=new Set();
 for(const s of graph){
  if(seen.has(s.id)||!s.requires.every(id=>seen.has(id)))return null;
  seen.add(s.id);
 }
 if(!seen.has("package"))return null;
 const articles=[];
 for(const item of plan.items)for(let n=1;n<=item.count;n++){
  const id=job.id+":"+item.channel+":"+n;
  articles.push({id,channel:item.channel,ordinal:n,tasks:graph.map(s=>({id:id+":"+s.id,stage:s.id,model:s.model,requires:s.requires.map(r=>id+":"+r),status:"planned_only"}))});
 }
 return {mode:"dry_run",articles,taskCount:articles.reduce((n,a)=>n+a.tasks.length,0),productionExecuted:false,publicationAuthorized:false,providerCalls:0};
}
export async function previewJob(storage,id,now,validateJob,stages){
 if(typeof id!=="string"||id.length>120||!/^[-a-zA-Z0-9:]+$/.test(id))return {status:400,body:{error:"invalid_job_id"}};
 return storage.transaction(async tx=>{
  if((await tx.get("ops:state"))?.stopped===true)return {status:409,body:{error:"emergency_paused"}};
  const key="production:job:"+id,job=await tx.get(key);
  if(!job)return {status:404,body:{error:"job_missing"}};
  if(job.status!=="blocked_prelaunch"||job.executed||job.publicationAuthorized)return {status:409,body:{error:"job_not_previewable"}};
  const plan=buildDryRun(job,validateJob,stages);
  if(!plan)return {status:409,body:{error:"invalid_or_paused_channel"}};
  if(job.preview)return {status:200,body:{preview:job.preview}};
  const requested=(await tx.get("ops:state"))?.requestedMode;
  const executorMode=requested==="assisted"?"semi_autonomous":requested==="autonomous"?"autonomous":"manual";
  const preview={...plan,executorMode,createdAt:new Date(now).toISOString()};
  await tx.put(key,{...job,preview});
  return {status:200,body:{preview}};
 });
}
