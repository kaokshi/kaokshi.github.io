// Notifications reflect validated executor reports, not independent verification.
// The caller has already persisted the report. No model or publishing calls.
import {opsState} from "./ops.mjs";
const labels={plan:"План",draft:"Черновик",edit:"Редактура",visual_plan:"План иллюстраций",images:"Иллюстрации",qa:"Проверки",package:"Сборка"};
export function reportEvents(old,next){
 const events=[];
 if(next.budget.unsettledRequest&&!old?.budget.unsettledRequest)events.push({id:"unsettled",label:"Есть несверенное списание. Требуется сверка."});
 if(Number(next.budget.mainRemainingRub)===0&&(!old||old.budget.day!==next.budget.day||Number(old.budget.mainRemainingRub)>0))events.push({id:"main-limit:"+next.budget.day,label:"Основной бюджет исчерпан по отчёту исполнителя."});
 if(Number(next.budget.reserveRemainingRub)===0&&(!old||old.budget.day!==next.budget.day||Number(old.budget.reserveRemainingRub)>0))events.push({id:"reserve-limit:"+next.budget.day,label:"Резерв исчерпан по отчёту исполнителя."});
 for(const a of next.articles){
  const prev=old?.articles.find(x=>x.id===a.id);
  const name=a.id.split("/").slice(-2).join(" / ");
  for(const s of a.stages){
   if(["failed","blocked","passed"].includes(s.status)&&prev?.stages.find(x=>x.id===s.id)?.status!==s.status)
    events.push({id:a.id+":"+s.id+":"+s.status,label:name+" · "+labels[s.id]+" · "+({failed:"ошибка",blocked:"заблокировано",passed:"этап пройден"}[s.status])});
  }
 }
 return events;
}
export async function notifyReceipt(storage,env,receipt,events,now=Date.now()){
 if(!events.length||!env?.TELEGRAM_BOT_TOKEN||!/^[1-9]\d{0,15}$/.test(env.TELEGRAM_OWNER_ID||""))return;
 const key="ops:notification:"+String(receipt.observedAt).padStart(15,"0")+":receipt:"+receipt.jobId;
 const claim=await storage.transaction(async tx=>{
  if(!(await opsState(tx)).notifications||await tx.get(key))return false;
  await tx.put(key,{at:new Date(now).toISOString(),status:"sending",count:events.length,kind:"executor_report"});return true;
 });
 if(!claim)return;
 let status="unconfirmed";
 try{
  const text="ZEN CORE · отчёт исполнителя (тестовый контур)\n"+events.slice(0,15).map(e=>e.label).join("\n")+(events.length>15?"\nЕщё событий: "+(events.length-15):"")+"\nПубликация и платное исполнение не разрешены.";
  const r=await fetch("https://api.telegram.org/bot"+env.TELEGRAM_BOT_TOKEN+"/sendMessage",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:env.TELEGRAM_OWNER_ID,text,link_preview_options:{is_disabled:true}}),redirect:"manual",signal:AbortSignal.timeout(10000)});
  if(r.ok&&(await r.json()).ok===true)status="sent";
 }catch{}
 await storage.put(key,{at:new Date(now).toISOString(),status,count:events.length,kind:"executor_report"});
}
