// Read-only provider telemetry. No inference, account writes or ledger settlement.
import {CLAUDY_BASE} from "./claudy-adapter.mjs";
function rub(v){
 if(!["number","string"].includes(typeof v)||String(v).trim()==="")return null;
 const n=Number(v);
 return Number.isFinite(n)&&n>=0?n:null;
}
export function claudyBalance(body){
 if(!body||typeof body!=="object"||Array.isArray(body))return null;
 for(const k of ["balance_rub","balanceRub","credits_rub","balance_rubles"]){
  const v=rub(body[k]);if(v!==null)return v;
 }
 if(body.currency==="RUB"||body.currency==="rub"){const v=rub(body.balance);if(v!==null)return v;}
 for(const k of ["data","account","balance"]){const v=claudyBalance(body[k]);if(v!==null)return v;}
 return null;
}
// Claudy support confirmed these fields are denominated in RUB.
export function claudyMonthlyLimit(value){
 if(!value||typeof value!=="object"||Array.isArray(value))return null;
 const limitRub=rub(value.monthly_limit),spentRub=rub(value.spent_this_month),leftRub=rub(value.left);
 if(limitRub===null||spentRub===null||leftRub===null)return null;
 // Preserve provider values; don't manufacture a remainder or reset date.
 return {limitRub,spentRub,leftRub,consistent:Math.abs(Math.max(0,limitRub-spentRub)-leftRub)<=0.02,scope:"monthly_provider_report"};
}
export function claudyFinancialFields(body){
 return {keyLimit:claudyMonthlyLimit(body?.key),accountLimit:claudyMonthlyLimit(body?.account_limit)};
}
function headerInt(headers,name){
 const v=headers?.get(name);
 if(typeof v!=="string"||!/^\d+$/.test(v))return null;
 const n=Number(v);return Number.isSafeInteger(n)?n:null;
}
export function claudyRateLimits(headers,now=Date.now()){
 const limit=headerInt(headers,"X-RateLimit-Limit"),remaining=headerInt(headers,"X-RateLimit-Remaining");
 return {observedAt:now,limitPerMinute:limit,remaining:limit!==null&&remaining!==null&&remaining>limit?null:remaining,
  resetSeconds:headerInt(headers,"X-RateLimit-Reset"),retryAfterSeconds:headerInt(headers,"Retry-After"),
  documentedDefaultPerMinute:30,documentedDefaultConcurrent:5};
}
export function balanceMovement(previous,current){
 if(!previous||!current||previous.status!=="ok"||current.status!=="ok"||!Number.isFinite(previous.balanceRub)||!Number.isFinite(current.balanceRub)||current.observedAt<=previous.observedAt)return null;
 return {netChangeRub:Math.round((current.balanceRub-previous.balanceRub)*1e6)/1e6,intervalStart:previous.observedAt,intervalEnd:current.observedAt,classification:"net_balance_change_not_confirmed_spend"};
}
export async function readClaudyStatus(key,{fetcher=fetch,now=Date.now()}={}){
 const base={provider:"claudy",observedAt:now,mode:"read_only",balanceRub:null,keyLimit:null,accountLimit:null,rateLimits:claudyRateLimits(null,now)};
 if(typeof key!=="string"||!key.trim())return {...base,status:"not_configured"};
 try{
  const r=await fetcher(CLAUDY_BASE+"/v1/me",{method:"GET",headers:{Authorization:"Bearer "+key,Accept:"application/json"},redirect:"error",signal:AbortSignal.timeout(12000)});
  const rateLimits=claudyRateLimits(r.headers,now);
  if(!r.ok)return {...base,rateLimits,status:r.status===401||r.status===403?"auth_failed":r.status===429?"rate_limited":"unavailable"};
  const body=await r.json(),balanceRub=claudyBalance(body);
  return {...base,rateLimits,balanceRub,...claudyFinancialFields(body),status:balanceRub===null?"balance_schema_unverified":"ok"};
 }catch{return {...base,status:"unavailable"};}
}

// Compare two observations of the SAME role/key enrollment and billing period.
// Monthly counters can reset or be adjusted. No operation-level attribution.
export function monthlySpendMovement(previous,current){
 const p=previous?.keyLimit,c=current?.keyLimit;
 if(previous?.status!=="ok"||current?.status!=="ok"||!p?.consistent||!c?.consistent||!Number.isSafeInteger(previous.observedAt)||!Number.isSafeInteger(current.observedAt)||current.observedAt<=previous.observedAt)return null;
 if(new Date(previous.observedAt).toISOString().slice(0,7)!==new Date(current.observedAt).toISOString().slice(0,7)||p.limitRub!==c.limitRub||c.spentRub<p.spentRub)return {classification:"counter_reset_or_adjustment",deltaRub:null,costVerified:false};
 return {classification:"provider_monthly_counter_movement",deltaRub:Math.round((c.spentRub-p.spentRub)*1e6)/1e6,intervalStart:previous.observedAt,intervalEnd:current.observedAt,costVerified:false};
}
