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
 const base={provider:"claudy",observedAt:now,mode:"read_only",balanceRub:null,rateLimits:claudyRateLimits(null,now)};
 if(typeof key!=="string"||!key.trim())return {...base,status:"not_configured"};
 try{
  const r=await fetcher(CLAUDY_BASE+"/v1/me",{method:"GET",headers:{Authorization:"Bearer "+key,Accept:"application/json"},redirect:"error",signal:AbortSignal.timeout(12000)});
  const rateLimits=claudyRateLimits(r.headers,now);
  if(!r.ok)return {...base,rateLimits,status:r.status===401||r.status===403?"auth_failed":r.status===429?"rate_limited":"unavailable"};
  const balanceRub=claudyBalance(await r.json());
  return {...base,rateLimits,balanceRub,status:balanceRub===null?"balance_schema_unverified":"ok"};
 }catch{return {...base,status:"unavailable"};}
}
