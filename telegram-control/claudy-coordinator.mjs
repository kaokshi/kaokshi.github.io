// Transactional coordination for one Claudy account. Staged, not wired to paid routes.
import {admitClaudy,finishClaudy} from "./claudy-admission.mjs";
import {claudyMonthlyEligibility,claudyEvidence} from "./claudy-preflight.mjs";
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

// Shadow financial reservations, deliberately disconnected from all paid dispatch.
// Persist amounts in integer kopecks and never release them from a terminal slot observation.
const MONEY_KEY="telemetry:claudy:shadow-reservations:v1";
const kopecks=v=>Number.isFinite(v)&&v>=0&&Number.isSafeInteger(Math.floor(v*100))?Math.floor(v*100):null;
export async function reserveClaudyShadowBudget(storage,{role,id,preflight,status,now=Date.now()}={}){
 if(!validId(id)||!["chat","production","development"].includes(role)||!Number.isSafeInteger(now)||now<0)return {reserved:false,dispatchAuthorized:false,reason:"invalid_request"};
 const eligibility=claudyMonthlyEligibility(preflight,status,now);
 if(!eligibility.eligible)return {reserved:false,dispatchAuthorized:false,reason:eligibility.reason};
 const amount=Math.ceil(eligibility.estimatedUpperRub*100);
 const balance=kopecks(status.balanceRub),keyLeft=kopecks(status.keyLimit.leftRub),accountLeft=status.accountLimit?kopecks(status.accountLimit.leftRub):null;
 if(!Number.isSafeInteger(amount)||amount<=0||balance===null||keyLeft===null||status.accountLimit&&accountLeft===null)return {reserved:false,dispatchAuthorized:false,reason:"invalid_amount"};
 return storage.transaction(async tx=>{
  const rows=await tx.get(MONEY_KEY)??[];
  if(!Array.isArray(rows)||rows.some(x=>!validId(x.id)||!["chat","production","development"].includes(x.role)||!Number.isSafeInteger(x.upperKop)||x.upperKop<=0))return {reserved:false,dispatchAuthorized:false,reason:"invalid_reservations"};
  if(rows.some(x=>x.id===id))return {reserved:false,dispatchAuthorized:false,reason:"duplicate_operation"};
  const total=rows.reduce((sum,x)=>sum+x.upperKop,0),same=rows.filter(x=>x.role===role).reduce((sum,x)=>sum+x.upperKop,0);
  if(!Number.isSafeInteger(total+amount)||total+amount>balance||same+amount>keyLeft||accountLeft!==null&&total+amount>accountLeft)return {reserved:false,dispatchAuthorized:false,reason:"reserved_budget_exhausted"};
  await tx.put(MONEY_KEY,[...rows,{id,role,upperKop:amount,createdAt:now,status:"shadow_only"}]);
  return {reserved:true,dispatchAuthorized:false,upperKop:amount,costVerified:false};
 });
}
// Evidence is written before any downstream response parsing, without retaining content or secrets.
export async function recordClaudyResponseEvidence(storage,{id,headers,response,now=Date.now()}={}){
 if(!validId(id)||!Number.isSafeInteger(now)||now<0)return {recorded:false};
 const usage={};
 for(const key of ["input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens"]){
  const v=response?.usage?.[key];usage[key]=Number.isSafeInteger(v)&&v>=0?v:null;
 }
 const evidence={...claudyEvidence(headers,response),usage,observedAt:now,status:"unverified_response"};
 return storage.transaction(async tx=>{
  const marker="telemetry:claudy:response-evidence:"+id;
  if(await tx.get(marker))return {recorded:false,reason:"duplicate_evidence"};
  await tx.put(marker,evidence);return {recorded:true,costVerified:false};
 });
}
