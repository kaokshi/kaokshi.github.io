// Proposed local generation admission policy; no network or financial state.
// One account-wide coordinator must own this state before live use.
export const CLAUDY_LOCAL_POLICY=Object.freeze({windowMs:60000,requests:25,concurrent:5,production:3,chat:1,development:1});
const roles=["production","chat","development"];
export function admitClaudy(state,role,id,now,policy=CLAUDY_LOCAL_POLICY){
 if(!roles.includes(role)||typeof id!=="string"||!id||!Number.isSafeInteger(now)||now<0||!state||!Array.isArray(state.starts)||!Array.isArray(state.active))return {allowed:false,reason:"invalid_state"};
 if(state.starts.some(x=>!Number.isSafeInteger(x)||x>now)||state.active.some(x=>!x||!roles.includes(x.role)||typeof x.id!=="string"))return {allowed:false,reason:"invalid_state"};
 const starts=state.starts.filter(x=>x>now-policy.windowMs),active=state.active;
 if(active.some(x=>x.id===id))return {allowed:false,reason:"already_active"};
 if(Number.isFinite(state.blockedUntil)&&state.blockedUntil>now)return {allowed:false,reason:"provider_backoff",retryAt:state.blockedUntil};
 if(starts.length>=policy.requests)return {allowed:false,reason:"minute_limit",retryAt:Math.min(...starts)+policy.windowMs};
 if(active.length>=policy.concurrent||active.filter(x=>x.role===role).length>=policy[role])return {allowed:false,reason:"concurrency_limit"};
 return {allowed:true,state:{...state,starts:[...starts,now],active:[...active,{id,role}]}};
}
// Release only a known completed operation; ambiguous paid operations are not retried here.
export function finishClaudy(state,id,{terminal=false,retryAfterSeconds=null,now=Date.now()}={}){
 if(!state||!Array.isArray(state.active)||!terminal||!state.active.some(x=>x.id===id))return state;
 const blockedUntil=Number.isSafeInteger(retryAfterSeconds)&&retryAfterSeconds>=0&&retryAfterSeconds<=86400?Math.max(state.blockedUntil||0,now+retryAfterSeconds*1000):state.blockedUntil||0;
 return {...state,active:state.active.filter(x=>x.id!==id),blockedUntil};
}
