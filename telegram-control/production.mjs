import {buildDryRun} from "./adapter.mjs";
// Durable production intents. No model inference, GitHub dispatch or publishing.
export const STAGES=[
 {id:"plan",model:"qwen/qwen3.6-plus",role:"Темы, источники и задания",requires:[]},
 {id:"draft",model:"anthropic/claude-sonnet-5.5",role:"Автор",requires:["plan"]},
 {id:"edit",model:"deepseek/deepseek-v4-pro-0813",role:"Независимый редактор",requires:["draft"]},
 {id:"visual_plan",model:"qwen/qwen3.6-plus",role:"План иллюстраций",requires:["edit"]},
 {id:"images",model:"bytedance-seed/seedream-5-0-flash",role:"Иллюстрации и проверенные источники",requires:["visual_plan"]},
 {id:"qa",model:null,role:"Независимые проверки фактов, текста, изображений и повторов",requires:["edit","images"]},
 {id:"package",model:null,role:"Приватная сборка и фиксация версии",requires:["qa"]},
 {id:"rss",model:null,role:"RSS только после запуска и всех проверок",requires:["package"]}
];
const channels=["cinema","legends","science"];
export function validateJob(value){
 if(!value||!Array.isArray(value.items)||value.items.length<1||value.items.length>3||typeof value.instructions!=="string"||value.instructions.length>1000)return null;
 if(value.day!==null&&(typeof value.day!=="string"||!/^\d{4}-\d{2}-\d{2}$/.test(value.day)||!Number.isFinite(Date.parse(value.day+"T00:00:00Z"))||new Date(value.day+"T00:00:00Z").toISOString().slice(0,10)!==value.day))return null;
 const items=[];
 for(const row of value.items){if(!channels.includes(row?.channel)||items.some(x=>x.channel===row.channel)||!Number.isInteger(row.count)||row.count<1||row.count>10)return null;items.push({channel:row.channel,count:row.count});}
 if(items.reduce((n,x)=>n+x.count,0)>9)return null;
 return {items,day:value.day,instructions:value.instructions.trim()};
}
export function makeJob(id,plan,now,origin="owner",releaseAt=null){
 return {id,origin,createdAt:new Date(now).toISOString(),plan,releaseAt,status:"blocked_prelaunch",executed:false,publicationAuthorized:false,reason:"validated_routerai_production_adapter_pending",stages:STAGES.map(s=>({...s,status:"not_started"}))};
}
export async function saveJob(tx,id,plan,now,origin="owner",releaseAt=null){
 const key="production:job:"+id;
 const old=await tx.get(key);
 if(old)return JSON.stringify(old.plan)===JSON.stringify(plan)?old:null;
 const job=makeJob(id,plan,now,origin,releaseAt);
 // An owner-confirmed agent order or enabled schedule can prepare an intake-only preview automatically.
 const mode=(await tx.get("ops:state"))?.requestedMode;
 if(["agent","schedule"].includes(origin)&&["assisted","autonomous"].includes(mode)){
  const preview=buildDryRun(job,validateJob,STAGES);
  if(preview)job.preview={...preview,executorMode:mode==="assisted"?"semi_autonomous":"autonomous",createdAt:job.createdAt};
 }
 await tx.put(key,job);
 await tx.put("production:index:"+String(now).padStart(15,"0")+":"+id,id);
 return job;
}
export async function jobsView(storage){
 const rows=await storage.list({prefix:"production:index:",reverse:true,limit:40});
 const jobs=await Promise.all([...rows.values()].map(id=>storage.get("production:job:"+id)));
 return {jobs:jobs.filter(Boolean),productionEnabled:false,executionMode:"intents_only",stages:STAGES};
}
export async function cancelJob(storage,id){
 if(typeof id!=="string"||id.length>120||!/^[-a-zA-Z0-9:]+$/.test(id))return null;
 return storage.transaction(async tx=>{
  const key="production:job:"+id,job=await tx.get(key);
  if(!job||job.executed||!["blocked_prelaunch","cancelled"].includes(job.status))return null;
  if(job.status==="cancelled")return job;
  const next={...job,status:"cancelled",cancelledAt:new Date().toISOString()};await tx.put(key,next);return next;
 });
}
export async function productionReadiness(){
 const root="https://raw.githubusercontent.com/kaokshi/kaokshi.github.io/main/config/";
 const read=async file=>{const r=await fetch(root+file,{redirect:"manual",signal:AbortSignal.timeout(5000),cache:"no-store"});if(!r.ok)throw Error();return r.json();};
 try{
  const [site,policy,core]=await Promise.all([read("site.json"),read("ai_revision_policy.json"),
    read("zen_core.json").catch(()=>null)]);
  return {
   verified:true,
   publicationEnabled:site.publication_enabled===true,
   projectLaunched:policy.future_rss_delivery?.project_launched===true,
   activeChannels:(Array.isArray(core?.production_channels)?core.production_channels:[]).filter(x=>channels.includes(x)),
   configuredDailyRub:core?Number(core.main_daily_rub):null,
   configuredMonthlyRub:core?Number(core.rolling_30_day_cap_rub):null,
   adapterConnected:core?.version===1,
   privateExecutionConfigured:core?.execution_enabled===true&&core?.publication_enabled===false&&core?.rss_enabled===false,
   sharedSpendGuardVerified:false,
   canExecute:false
  };
 }catch{return {verified:false,adapterConnected:false,sharedSpendGuardVerified:false,canExecute:false};}
}
