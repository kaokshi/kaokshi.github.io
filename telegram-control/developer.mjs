// Owner-only developer task intents. No paid API calls, GitHub writes or execution.
import {opsState} from "./ops.mjs";
const KEY="developer:policy:v1", PREFIX="developer:task:";
const defaults=()=>({version:0,perTaskKop:1000,dailyKop:5000,monthlyKop:150000});
const idOK=x=>typeof x==="string"&&/^[a-f0-9-]{36}$/.test(x);
const kop=x=>Number.isSafeInteger(x)&&x>=100&&x<=150000;
const fail=(error,status=400)=>({error,status});
export async function developerView(storage){
 const policy=await storage.get(KEY)??defaults();
 const rows=await storage.list({prefix:PREFIX,reverse:true,limit:100});
 const tasks=[...rows.values()].sort((a,b)=>b.createdAt-a.createdAt);
 return {policy,tasks,model:"anthropic/claude-opus-5.5",provider:"claudy",executionEnabled:false,paidDispatchAuthorized:false,stopped:(await opsState(storage)).stopped,reason:"executor_and_settlement_pending"};
}
export async function developerAction(storage,d,now=Date.now()){
 if(d?.confirm!==true)return fail("confirmation_required");
 return storage.transaction(async tx=>{
  const policy=await tx.get(KEY)??defaults();
  if(d.action==="policy"){
   const {perTaskKop,dailyKop,monthlyKop}=d.settings??{};
   if(![perTaskKop,dailyKop,monthlyKop].every(kop)||perTaskKop>dailyKop||dailyKop>monthlyKop)return fail("invalid_developer_budget");
   if(d.version!==policy.version)return fail("stale_settings",409);
   const next={version:policy.version+1,perTaskKop,dailyKop,monthlyKop};
   await tx.put(KEY,next);return {policy:next};
  }
  if(!idOK(d.id))return fail("invalid_id");
  const key=PREFIX+d.id,old=await tx.get(key);
  if(d.action==="cancel"){
   if(!old)return fail("task_missing",404);
   if(old.status==="cancelled")return {task:old};
   if(old.status!=="queued_blocked")return fail("task_not_cancellable",409);
   const task={...old,status:"cancelled",cancelledAt:now};
   await tx.put(key,task);return {task};
  }
  if(d.action!=="create")return fail("unknown_action");
  if((await opsState(tx)).stopped)return fail("emergency_paused",409);
  const text=typeof d.text==="string"?d.text.trim():"",limitKop=d.budget?.limitKop;
  if(!text||text.length>3000||!["low","medium","high"].includes(d.reasoning)||!kop(limitKop))return fail("invalid_developer_task");
  if(old)return old.text===text&&old.reasoning===d.reasoning&&old.limitKop===limitKop?{task:old}:fail("id_conflict",409);
  if(d.version!==policy.version)return fail("stale_settings",409);
  if(limitKop>policy.perTaskKop)return fail("developer_task_cap",409);
  // Queue ceilings are not reservations or actual spend; no execution is enabled.
  const task={id:d.id,text,reasoning:d.reasoning,limitKop,policyVersion:policy.version,createdAt:now,status:"queued_blocked",chargedRub:null,executed:false,repository:"kaokshi/kaokshi.github.io",branchPolicy:"isolated_pr_only"};
  await tx.put(key,task);return {task};
 });
}
