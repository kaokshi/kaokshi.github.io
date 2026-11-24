// Local emergency gate and owner-only notification settings. External jobs are not controlled here.
export const MODES=["manual","assisted","autonomous"];
export const initialOps=()=>({version:0,stopped:false,notifications:false,requestedMode:"manual",updatedAt:null});
export async function opsState(storage){const saved=await storage.get("ops:state");return {...initialOps(),...saved,requestedMode:MODES.includes(saved?.requestedMode)?saved.requestedMode:"manual"};}
export async function updateOps(storage,d,now,initialSchedule){
 if(d?.confirm!==true||!Number.isInteger(d.version)||!["stop","resume","notifications","mode"].includes(d.action)||d.action==="notifications"&&typeof d.enabled!=="boolean"||d.action==="mode"&&!MODES.includes(d.mode))return {status:400,body:{error:"invalid_ops"}};
 const next=await storage.transaction(async tx=>{
  const old=await opsState(tx);if(old.version!==d.version||d.action==="mode"&&old.stopped&&d.mode!=="manual")return null;
  const value={...old,version:old.version+1,updatedAt:new Date(now).toISOString()};
  if(d.action==="notifications")value.notifications=d.enabled;
  else if(d.action==="mode"){
   value.requestedMode=d.mode;
   // A change of intent never activates the scheduler or any paid lane.
   const schedule=await tx.get("core:settings")||initialSchedule();
   await tx.put("core:settings",{...schedule,paused:true,version:schedule.version+1,updatedAt:value.updatedAt});
   await tx.put("core:cursor",now);
  } else {
   value.stopped=d.action==="stop";
   // Resume never restarts the timer. Explicitly enable it in the schedule UI.
   const schedule=await tx.get("core:settings")||initialSchedule();
   await tx.put("core:settings",{...schedule,paused:true,version:schedule.version+1,updatedAt:value.updatedAt});
   await tx.put("core:cursor",now);
  }
  await tx.put("ops:state",value);
  await tx.put("ops:event:"+String(value.version).padStart(12,"0"),{kind:d.action,at:value.updatedAt,enabled:value.notifications,stopped:value.stopped,requestedMode:value.requestedMode});
  return value;
 });
 return next?{status:200,body:{state:next}}:{status:409,body:{error:"stale_ops"}};
}
export async function opsView(storage){
 const state=await opsState(storage);
 const sources=[["ops:event:","control"],["core:event:","timer"],["budget:history:","budget"],["agent:message:","chat"],["production:index:","job"],["ops:notification:","notification"]];
 const rows=(await Promise.all(sources.map(async([prefix,type])=>{
  const list=await storage.list({prefix,reverse:true,limit:20});
  const events=[];
  for(const [key,v] of list){
   if(type==="control")events.push({type,at:v.at,kind:v.kind,stopped:v.stopped,enabled:v.enabled,requestedMode:v.requestedMode});
   if(type==="timer")events.push({type,at:new Date(v.recordedAt).toISOString(),kind:v.type,channel:v.channel,status:v.status});
   if(type==="budget")events.push({type,at:v.updated.updatedAt,mainKop:v.updated.mainKop,reserveKop:v.updated.reserveKop});
   if(type==="chat")events.push({type,at:v.at,status:v.status,cost:v.cost});
   if(type==="job"){const j=await storage.get("production:job:"+v);if(j)events.push({type,at:j.cancelledAt||j.createdAt,status:j.status,items:j.plan.items});}
   if(type==="notification")events.push({type,at:v.at,status:v.status,count:v.count,kind:v.kind||"timer"});
  }return events;
 }))).flat().sort((a,b)=>String(b.at).localeCompare(String(a.at))).slice(0,60);
 return {state,events:rows,scope:"zen_control_only",externalStopConnected:false,modeEffective:"dry_run",autonomousExecutionEnabled:false,publicationAuthorized:false};
}
export async function notifyTimer(storage,env,batch,now){
 if(!batch.length||!env?.TELEGRAM_BOT_TOKEN||!/^[1-9]\d{0,15}$/.test(env.TELEGRAM_OWNER_ID||""))return;
 const key="ops:notification:"+String(now).padStart(15,"0");
 const claim=await storage.transaction(async tx=>{
  const state=await opsState(tx);
  if(state.stopped||!state.notifications||await tx.get(key))return false;
  await tx.put(key,{at:new Date(now).toISOString(),status:"sending",count:batch.length});return true;
 });
 if(!claim)return;
 let status="unconfirmed";
 try{
  const names={legends:"Легенды тёмных улиц",science:"За гранью очевидного",cinema:"Кадр за Кадром"};
  const lines=batch.slice(0,12).map(x=>(x.type==="prepare"?"Подготовка":"Релиз")+" · "+names[x.channel]+" · "+x.id.split(":").slice(1,4).join(" "));
  const r=await fetch("https://api.telegram.org/bot"+env.TELEGRAM_BOT_TOKEN+"/sendMessage",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:Number(env.TELEGRAM_OWNER_ID),text:"ZEN CORE · тестовый планировщик\n"+lines.join("\n")+(batch.length>12?"\nИ ещё "+(batch.length-12)+" событий":"")+"\nГенерация и RSS заблокированы. Это уведомление о расписании, не о готовности статьи.",disable_web_page_preview:true}),redirect:"manual",signal:AbortSignal.timeout(10000)});
  if(r.ok&&(await r.json()).ok===true)status="sent";
 }catch{}
 await storage.put(key,{at:new Date(now).toISOString(),status,count:batch.length});
 // Ambiguous delivery is never automatically retried.
}
