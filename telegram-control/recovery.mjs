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
