// Isolated simulation ledger. Never a live spending authorization.
// Only trusted server-side callers with verified provider receipts may eventually
// use a separate production ledger. No endpoint accepts simulated receipts.
import {budgetDecision,RESERVE_KOP,validateBudget} from "./budget.mjs";
const P="simulation:spend:";
const money=n=>Number.isSafeInteger(n)&&n>=0&&n<=100000000;
const idOk=s=>typeof s==="string"&&/^[a-zA-Z0-9:_-]{1,180}$/.test(s);
const dayAt=now=>new Date(now+10800000).toISOString().slice(0,10);
const totals=()=>({mainKop:0,reserveKop:0});
const deny=reason=>({allowed:false,reason,mode:"simulation",liveAuthorized:false});
const kinds=new Set(["new_article","draft","edit","image","missing_image","image_repair","required_final_check","required_revision"]);
function validContext(c){
 return c?.mode==="simulation"&&c.configVerified===true&&c.policy?.ownerSaved===true&&validateBudget(c.policy)&&money(c.configDailyKop)&&c.configDailyKop===c.policy.mainKop&&money(c.monthlyCapKop)&&c.monthlyCapKop>0&&money(c.availableKop);
}
function validOperation(o){
 return idOk(o?.id)&&idOk(o.articleId)&&kinds.has(o.kind)&&money(o.boundKop)&&o.boundKop>0&&Number.isInteger(o.attempt)&&o.attempt>=1&&o.attempt<=3&&typeof o.required==="boolean";
}
const same=(a,b)=>["id","articleId","kind","boundKop","attempt","required"].every(k=>a[k]===b[k]);
const opKey=o=>P+"op:"+o.id;
const dayKey=d=>P+"day:"+d;
const monthKey=m=>P+"month:"+m;
const articleKey=a=>P+"article:"+a;
export async function simulateReserve(storage,context,op,now=Date.now()){
 if(!validContext(context)||!validOperation(op))return deny("unverified_context_or_price");
 const day=dayAt(now),month=day.slice(0,7);
 return storage.transaction(async tx=>{
  if((await tx.get("ops:state"))?.stopped===true)return deny("emergency_paused");
  if(await tx.get(P+"halt"))return deny("reconciliation_required");
  const existing=await tx.get(opKey(op));
  if(existing)return deny(same(existing.operation,op)?"already_reserved":"id_conflict");
  const daily=await tx.get(dayKey(day))||totals();
  const monthly=await tx.get(monthKey(month))||0;
  const outstanding=await tx.get(P+"outstanding")||0;
  const settled=await tx.get(P+"settled")||0;
  if(!money(daily.mainKop)||!money(daily.reserveKop)||!money(monthly)||!money(outstanding)||!money(settled))return deny("invalid_ledger");
  if(monthly+op.boundKop>context.monthlyCapKop)return deny("monthly_cap");
  // availableKop is the fixture opening funds, not a live balance snapshot.
  if(settled+outstanding+op.boundKop>context.availableKop)return deny("insufficient_balance");
  const article=await tx.get(articleKey(op.articleId));
  // A reservation alone is not evidence that the article was started.
  const started=article?.status==="started";
  if(article?.status==="finished")return deny("article_finished");
  if((started||article?.status==="starting")&&op.kind==="new_article")return deny("article_already_started");
  if(op.kind!=="new_article"&&!started)return deny("article_not_started");
  const d=budgetDecision(context.policy,{...daily,verified:true},{...op,started});
  if(!d.allowed)return deny(d.reason);
  const operation=Object.fromEntries(["id","articleId","kind","boundKop","attempt","required"].map(k=>[k,op[k]]));
  const receipt={operation,day,month,mainKop:d.mainKop,reserveKop:d.reserveKop,status:"reserved",mode:"simulation",liveAuthorized:false};
  await tx.put(opKey(op),receipt);
  if(op.kind==="new_article")await tx.put(articleKey(op.articleId),{status:"starting",evidenceId:op.id});
  await tx.put(dayKey(day),{mainKop:daily.mainKop+d.mainKop,reserveKop:daily.reserveKop+d.reserveKop});
  await tx.put(monthKey(month),monthly+op.boundKop);
  await tx.put(P+"outstanding",outstanding+op.boundKop);
  return {allowed:true,...receipt};
 });
}
export async function simulateSettle(storage,id,result){
 if(!idOk(id)||!["charged","not_sent","unknown"].includes(result?.status)||result.status==="charged"&&!money(result.actualKop)||result.status==="not_sent"&&result.actualKop!==0)return deny("invalid_receipt");
 return storage.transaction(async tx=>{
  const key=P+"op:"+id,old=await tx.get(key);
  if(!old)return deny("reservation_missing");
  if(old.status!=="reserved")return deny("already_reconciled");
  if(result.status==="unknown"){
   await tx.put(key,{...old,status:"unknown"});await tx.put(P+"halt",{reason:"unknown_charge",id});
   return deny("reconciliation_required");
  }
  const actual=result.actualKop,bound=old.operation.boundKop;
  if(actual>bound){
   // Preserve full hold, record excess separately and halt all new reservations.
   await tx.put(key,{...old,status:"overrun",actualKop:actual});
   await tx.put(P+"halt",{reason:"price_overrun",id,actualKop:actual,unaccountedKop:actual-bound});
   return deny("reconciliation_required");
  }
  const main=Math.min(actual,old.mainKop),reserve=actual-main;
  const daily=await tx.get(dayKey(old.day)),monthly=await tx.get(monthKey(old.month)),outstanding=await tx.get(P+"outstanding");
  if(!daily||daily.mainKop<old.mainKop||daily.reserveKop<old.reserveKop||monthly<bound||outstanding<bound)return deny("invalid_ledger");
  await tx.put(dayKey(old.day),{mainKop:daily.mainKop-old.mainKop+main,reserveKop:daily.reserveKop-old.reserveKop+reserve});
  await tx.put(monthKey(old.month),monthly-bound+actual);
  await tx.put(P+"outstanding",outstanding-bound);
  await tx.put(P+"settled",(await tx.get(P+"settled")||0)+actual);
  const receipt={...old,status:result.status,actualKop:actual,mainKop:main,reserveKop:reserve};
  await tx.put(key,receipt);
  if(old.operation.kind==="new_article")await tx.put(articleKey(old.operation.articleId),{status:result.status==="charged"?"started":"not_started",evidenceId:id});
  return {allowed:true,...receipt};
 });
}
// Explicit synthetic completion evidence; never called from Telegram or live QA.
export async function simulateFinishArticle(storage,articleId){
 if(!idOk(articleId))return deny("invalid_article");
 return storage.transaction(async tx=>{
  const key=articleKey(articleId),article=await tx.get(key);
  if(article?.status!=="started")return deny("article_not_started");
  await tx.put(key,{...article,status:"finished"});
  return {allowed:true,mode:"simulation",liveAuthorized:false};
 });
}
export async function simulationSummary(storage,now=Date.now()){
 const day=dayAt(now);
 return storage.transaction(async tx=>({mode:"simulation",liveAuthorized:false,day,accounted:await tx.get(dayKey(day))||totals(),monthlyAccountedKop:await tx.get(monthKey(day.slice(0,7)))||0,outstandingKop:await tx.get(P+"outstanding")||0,settledKop:await tx.get(P+"settled")||0,halt:await tx.get(P+"halt")||null,reserveCapKop:RESERVE_KOP}));
}
