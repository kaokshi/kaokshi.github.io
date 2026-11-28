// Claudy as an owner-selectable provider for the owner chat only.
// Owner decision 2026-11-28: Claudy has no per-request receipt, so the cost of
// one chat request is the movement of the dedicated chat key's monthly counter
// (GET /v1/me key.spent_this_month) read immediately before and after that one
// request, under strict single flight. Every ambiguous case stays unsettled.
import {CLAUDY_BASE,claudyHeaders} from "./claudy-adapter.mjs";
import {monthlySpendMovement} from "./claudy-monitor.mjs";

export const CLAUDY_CHAT_LANE="claudy_chat";
// Native thinking shares max_tokens with the reply, so the chat gets more room
// than the RouterAI ceiling. The bound below is priced on this full ceiling.
export const CLAUDY_CHAT_MAX_OUTPUT=4000;
// Before read -> dispatch (<=60 s request timeout) -> after read (<=12 s).
export const CLAUDY_COUNTER_WINDOW_MS=100000;
// A timed-out request may still complete upstream; reconcile waits this long.
export const CLAUDY_IN_FLIGHT_MS=120000;
const ADMISSION_KEY="telemetry:claudy:admission:v1";

// The persisted baseline holds only the numbers the rule needs.
export function counterSnapshot(status){
 const k=status?.keyLimit;
 if(status?.status!=="ok"||!Number.isSafeInteger(status.observedAt)||!k||k.consistent!==true||![k.limitRub,k.spentRub,k.leftRub].every(Number.isFinite))return null;
 return {status:"ok",observedAt:status.observedAt,keyLimit:{limitRub:k.limitRub,spentRub:k.spentRub,leftRub:k.leftRub,consistent:true}};
}

// Pure settlement rule. Returns settled / overrun / unknown, never a guess.
// - settled: both reads ok and consistent, same month and limit, reads within
//   the window (unless maxWindowMs is null for an owner-confirmed reconcile),
//   the request reached a terminal HTTP answer, no overlapping operation, and
//   0 <= delta <= bound. A completed reply with output tokens but delta 0 means
//   the counter has not caught up yet, so that stays unknown.
// - overrun: delta > bound. The caller keeps the day blocked.
// - unknown: anything else. The caller keeps the hold.
export function claudyCounterSettlement({before,after,boundRub,responded,outputTokens=0,overlap=false,maxWindowMs=CLAUDY_COUNTER_WINDOW_MS}={}){
 const unknown=reason=>({outcome:"unknown",reason,costVerified:false});
 if(overlap!==false)return unknown("overlapping_operation");
 if(responded!==true)return unknown("transport_unconfirmed");
 if(typeof boundRub!=="number"||!Number.isFinite(boundRub)||boundRub<=0)return unknown("bound_invalid");
 const b=counterSnapshot(before),a=counterSnapshot(after);
 if(!b||!a)return unknown("counter_read_failed");
 const movement=monthlySpendMovement(b,a);
 if(!movement)return unknown("counter_read_failed");
 if(movement.classification!=="provider_monthly_counter_movement")return unknown("counter_reset_or_adjustment");
 if(maxWindowMs!==null&&a.observedAt-b.observedAt>maxWindowMs)return unknown("counter_reads_stale");
 const delta=movement.deltaRub;
 if(typeof delta!=="number"||!Number.isFinite(delta)||delta<0)return unknown("counter_delta_invalid");
 if(delta>boundRub+1e-9)return {outcome:"overrun",observedRub:delta,costVerified:false};
 if(delta===0&&Number.isSafeInteger(outputTokens)&&outputTokens>0)return unknown("counter_not_updated");
 return {outcome:"settled",costRub:delta,costVerified:true,source:"claudy_monthly_counter_delta"};
}

// Another admission in the account coordinator after ours means the counter
// movement may not belong to this request alone.
export async function claudyOverlap(storage,id,slotAt){
 const state=await storage.get(ADMISSION_KEY);
 if(!state||!Array.isArray(state.starts)||!Array.isArray(state.active))return true;
 if(state.active.some(x=>x?.id!==id))return true;
 return state.starts.filter(x=>x>=slotAt).length>1;
}

// One request, no retry. responded=false means no HTTP answer was observed,
// so the provider may still have completed and charged it.
export async function sendClaudyMessage(key,payload,{fetcher=fetch}={}){
 let r;
 try{r=await fetcher(CLAUDY_BASE+"/v1/messages",{method:"POST",headers:claudyHeaders(key),body:JSON.stringify(payload),redirect:"error",signal:AbortSignal.timeout(60000)});}
 catch{return {responded:false,ok:false,status:null,headers:null,body:null,retryAfterSeconds:null};}
 const retry=r.headers?.get("Retry-After"),retryAfterSeconds=typeof retry==="string"&&/^\d{1,5}$/.test(retry)?Number(retry):null;
 let body=null;
 try{body=await r.json();}catch{}
 return {responded:true,ok:r.ok,status:r.status,headers:r.headers,body,retryAfterSeconds};
}
