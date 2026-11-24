// Explicit one-time deployment of a new, isolated read-only Worker.
// No package downloads, paid model requests, GitHub writes or editorial changes.
import {readFile} from "node:fs/promises";
import {randomBytes,createHmac} from "node:crypto";
import {buildSnapshot,snapshotBindings} from "./snapshot.mjs";
const SCRIPT="zen-editorial-control";
const account=process.env.CLOUDFLARE_ACCOUNT_ID||"";
const cfToken=process.env.CLOUDFLARE_API_TOKEN||"";
const botToken=process.env.TELEGRAM_BOT_TOKEN||"";
const owner=process.env.TELEGRAM_OWNER_ID||"";
const router=process.env.ROUTERAI_API_KEY||"";
const webhookSecret=randomBytes(32).toString("hex");
let stage="preflight";
function requireOk(value,code){if(!value)throw new Error(code);}
async function request(url,options){
 const response=await fetch(url,{...options,redirect:"error",signal:AbortSignal.timeout(25000)});
 let data;
 try{data=await response.json();}catch{throw new Error("invalid_upstream_json");}
 if(!response.ok)throw new Error("http_"+response.status);
 return data;
}
const root="https://api.cloudflare.com/client/v4/accounts/"+account+"/workers";
async function cf(path,method="GET",body){
 const headers={Authorization:"Bearer "+cfToken};
 if(body&&!(body instanceof FormData))headers["Content-Type"]="application/json";
 const res=await request(root+path,{method,headers,body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 requireOk(res.success===true,"cloudflare_rejected");
 return res.result;
}
async function telegram(method,payload={}){
 const res=await request("https://api.telegram.org/bot"+botToken+"/"+method,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
 requireOk(res.ok===true,"telegram_rejected");
 return res.result;
}
async function main(){
 requireOk(/^[a-f0-9]{32}$/i.test(account)&&cfToken,"cloudflare_config_missing");
 requireOk(/^\d+:[A-Za-z0-9_-]+$/.test(botToken),"bot_token_missing_or_invalid");
 requireOk(/^[1-9]\d{0,15}$/.test(owner)&&Number.isSafeInteger(Number(owner)),"owner_id_missing_or_invalid");
 // Only print known-safe labels, never API responses or secret values.
 console.log("::add-mask::"+webhookSecret);
 stage="telegram_identity";
 const me=await telegram("getMe");
 requireOk(me.is_bot===true&&/^[A-Za-z0-9_]{5,32}$/.test(me.username||""),"bot_identity_invalid");
 stage="cloudflare_read_access";
 const sub=await cf("/subdomain");
 requireOk(/^[a-z0-9][a-z0-9-]{0,62}$/.test(sub?.subdomain||""),"workers_subdomain_not_configured");
 const endpoint="https://"+SCRIPT+"."+sub.subdomain+".workers.dev";
 stage="check_existing_webhook";
 const previous=await telegram("getWebhookInfo");
 requireOk(!previous.url || previous.url===endpoint+"/telegram","existing_other_webhook_refused");
 stage="check_existing_worker";
 const scripts=await cf("/scripts");
 requireOk(Array.isArray(scripts),"worker_list_invalid");
 // Never overwrite an unrelated existing script. An existing script without an
 // associated webhook requires an owner-reviewed recovery procedure.
 requireOk(!scripts.some(s=>s.id===SCRIPT||s.name===SCRIPT)||previous.url===endpoint+"/telegram","existing_worker_refused");
 stage="private_snapshot";
 const readToken=process.env.EDITORIAL_REVIEW_READ_TOKEN||"";
 requireOk(readToken&&readToken!==process.env.EDITORIAL_PRIVATE_TOKEN,"separate_read_token_required");
 const snapshot=await buildSnapshot(readToken);
 requireOk(snapshot!==null,"private_snapshot_unavailable");
 const privateBindings=snapshotBindings(snapshot);
 console.log("PRIVATE_SNAPSHOT_READY");
 stage="upload_worker";
 const code=await readFile(new URL("./worker.mjs",import.meta.url),"utf8");
 const bindings=[
  {type:"secret_text",name:"TELEGRAM_BOT_TOKEN",text:botToken},
  {type:"secret_text",name:"TELEGRAM_OWNER_ID",text:owner},
  {type:"secret_text",name:"TELEGRAM_WEBHOOK_SECRET",text:webhookSecret}
 ];
 bindings.push(...privateBindings);
 bindings.push({type:"secret_text",name:"EDITORIAL_REVIEW_READ_TOKEN",text:readToken});
 if(router)bindings.push({type:"secret_text",name:"ROUTERAI_API_KEY",text:router});
 const form=new FormData();
 form.set("metadata",new Blob([JSON.stringify({main_module:"worker.mjs",compatibility_date:"2026-11-24",bindings,observability:{enabled:false}})],{type:"application/json"}));
 form.set("worker.mjs",new Blob([code],{type:"application/javascript+module"}),"worker.mjs");
 for(const module of ["miniapp.mjs","ui.mjs","snapshot.mjs"]){
  const source=await readFile(new URL("./"+module,import.meta.url),"utf8");
  form.set(module,new Blob([source],{type:"application/javascript+module"}),module);
 }
 await cf("/scripts/"+SCRIPT,"PUT",form);
 stage="enable_worker_subdomain";
 await cf("/scripts/"+SCRIPT+"/subdomain","POST",{enabled:true,previews_enabled:false});
 stage="verify_worker_security";
 let ready=false;
 for(let i=0;i<8;i++){
  try{
   const health=await fetch(endpoint+"/health",{redirect:"error",signal:AbortSignal.timeout(8000)});
   if(health.ok&&(await health.text())==="ok"){ready=true;break;}
  }catch{}
  await new Promise(r=>setTimeout(r,3000));
 }
 requireOk(ready,"worker_health_failed");
 const denied=await fetch(endpoint+"/telegram",{method:"POST",body:"{}",redirect:"error",signal:AbortSignal.timeout(8000)});
 requireOk(denied.status===403,"webhook_secret_gate_failed");
 let ignored;
 for(let i=0;i<8;i++){
 ignored=await fetch(endpoint+"/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":webhookSecret,"Content-Type":"application/json"},body:JSON.stringify({message:{chat:{type:"private",id:0},from:{id:0,is_bot:false},text:"/balance"}}),redirect:"error",signal:AbortSignal.timeout(8000)});
 if(ignored.status===200)break;
 if(ignored.status!==403)break;
 await new Promise(r=>setTimeout(r,2500));
 }
 requireOk(ignored?.status===200,"non_owner_gate_failed_"+ignored?.status);
 stage="register_webhook";
 requireOk(await telegram("setWebhook",{url:endpoint+"/telegram",secret_token:webhookSecret,allowed_updates:["message"],max_connections:2,drop_pending_updates:false})===true,"webhook_registration_failed");
 stage="verify_webhook";
 const info=await telegram("getWebhookInfo");
 requireOk(info.url===endpoint+"/telegram","webhook_url_mismatch");
 stage="verify_owner_reply";
 const ownerReply=await fetch(endpoint+"/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":webhookSecret,"Content-Type":"application/json"},body:JSON.stringify({message:{chat:{type:"private",id:Number(owner)},from:{id:Number(owner),is_bot:false},text:"/start"}}),redirect:"error",signal:AbortSignal.timeout(20000)});
 requireOk(ownerReply.ok,"owner_reply_http_"+ownerReply.status);
 console.log("OWNER_REPLY_CONFIRMED");
 stage="verify_miniapp";
 const page=await fetch(endpoint+"/app?v=5",{redirect:"error",signal:AbortSignal.timeout(8000)});
 requireOk(page.ok&&(await page.text()).includes("ZEN CONTROL"),"app_page_unavailable");
 const unauthorized=await fetch(endpoint+"/api/dashboard",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initData:"invalid"}),redirect:"error",signal:AbortSignal.timeout(8000)});
 requireOk(unauthorized.status===401,"app_auth_gate_failed");
 const params=new URLSearchParams({auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:Number(owner),is_bot:false}),query_id:"deploy-test"});
 const data=[...params.entries()].sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,v])=>k+"="+v).join("\n");
 const signKey=createHmac("sha256","WebAppData").update(botToken).digest();
 params.set("hash",createHmac("sha256",signKey).update(data).digest("hex"));
 const appResponse=await fetch(endpoint+"/api/dashboard",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initData:params.toString()}),redirect:"error",signal:AbortSignal.timeout(15000)});
 requireOk(appResponse.ok,"signed_dashboard_failed");
 const dashboard=await appResponse.json();
 requireOk(dashboard.mode==="read_only","app_mode_invalid");
 requireOk(dashboard.review?.articles?.length>0,"private_snapshot_delivery_failed");
 requireOk(dashboard.sync==="live","live_review_sync_failed");
 stage="verify_private_image";
 const sample=dashboard.review.articles.flatMap(a=>a.images||[]).find(im=>im.path);
 requireOk(sample&&dashboard.review.revision,"no_image_sample");
 const imgBody={initData:params.toString(),revision:dashboard.review.revision,path:sample.path};
 const imageRequest=async body=>fetch(endpoint+"/api/image",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),redirect:"error",signal:AbortSignal.timeout(20000)});
 const badImage=await imageRequest({...imgBody,initData:"invalid"});
 requireOk(badImage.status===401,"image_auth_gate_failed");
 const goodImage=await imageRequest(imgBody);
 requireOk(goodImage.ok&&["image/jpeg","image/png"].includes(goodImage.headers.get("Content-Type")),"private_image_delivery_failed_"+goodImage.status);
 const imageBytes=await goodImage.arrayBuffer();
 requireOk(imageBytes.byteLength>100,"private_image_empty");
 console.log("PRIVATE_IMAGE_CONFIRMED");
 console.log("PRIVATE_REVIEW_CONFIRMED");
 console.log("LIVE_REVIEW_SYNC="+(dashboard.sync==="live"));
 console.log("IMAGE_PREVIEW_CONFIGURED="+(dashboard.imagePreview===true));
 console.log("MINIAPP_AUTH_CONFIRMED");
 console.log("MINIAPP_BALANCE_AVAILABLE="+(typeof dashboard.balance==="number"));
 stage="miniapp_menu";
 requireOk(await telegram("setChatMenuButton",{chat_id:Number(owner),menu_button:{type:"web_app",text:"ZEN CONTROL",web_app:{url:endpoint+"/app?v=5"}}})===true,"miniapp_menu_failed");
 const menu=await telegram("getChatMenuButton",{chat_id:Number(owner)});
 requireOk(menu.type==="web_app"&&menu.web_app.url===endpoint+"/app?v=5","miniapp_menu_not_verified");
 console.log("MINIAPP_MENU_CONFIRMED");
 stage="send_inline_launch";
 const launch=await telegram("sendMessage",{chat_id:Number(owner),text:"ZEN CONTROL обновлён. Закройте старое окно и войдите через кнопку ниже.",reply_markup:{inline_keyboard:[[{text:"Открыть ZEN CONTROL",web_app:{url:endpoint+"/app?v=5"}}]]}});
 requireOk(Number.isInteger(launch?.message_id),"inline_launch_failed");
 console.log("INLINE_LAUNCH_SENT");
 console.log("MINIAPP_URL="+endpoint+"/app?v=5");
 stage="commands";
 try { requireOk(await telegram("setMyCommands",{scope:{type:"chat",chat_id:Number(owner)},commands:[
  {command:"start",description:"Главное меню"},{command:"status",description:"Состояние редакции"},
  {command:"balance",description:"Баланс RouterAI"},{command:"models",description:"Модели"},
  {command:"articles",description:"Статьи"},{command:"tests",description:"Тесты"},
  {command:"expenses",description:"Расходы"},{command:"help",description:"Помощь"}
 ]})===true,"commands_registration_failed"); } catch { console.log("COMMAND_MENU_PENDING_OWNER_START"); }
 console.log("DEPLOY_OK");
 console.log("BOT_LINK=https://t.me/"+me.username);
 console.log("BALANCE_CONFIGURED="+Boolean(router));
 console.log("PRIVATE_ARTICLE_SNAPSHOT_CONNECTED");
 console.log("OWNER_START_REQUIRED");
}
try{await main();}catch(e){
 // Only known safe error codes are allowed. Fetch errors may contain credentials.
 const value=String(e?.message||"");
 const safe=/^(?:http_\d{3}|[a-z][a-z0-9_]{2,80})$/.test(value)?value:"operation_failed";
 console.log("DEPLOY_BLOCKED stage="+stage+" reason="+safe);
 process.exitCode=1;
}
