// Safe recovery of the local dry-run timer only. No external calls or paid retries.
import {opsState} from "./ops.mjs";
const GAP=300000,DAY=86400000;
export async function recoverTimer(storage,initial,nextSlots,now=Date.now()){
 return storage.transaction(async tx=>{
  const ops=await opsState(tx),settings=await tx.get("core:settings")||initial();
  if(ops.stopped||settings.paused)return {status:"skipped",reason:"owner_pause"};
  const next=nextSlots(settings,now)[0];
  if(!next)return {status:"skipped",reason:"no_future_slots"};
  const alarm=await tx.getAlarm();
  if(alarm!=null&&alarm>=now-GAP)return {status:"healthy",alarmAt:alarm};
  const cursor=await tx.get("core:cursor");
  if(!Number.isSafeInteger(cursor)||cursor>now||now-cursor>2*DAY)return {status:"blocked",reason:"cursor_unverified"};
  const old=await tx.get("recovery:last");
  if(old?.scheduleVersion===settings.version&&old.attemptAt>now-GAP)return {status:"skipped",reason:"cooldown"};
  // Preserve the cursor so the existing idempotent scheduler can account for missed
  // dry-run slots; never rewind it or fabricate completion evidence.
  const at=now+1000;
  await tx.setAlarm(at);
  const event={status:"rearmed",reason:alarm==null?"missing_alarm":"overdue_alarm",attemptAt:now,alarmAt:at,scheduleVersion:settings.version,productionEnabled:false};
  await tx.put("recovery:last",event);
  await tx.put("recovery:event:"+String(now).padStart(15,"0"),event);
  return event;
 });
}

// A killed Worker may have sent a paid request before recording the response.
// Quarantine the operation without releasing its reservation or resending it.
export async function recoverInterrupted(storage,now=Date.now()){
 return storage.transaction(async tx=>{
  const hold=await tx.get("agent:charge-hold");
  if(!hold||hold.status!=="pending"||!Number.isSafeInteger(hold.startedAt)||now-hold.startedAt<GAP)return {status:"nothing_to_recover"};
  const key="agent:id:"+hold.id,m=await tx.get(key);
  if(!m||m.status!=="pending")return {status:"blocked",reason:"inconsistent_pending_charge"};
  const message={...m,status:"failed",cost:null,reply:"Запрос прерван. Стоимость не подтверждена. Резерв сохранён, повтор заблокирован.",proposal:null,job:null,recovery:"charge_unknown"};
  await tx.put(key,message);
  await tx.put("agent:message:"+String(hold.startedAt).padStart(15,"0")+":"+hold.id,message);
  await tx.put("agent:charge-hold",{...hold,status:"unknown"});
  const event={status:"quarantined",reason:"interrupted_paid_chat",attemptAt:now,productionEnabled:false};
  await tx.put("recovery:event:"+String(now).padStart(15,"0")+":chat:"+hold.id,event);
  return event;
 });
}
