import {statsView,importStats} from "./statistics.mjs";
import {opsState,opsView,updateOps,notifyTimer} from "./ops.mjs";
import {readBudget,writeBudget} from "./budget.mjs";
import {saveJob,jobsView,cancelJob,productionReadiness,validateJob,STAGES} from "./production.mjs";
import {previewJob} from "./adapter.mjs";
import {intakeView,controlView,queueView,accountView} from "./intake.mjs";
import {recordReceipt} from "./receipt.mjs";
import {runProbe} from "./probe.mjs";
import {requestStage} from "./stage-intent.mjs";
import {notifyReceipt} from "./notifications.mjs";
import {readMemory,writeMemory} from "./memory.mjs";
import {diagnose} from "./diagnostics.mjs";
import {recoverTimer,recoverInterrupted} from "./recovery.mjs";
// ZEN CORE v0.1: persistent owner plan, deterministic cloud scheduler, dry-run only.
// No calls to RouterAI, editorial workflows, or publishing systems.
export const CHANNELS=["cinema","legends","science"];
const MIN=60000,DAY=86400000,MSK=3*60*MIN;
export const initial=()=>({version:0,timezone:"Europe/Moscow",paused:true,leadMinutes:360,channels:CHANNELS.map(channel=>({channel,enabled:false,times:[]})),updatedAt:null});
export function validateSchedule(value){
 if(!value||!Array.isArray(value.channels)||value.channels.length!==3||value.timezone!=="Europe/Moscow"||typeof value.paused!=="boolean"||!Number.isInteger(value.leadMinutes)||value.leadMinutes<60||value.leadMinutes>1440)return null;
 const rows=[];
 for(const id of CHANNELS){
  const matches=value.channels.filter(r=>r?.channel===id);if(matches.length!==1)return null;
  const r=matches[0];if(typeof r.enabled!=="boolean"||!Array.isArray(r.times)||r.times.length>10||r.times.some(t=>typeof t!=="string"||!/^([01]\d|2[0-3]):[0-5]\d$/.test(t))||new Set(r.times).size!==r.times.length||r.enabled&&!r.times.length)return null;
  rows.push({channel:id,enabled:r.enabled,times:[...r.times].sort()});
 }
 if(rows.reduce((sum,r)=>sum+(r.enabled?r.times.length:0),0)>30)return null;
 return {timezone:"Europe/Moscow",paused:value.paused,leadMinutes:value.leadMinutes,channels:rows};
}
export function slots(settings,now){
 if(settings.paused)return [];
 const day=Math.floor((now+MSK)/DAY)*DAY-MSK;
 const list=[];
 for(let offset=-1;offset<=3;offset++){
  const midnight=day+offset*DAY,date=new Date(midnight+MSK).toISOString().slice(0,10);
  for(const row of settings.channels){if(!row.enabled)continue;for(const time of row.times){
   const [h,m]=time.split(":").map(Number),release=midnight+(h*60+m)*MIN;
   const base=row.channel+":"+date+":"+time;
   list.push({id:base+":prepare",channel:row.channel,type:"prepare",at:release-settings.leadMinutes*MIN,releaseAt:release});
   list.push({id:base+":release",channel:row.channel,type:"release",at:release,releaseAt:release});
  }}
 }
 return list.sort((a,b)=>a.at-b.at||a.id.localeCompare(b.id));
}
export function nextSlots(settings,now){return slots(settings,now).filter(x=>x.at>now).slice(0,60);}
export class CoreHandler {
 constructor(ctx,env={}){this.storage=ctx.storage;this.env=env;}
 async state(){return await this.storage.get("core:settings")||initial();}
 async view(now=Date.now()){
  const settings=await this.state(),events=await this.storage.list({prefix:"core:event:",reverse:true,limit:60}),orders=await this.storage.list({prefix:"core:order:",reverse:true,limit:30});
  return {settings,requestedMode:(await opsState(this.storage)).requestedMode,mode:"dry_run",productionEnabled:false,rssEnabled:false,next:nextSlots(settings,now),events:[...events.values()],orders:[...orders.values()],lastTick:await this.storage.get("core:lastTick")||null,alarmAt:await this.storage.getAlarm()};
 }
 async diagnostics(now=Date.now(),verifyConfig=false){
  const [ops,settings,alarmAt,budget,jobView,rows,readiness]=await Promise.all([opsState(this.storage),this.state(),this.storage.getAlarm(),readBudget(this.storage,now),jobsView(this.storage),this.storage.list({prefix:"agent:message:",reverse:true,limit:30}),verifyConfig?productionReadiness():null]);
  return diagnose({ops,settings,alarmAt,next:nextSlots(settings,now),budget,jobs:jobView.jobs,readiness,messages:[...rows.values()]},now);
 }
 async watchdog(now=Date.now()){
  const interrupted=await recoverInterrupted(this.storage,now);
  const timer=await recoverTimer(this.storage,initial,nextSlots,now);
  return {...timer,interrupted};
 }
 async scheduleAlarm(now){
  const next=(await opsState(this.storage)).stopped?null:nextSlots(await this.state(),now)[0];
  if(next)await this.storage.setAlarm(next.at);else await this.storage.deleteAlarm();
 }
 async alarm(now=Date.now()){
  const batch=await this.storage.transaction(async tx=>{
   const batch=[];
   const settings=await tx.get("core:settings")||initial();
   const last=await tx.get("core:cursor");
   if(!(await opsState(tx)).stopped&&!settings.paused&&typeof last==="number"){
    const start=Math.max(last,now-2*DAY);
    for(const slot of slots(settings,now).filter(x=>x.at>start&&x.at<=now)){
     const key="core:event:"+String(slot.at).padStart(15,"0")+":"+slot.id;
     if(await tx.get(key))continue;
     await tx.put(key,{...slot,recordedAt:now,status:"blocked_prelaunch",reason:slot.type==="prepare"?"production_not_connected":"rss_not_authorized"});
     batch.push(slot);
     if(slot.type==="prepare"&&slot.channel!=="cinema")await saveJob(tx,"scheduled:"+slot.id,{items:[{channel:slot.channel,count:1}],day:slot.id.split(":")[1],instructions:"По сохранённому расписанию"},now,"schedule",slot.releaseAt);
    }
   }
   await tx.put("core:cursor",now);await tx.put("core:lastTick",new Date(now).toISOString());return batch;
  });
  const old=await this.storage.list({prefix:"core:event:",limit:1000});
  for(const [key,event] of old)if(event.at<now-30*DAY)await this.storage.delete(key);
  await this.scheduleAlarm(now);
  await notifyTimer(this.storage,this.env,batch,now);
 }
 async fetch(request){
  const url=new URL(request.url),now=Date.now();
  if(request.method==="GET"&&url.pathname==="/core")return Response.json(await this.view(now));
  if(request.method==="GET"&&url.pathname==="/core/diagnostics")return Response.json(await this.diagnostics(now,true));
  if(request.method==="GET"&&url.pathname==="/core/memory")return Response.json({memory:await readMemory(this.storage)});
  if(request.method==="GET"&&url.pathname==="/core/ops")return Response.json(await opsView(this.storage));
  if(request.method==="GET"&&url.pathname==="/core/budget")return Response.json(await readBudget(this.storage,now));
  if(request.method==="GET"&&url.pathname==="/core/jobs"){
   const [jobs,readiness,budget,ops,settings]=await Promise.all([jobsView(this.storage),productionReadiness(),
    readBudget(this.storage,now),opsState(this.storage),this.state()]);
   const budgetConflict=!Number.isFinite(readiness.configuredDailyRub)||Math.round(readiness.configuredDailyRub*100)!==budget.policy.mainKop;
   const verified=budget.production.ledgerConnected===true;
   const reported=budget.production.reported?.budget;
   const canExecute=this.env?.ZEN_CORE_EXECUTION_ENABLED==="true"&&readiness.privateExecutionConfigured===true&&
    verified&&!budgetConflict&&!ops.stopped&&!reported?.unsettledRequest&&!reported?.overrun&&!reported?.rollingOverrun;
   return Response.json({...jobs,productionEnabled:canExecute,requestedMode:ops.requestedMode,
    opsVersion:ops.version,modeRevision:ops.modeRevision??0,stopped:ops.stopped,schedulePaused:settings.paused,
    readiness:{...readiness,dryRunAdapterAvailable:true,budgetConflict,ownerDailyRub:budget.policy.mainKop/100,
     sharedSpendGuardVerified:verified,canExecute}});
  }
  if(request.method!=="POST")return new Response("Not found",{status:404});
  const d=await request.json();
  if(url.pathname==="/core/statistics")return Response.json(await statsView(this.storage,d.period,now));
  if(url.pathname==="/core/statistics/import"){const r=await importStats(this.storage,d,now);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/probe"){const r=await runProbe(this.storage,d,now);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/watchdog")return Response.json(await this.watchdog(now));
  if(url.pathname==="/core/memory/settings"){const r=await writeMemory(this.storage,d,now);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/ops/settings"){const r=await updateOps(this.storage,d,now,initial);if(r.status===200)await this.scheduleAlarm(now);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/budget/settings"){const r=await writeBudget(this.storage,d,now);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/receipt"){const r=await recordReceipt(this.storage,d,now);if(r.receipt&&r.events?.length){try{await notifyReceipt(this.storage,this.env,r.receipt,r.events,now);}catch{}}return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/account"){const r=await accountView(this.storage,d,now,this.env?.ZEN_CORE_EXECUTION_ENABLED==="true");return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/queue"){const r=await queueView(this.storage,d.cursor??null);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/control"){const r=await controlView(this.storage,d.id,now,this.env?.ZEN_CORE_EXECUTION_ENABLED==="true");return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/intake"){const r=await intakeView(this.storage,d.id);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/jobs/stage"){const r=await requestStage(this.storage,d,now);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/jobs/preview"){if(d.confirm!==true)return Response.json({error:"invalid_confirmation"},{status:400});const r=await previewJob(this.storage,d.id,now,validateJob,STAGES);return Response.json(r.body,{status:r.status});}
  if(url.pathname==="/core/jobs/cancel"){if(d.confirm!==true)return Response.json({error:"invalid_confirmation"},{status:400});const job=await cancelJob(this.storage,d.id);return Response.json(job?{job}:{error:"job_missing"},{status:job?200:404});}
  if(url.pathname==="/core/schedule"){
   const schedule=validateSchedule(d.settings);
   if(!schedule||!Number.isInteger(d.version))return Response.json({error:"invalid_schedule"},{status:400});
   const result=await this.storage.transaction(async tx=>{
    const previous=await tx.get("core:settings")||initial();
    if(previous.version!==d.version||(!schedule.paused&&(await opsState(tx)).stopped))return null;
    const saved={...schedule,version:previous.version+1,updatedAt:new Date(now).toISOString()};
    await tx.put("core:settings",saved);await tx.put("core:cursor",now);return saved;
   });
   if(!result)return Response.json({error:"stale_settings"},{status:409});
   await this.scheduleAlarm(now);
   return Response.json(await this.view(now));
  }
  if(url.pathname==="/core/order"){
   if(typeof d.text!=="string"||!d.text.trim()||d.text.length>2000||!/^[a-f0-9-]{36}$/.test(d.id||""))return Response.json({error:"invalid_order"},{status:400});
   const order=await this.storage.transaction(async tx=>{
    if((await opsState(tx)).stopped)return null;
    const key="core:orderid:"+d.id,old=await tx.get(key);
    if(old)return old.text===d.text.trim()?old:null;
    const entry={id:d.id,text:d.text.trim(),at:new Date(now).toISOString(),status:"queued_for_future_agent",executed:false};
    await tx.put(key,entry);await tx.put("core:order:"+String(now).padStart(15,"0")+":"+d.id,entry);return entry;
   });
   return Response.json(order?{order}:{error:"id_conflict"},{status:order?200:409});
  }
  return new Response("Not found",{status:404});
 }
}
export function coreStub(env){return env.REVIEW_JOURNAL?.get(env.REVIEW_JOURNAL.idFromName("zen-core-scheduler-v1"));}
export async function rssStatus(){
 const base="https://raw.githubusercontent.com/kaokshi/kaokshi.github.io/main/config/";
 const get=async file=>{const r=await fetch(base+file,{redirect:"manual",signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error();return r.json();};
 let flags=null;
 try{const [site,policy]=await Promise.all([get("site.json"),get("ai_revision_policy.json")]);const p=policy.future_rss_delivery;flags={publication:site.publication_enabled===true,launched:p.project_launched===true,active:p.currently_active===true,channels:Array.isArray(site.auto_release_active_channels)?site.auto_release_active_channels.filter(x=>CHANNELS.includes(x)):[]};}catch{}
 const feeds=await Promise.all(CHANNELS.map(async channel=>{
  const url="https://kaokshi.github.io/feeds/"+channel+".xml";
  try{const r=await fetch(url,{method:"HEAD",redirect:"manual",signal:AbortSignal.timeout(5000)});return {channel,url,http:r.status,available:r.ok};}catch{return {channel,url,http:null,available:false};}
 }));
 return {flags,feeds,checkedAt:new Date().toISOString(),zenIngestionVerified:false,deliveryEnabled:false};
}
