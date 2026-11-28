// Transactional coordination for one Claudy account. Staged, not wired to paid routes.
import {admitClaudy,finishClaudy} from "./claudy-admission.mjs";
const KEY="telemetry:claudy:admission:v1";
const validId=id=>typeof id==="string"&&/^[a-zA-Z0-9:_-]{1,120}$/.test(id);
const initial=()=>({starts:[],active:[],blockedUntil:0});
export async function reserveClaudySlot(storage,{role,id,now=Date.now()}={}){
 if(!validId(id))return {allowed:false,reason:"invalid_id"};
 return storage.transaction(async tx=>{
  const old=await tx.get(KEY)??initial();
  // Retain durable operation markers even after completion to prevent reuse.
  const marker="telemetry:claudy:operation:"+id;
  if(await tx.get(marker))return {allowed:false,reason:"duplicate_operation"};
  const decision=admitClaudy(old,role,id,now);
  if(!decision.allowed)return decision;
  await tx.put(KEY,decision.state);
  await tx.put(marker,{role,status:"reserved",createdAt:now});
  return {allowed:true,id,role};
 });
}
export async function observeClaudyTerminal(storage,{id,terminal=false,retryAfterSeconds=null,now=Date.now()}={}){
 if(!validId(id)||terminal!==true||!Number.isSafeInteger(now)||now<0)return {released:false};
 return storage.transaction(async tx=>{
  const old=await tx.get(KEY),marker="telemetry:claudy:operation:"+id;
  const operation=await tx.get(marker);
  if(!old||operation?.status!=="reserved"||!old.active?.some(x=>x.id===id)||now<operation.createdAt)return {released:false};
  const next=finishClaudy(old,id,{terminal:true,retryAfterSeconds,now});
  await tx.put(KEY,next);
  await tx.put(marker,{...operation,status:"terminal_observed",observedAt:now});
  // This only releases a concurrency slot. No charge settlement or authorization.
  return {released:true,costVerified:false};
 });
}
export async function readClaudyCoordinator(storage){
 const state=await storage.get(KEY)??initial();
 return {active:state.active.length,roles:{production:state.active.filter(x=>x.role==="production").length,chat:state.active.filter(x=>x.role==="chat").length,development:state.active.filter(x=>x.role==="development").length},blockedUntil:state.blockedUntil??0,liveEnabled:false};
}
