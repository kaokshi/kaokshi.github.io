// Free token-count preflight and diagnostic evidence. No paid requests or ledger writes.
import {CLAUDY_BASE,claudyModel,claudyHeaders,claudyBound} from "./claudy-adapter.mjs";
const count=v=>Number.isSafeInteger(v)&&v>=0;
export async function claudyCountTokens(key,model,payload,{fetcher=fetch}={}){
 const selected=claudyModel(model);
 if(!selected||payload?.model!==selected.id||!Array.isArray(payload.messages)||typeof payload.system!=="string")return {status:"invalid_request",inputTokens:null};
 try{
  const r=await fetcher(CLAUDY_BASE+"/v1/messages/count_tokens",{method:"POST",headers:claudyHeaders(key),redirect:"error",signal:AbortSignal.timeout(12000),body:JSON.stringify({model:selected.id,system:payload.system,messages:payload.messages})});
  if(!r.ok)return {status:r.status===429?"rate_limited":r.status===401||r.status===403?"auth_failed":"unavailable",inputTokens:null};
  const data=await r.json();
  return count(data?.input_tokens)?{status:"ok",inputTokens:data.input_tokens}:{status:"invalid_usage",inputTokens:null};
 }catch{return {status:"unavailable",inputTokens:null};}
}
export function claudyPreflight(model,payload,observation,balanceRub,perCallRub){
 const b=claudyBound(model,payload,perCallRub),selected=claudyModel(model);
 if(!b||!selected||observation?.status!=="ok"||!count(observation.inputTokens)||typeof balanceRub!=="number"||!Number.isFinite(balanceRub)||balanceRub<0)return {allowed:false,reason:"unverified_preflight"};
 // Count endpoint may differ from final usage. Never lower the conservative estimate.
 const inputBound=Math.max(b.inputBound,observation.inputTokens+1024);
 const estimatedUpperRub=Math.ceil((inputBound*selected.input*2+payload.max_tokens*selected.output)/1e6*100)/100;
 const reason=estimatedUpperRub>perCallRub?"call_budget_exceeded":estimatedUpperRub>balanceRub?"insufficient_balance":null;
 return {allowed:reason===null,reason,inputBound,reportedInputTokens:observation.inputTokens,estimatedUpperRub,estimateOnly:true,settlementAvailable:false};
}
export function claudyEvidence(headers,response){
 const id=v=>typeof v==="string"&&/^[A-Za-z0-9_.:-]{1,160}$/.test(v)?v:null;
 return {provider:"claudy",requestId:id(headers?.get("request-id"))??id(headers?.get("x-request-id")),messageId:id(response?.id),cacheMode:headers?.get("X-Claudy-Cache")==="bypass"?"bypass":"unknown",costVerified:false,settlementSource:null};
}
