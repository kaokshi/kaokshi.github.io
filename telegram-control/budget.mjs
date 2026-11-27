import {readAgentSettings} from "./agent-settings.mjs";
// Owner budget policy. Monetary arithmetic uses integer kopecks.
// No production charges are authorized until a shared reconciled ledger is connected.
export const RESERVE_KOP=5000;
export const DEFAULT_MAIN_KOP=30000;
export const initialBudget=()=>({version:0,mainKop:DEFAULT_MAIN_KOP,reserveKop:RESERVE_KOP,timezone:"Europe/Moscow",updatedAt:null,ownerSaved:false});
const isMoney=x=>Number.isSafeInteger(x)&&x>=0&&x<=100000000;
export function validateBudget(value){
 if(!value||!Number.isInteger(value.mainKop)||value.mainKop<100||value.mainKop>1000000||value.reserveKop!==RESERVE_KOP)return null;
 return {mainKop:value.mainKop,reserveKop:RESERVE_KOP,timezone:"Europe/Moscow"};
}
export function budgetDecision(policy,ledger,operation){
 if(!validateBudget(policy)||!ledger||ledger.verified!==true||!isMoney(ledger.mainKop)||!isMoney(ledger.reserveKop)||!operation||!isMoney(operation.boundKop)||operation.boundKop===0)return {allowed:false,reason:"unverified_ledger_or_price"};
 if(ledger.mainKop>policy.mainKop||ledger.reserveKop>RESERVE_KOP)return {allowed:false,reason:"budget_exhausted"};
 const main=Math.min(operation.boundKop,policy.mainKop-ledger.mainKop),reserve=operation.boundKop-main;
 if(reserve===0)return {allowed:true,mainKop:main,reserveKop:0,reason:"main"};
 const eligible=operation.started===true&&operation.required===true&&["missing_image","image_repair","required_final_check","required_revision"].includes(operation.kind)&&Number.isInteger(operation.attempt)&&operation.attempt>=1&&operation.attempt<=3;
 if(!eligible)return {allowed:false,reason:"reserve_not_eligible"};
 if(reserve>RESERVE_KOP-ledger.reserveKop)return {allowed:false,reason:"budget_exhausted"};
 return {allowed:true,mainKop:main,reserveKop:reserve,reason:operation.kind};
}
export async function readBudget(storage,now=Date.now()){
 const policy=await storage.get("budget:policy")||initialBudget();
 const day=new Date(now+10800000).toISOString().slice(0,10);
 const latest=await storage.get("budget:executor-report");
 const fresh=Boolean(latest&&latest.budget?.day===day&&now-latest.observedAt<=300000&&latest.observedAt<=now+30000);
 const chat=await storage.get("agent:budget:"+day)||{reserved:0,calls:0};
 const agent=await readAgentSettings(storage);
 const matched=fresh&&policy.mainKop===DEFAULT_MAIN_KOP;
 const remaining=(field)=>matched?Math.floor(Number(latest.budget[field])*100+1e-8):null;
 const mainLeft=remaining("mainRemainingRub"),reserveLeft=remaining("reserveRemainingRub");
 return {policy,day,totalKop:policy.mainKop+RESERVE_KOP,production:{
  ledgerConnected:matched,meteredRub:matched?latest.budget.meteredRub:null,rollingMeteredRub:matched?latest.budget.rollingMeteredRub??null:null,mainSpentKop:mainLeft===null?null:policy.mainKop-mainLeft,
  reserveSpentKop:reserveLeft===null?null:RESERVE_KOP-reserveLeft,reserveRemainingKop:reserveLeft,
  activationReason:matched?latest.reserveReasons?.map(x=>x.reason).join(", ")||null:null,
  reported:latest?{...latest,stale:!fresh}:null,policyMismatch:policy.mainKop!==DEFAULT_MAIN_KOP
 },chat:{limitRub:agent.dailyRub,opusDailyRub:agent.opusDailyRub,opusAccountedRub:chat.opusAccountedRub??(chat.calls?null:0),confirmedRub:chat.confirmedRub??(chat.calls?null:0),accountedRub:chat.reserved,calls:chat.calls,chargeHold:Boolean(await storage.get("agent:charge-hold")),productionHold:Boolean(await storage.get("account:production-hold"))},executionEnabled:false};
}
export async function writeBudget(storage,data,now=Date.now()){
 const v=validateBudget(data?.budget);
 if(data?.confirm!==true||!v||!Number.isInteger(data.version))return {status:400,body:{error:"invalid_budget"}};
 const saved=await storage.transaction(async tx=>{
  const current=await tx.get("budget:policy")||initialBudget();
  if(current.version!==data.version)return false;
  const updated={...v,version:current.version+1,updatedAt:new Date(now).toISOString(),ownerSaved:true};
  await tx.put("budget:policy",updated);await tx.put("budget:history:"+String(updated.version).padStart(12,"0"),{previous:current,updated});return true;
 });
 return saved?{status:200,body:await readBudget(storage,now)}:{status:409,body:{error:"stale_budget"}};
}
