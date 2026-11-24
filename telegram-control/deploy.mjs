// Explicit one-time deployment of a new, isolated read-only Worker.
// No package downloads, paid model requests, GitHub writes or editorial changes.
import {readFile} from "node:fs/promises";
import {createHmac} from "node:crypto";
import {buildSnapshot,snapshotBindings} from "./snapshot.mjs";
import {activeDeployment,latestVersion,readBridgeProbe} from "./deploy-guard.mjs";
const SCRIPT="zen-editorial-control";
const account=process.env.CLOUDFLARE_ACCOUNT_ID||"";
const cfToken=process.env.CLOUDFLARE_API_TOKEN||"";
const botToken=process.env.TELEGRAM_BOT_TOKEN||"";
const owner=process.env.TELEGRAM_OWNER_ID||"";
const router=process.env.ROUTERAI_API_KEY||"";
const webhookSecret=process.env.TELEGRAM_WEBHOOK_SECRET||"";
const intakeSecret=process.env.ZEN_CORE_INTAKE_SECRET||"";
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
 const inspectOnly=process.env.ZEN_CONTROL_PREFLIGHT_ONLY==="true";
 requireOk(inspectOnly||process.env.ZEN_CONTROL_CONFIRM_UPDATE==="true","explicit_update_confirmation_required");
 requireOk(/^[a-fA-F0-9]{64}$/.test(intakeSecret),"intake_secret_required");
 requireOk(!webhookSecret||/^[a-zA-Z0-9_-]{32,256}$/.test(webhookSecret),"existing_webhook_secret_invalid");
 requireOk(/^[a-f0-9]{32}$/i.test(account)&&cfToken,"cloudflare_config_missing");
 requireOk(/^\d+:[A-Za-z0-9_-]+$/.test(botToken),"bot_token_missing_or_invalid");
 requireOk(/^[1-9]\d{0,15}$/.test(owner)&&Number.isSafeInteger(Number(owner)),"owner_id_missing_or_invalid");
 // Only print known-safe labels, never API responses or secret values.
 if(webhookSecret)console.log("::add-mask::"+webhookSecret);
 console.log("::add-mask::"+intakeSecret);
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
 requireOk(scripts.some(s=>s.id===SCRIPT||s.name===SCRIPT)&&previous.url===endpoint+"/telegram","existing_worker_required");
 stage="private_snapshot";
 const readToken=process.env.EDITORIAL_REVIEW_READ_TOKEN||"";
 requireOk(readToken&&readToken!==process.env.EDITORIAL_PRIVATE_TOKEN,"separate_read_token_required");
 const snapshot=await buildSnapshot(readToken);
 requireOk(snapshot!==null,"private_snapshot_unavailable");
 const privateBindings=snapshotBindings(snapshot);
 console.log("PRIVATE_SNAPSHOT_READY");
 stage="inspect_existing_deployment";
 const deploymentPath="/scripts/"+SCRIPT+"/deployments";
 const previousDeployment=activeDeployment(await cf(deploymentPath));
 const versionPath="/scripts/"+SCRIPT+"/versions?per_page=10&page=1";
 requireOk(latestVersion(await cf(versionPath))===previousDeployment.version,"latest_upload_differs_from_active_version");
 const expectedVersion=process.env.ZEN_CONTROL_EXPECTED_PREVIOUS_VERSION;
 if(expectedVersion)requireOk(expectedVersion===previousDeployment.version,"unexpected_previous_version");
 console.log("PREVIOUS_DEPLOYMENT_ID="+previousDeployment.id);
 console.log("PREVIOUS_VERSION_ID="+previousDeployment.version);
 stage="verify_existing_webhook_secret";
 if(webhookSecret){const oldProbe=await fetch(endpoint+"/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":webhookSecret,"Content-Type":"application/json"},body:"{}",redirect:"error",signal:AbortSignal.timeout(8000)});requireOk(oldProbe.status===200,"existing_webhook_secret_not_verified");}
 stage="prepare_worker";
 const code=await readFile(new URL("./worker.mjs",import.meta.url),"utf8");
 const bindings=[
  {type:"secret_text",name:"TELEGRAM_BOT_TOKEN",text:botToken},
  {type:"secret_text",name:"TELEGRAM_OWNER_ID",text:owner},
  {type:"inherit",name:"TELEGRAM_WEBHOOK_SECRET"}
 ];
 bindings.push(...privateBindings);
 bindings.push({type:"secret_text",name:"EDITORIAL_REVIEW_READ_TOKEN",text:readToken});
 if(router)bindings.push({type:"secret_text",name:"ROUTERAI_API_KEY",text:router});
 if(intakeSecret){requireOk(/^[a-fA-F0-9]{64}$/.test(intakeSecret),"invalid_intake_secret");bindings.push({type:"secret_text",name:"ZEN_CORE_INTAKE_SECRET",text:intakeSecret});}
 const settings=await cf("/scripts/"+SCRIPT+"/settings");
 requireOk(Array.isArray(settings?.bindings)&&settings.bindings.some(b=>b.name==="TELEGRAM_WEBHOOK_SECRET"&&b.type==="secret_text"),"existing_webhook_binding_required");
 const hasJournal=Array.isArray(settings?.bindings)&&settings.bindings.some(b=>b.name==="REVIEW_JOURNAL"&&b.type==="durable_object_namespace"&&b.class_name==="ReviewJournal");
 bindings.push({type:"inherit",name:"REVIEW_JOURNAL"});
 requireOk(hasJournal,"existing_journal_binding_required");
 const migrations={};
 const form=new FormData();
 form.set("metadata",new Blob([JSON.stringify({main_module:"entry.mjs",compatibility_date:"2026-11-24",bindings,...migrations,observability:{enabled:false}})],{type:"application/json"}));
 form.set("worker.mjs",new Blob([code],{type:"application/javascript+module"}),"worker.mjs");
 for(const module of ["entry.mjs","miniapp.mjs","ui.mjs","snapshot.mjs","journal.mjs","core.mjs","agent.mjs","production.mjs","budget.mjs","ops.mjs","adapter.mjs","intake.mjs","receipt.mjs","memory.mjs","notifications.mjs","diagnostics.mjs","recovery.mjs","account.mjs","release.mjs","probe.mjs","stage-intent.mjs"]){
  const source=await readFile(new URL("./"+module,import.meta.url),"utf8");
  form.set(module,new Blob([source],{type:"application/javascript+module"}),module);
 }
 if(inspectOnly){console.log("PREFLIGHT_READY_NO_CHANGES");return;}
 const beforeUpload=activeDeployment(await cf(deploymentPath));
 requireOk(beforeUpload.id===previousDeployment.id&&beforeUpload.version===previousDeployment.version,"deployment_changed_during_preflight");
 requireOk(latestVersion(await cf(versionPath))===previousDeployment.version,"latest_upload_changed_during_preflight");
 stage="upload_worker";
 await cf("/scripts/"+SCRIPT+"?bindings_inherit=strict","PUT",form);
 const updatedDeployment=activeDeployment(await cf(deploymentPath));
 requireOk(updatedDeployment.id!==previousDeployment.id&&updatedDeployment.version!==previousDeployment.version,"new_deployment_unverified");
 console.log("NEW_DEPLOYMENT_ID="+updatedDeployment.id);
 console.log("NEW_VERSION_ID="+updatedDeployment.version);
 // The existing working subdomain is preserved; no routing change is needed.
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
 if(webhookSecret){
 let ignored;
 for(let i=0;i<8;i++){
 ignored=await fetch(endpoint+"/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":webhookSecret,"Content-Type":"application/json"},body:JSON.stringify({message:{chat:{type:"private",id:0},from:{id:0,is_bot:false},text:"/balance"}}),redirect:"error",signal:AbortSignal.timeout(8000)});
 if(ignored.status===200)break;
 if(ignored.status!==403)break;
 await new Promise(r=>setTimeout(r,2500));
 }
 requireOk(ignored?.status===200,"non_owner_gate_failed_"+ignored?.status);
 }
 // Existing webhook and verified secret are deliberately unchanged.
 stage="verify_webhook";
 const info=await telegram("getWebhookInfo");
 requireOk(info.url===endpoint+"/telegram","webhook_url_mismatch");
 stage="verify_miniapp";
 const page=await fetch(endpoint+"/app?v=12",{redirect:"error",signal:AbortSignal.timeout(8000)});
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
 stage="verify_review_journal";
 requireOk(dashboard.reviewEnabled===true,"journal_binding_missing");
 const journalRequest=async(path,body)=>fetch(endpoint+path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),redirect:"error",signal:AbortSignal.timeout(20000)});
 const deniedJournal=await journalRequest("/api/journal",{initData:"invalid"});
 requireOk(deniedJournal.status===401,"journal_auth_gate_failed");
 const journal=await journalRequest("/api/journal",{initData:params.toString()});
 requireOk(journal.ok,"journal_read_http_"+journal.status);
 requireOk(Array.isArray((await journal.json()).entries),"journal_response_invalid");
 const invalid=await journalRequest("/api/review",{initData:params.toString(),action:"publish"});
 requireOk(invalid.status===400,"review_action_gate_failed");
 console.log("REVIEW_JOURNAL_CONFIRMED");
 console.log("REVIEW_MUTATION_GATE_CONFIRMED");
 stage="verify_release";
 const rel=await journalRequest("/api/release",{initData:params.toString()});requireOk(rel.ok,"release_unavailable");const rv=await rel.json();requireOk(rv.version==="1.0.0-rc.1"&&rv.productionEnabled===false&&rv.publicationAuthorized===false&&rv.rssEnabled===false,"release_gate_failed");
 stage="verify_core";
 const core=await journalRequest("/api/core",{initData:params.toString()});
 requireOk(core.ok,"core_read_http_"+core.status);
 const state=await core.json();
 requireOk(state.mode==="dry_run"&&state.productionEnabled===false&&state.rssEnabled===false,"core_safety_gate_failed");
 const deniedCore=await journalRequest("/api/core",{initData:"invalid"});
 requireOk(deniedCore.status===401,"core_auth_gate_failed");
 const invalidSchedule=await journalRequest("/api/core/schedule",{initData:params.toString(),settings:{},version:0});
 requireOk(invalidSchedule.status===400,"core_validation_failed");
 const rss=await journalRequest("/api/rss",{initData:params.toString()});
 requireOk(rss.ok,"rss_status_failed");
 const feeds=await rss.json();
 requireOk(feeds.deliveryEnabled===false&&feeds.feeds?.length===3,"rss_gate_failed");
 console.log("CORE_DRY_RUN_CONFIRMED");
 console.log("RSS_STATUS_CONFIRMED");
 stage="verify_qwen_chat";
 const agent=await journalRequest("/api/core/agent",{initData:params.toString()});
 requireOk(agent.ok,"agent_http_"+agent.status);
 const av=await agent.json();
 requireOk(typeof av.enabled==="boolean"&&av.mode==="owner_chat_only"&&av.limit===10&&av.perCall===2&&Array.isArray(av.messages),"agent_config_invalid");
 const deniedAgent=await journalRequest("/api/core/agent/chat",{initData:"invalid"});
 requireOk(deniedAgent.status===401,"agent_auth_gate_failed");
 const noConsent=await journalRequest("/api/core/agent/chat",{initData:params.toString(),text:"test",consent:false});
 requireOk(noConsent.status===400,"agent_consent_gate_failed");
 const noApply=await journalRequest("/api/core/agent/apply",{initData:params.toString(),confirm:false});
 requireOk(noApply.status===400,"agent_confirmation_gate_failed");
 console.log("QWEN_CHAT_ENDPOINT_CONFIRMED");
 console.log("QWEN_PAID_TEST_NOT_RUN");
 stage="verify_budget_and_jobs";
 const budget=await journalRequest("/api/core/budget",{initData:params.toString()});
 requireOk(budget.ok,"budget_http_"+budget.status);
 const bv=await budget.json();
 requireOk(bv.policy.reserveKop===5000&&bv.executionEnabled===false&&bv.production.ledgerConnected===false&&bv.production.reserveRemainingKop===null,"budget_gate_failed");
 const deniedBudget=await journalRequest("/api/core/budget",{initData:"invalid"});
 requireOk(deniedBudget.status===401,"budget_auth_gate_failed");
 const invalidBudget=await journalRequest("/api/core/budget/settings",{initData:params.toString(),confirm:false});
 requireOk(invalidBudget.status===400,"budget_confirmation_failed");
 const queue=await journalRequest("/api/core/jobs",{initData:params.toString()});
 requireOk(queue.ok,"queue_http_"+queue.status);
 const qv=await queue.json();
 requireOk(qv.productionEnabled===false&&Array.isArray(qv.jobs)&&qv.stages.length===8&&qv.readiness.canExecute===false,"queue_gate_failed");
 console.log("BUDGET_POLICY_CONFIRMED");
 console.log("PRODUCTION_QUEUE_CONFIRMED");
 stage="verify_ops";
 const ops=await journalRequest("/api/core/ops",{initData:params.toString()});
 requireOk(ops.ok,"ops_http_"+ops.status);
 const ov=await ops.json();
 requireOk(ov.scope==="zen_control_only"&&ov.externalStopConnected===false&&typeof ov.state.stopped==="boolean"&&Array.isArray(ov.events),"ops_state_invalid");
 const deniedOps=await journalRequest("/api/core/ops",{initData:"invalid"});
 requireOk(deniedOps.status===401,"ops_auth_gate_failed");
 const invalidOps=await journalRequest("/api/core/ops/settings",{initData:params.toString(),confirm:false});
 requireOk(invalidOps.status===400,"ops_confirmation_failed");
 console.log("OPS_CONTROLS_CONFIRMED");
 stage="verify_mode_ui";
 const uiPage=await fetch(endpoint+"/app?v=12",{redirect:"error",signal:AbortSignal.timeout(10000)});
 requireOk(uiPage.ok&&(await uiPage.text()).includes('id="coremode"'),"mode_panel_not_deployed");
 const uiScript=await fetch(endpoint+"/app.js?v=1.0.0-rc.1-mode2",{redirect:"error",signal:AbortSignal.timeout(10000)});
 requireOk(uiScript.ok&&(await uiScript.text()).includes("function renderModeControl"),"mode_script_not_deployed");
 console.log("MODE_UI_CONFIRMED");
 stage="verify_private_bridge";
 const bridge=async(path,body,auth)=>fetch(endpoint+"/internal/zen-core/"+path,{method:"POST",headers:{"Content-Type":"application/json",...(auth?{Authorization:"Bearer "+intakeSecret}:{})},body:JSON.stringify(body),redirect:"error",signal:AbortSignal.timeout(10000)});
 requireOk((await bridge("queue",{},false)).status===401,"bridge_auth_failed");
 await readBridgeProbe(()=>bridge("queue",{},true),v=>v.schema==="zen-core/queue-v1"&&Array.isArray(v.ids)&&v.executionEnabled===false&&v.rssEnabled===false&&v.publicationAuthorized===false);
 await readBridgeProbe(()=>bridge("account",{action:"status"},true),v=>v.schema==="zen-core/account-status-v1"&&v.executionEnabled===false);
 await readBridgeProbe(()=>bridge("control",{id:"deploy-probe-not-a-real-job"},true),v=>v.schema==="zen-core/control-v1"&&v.controlClear===false&&v.executionEnabled===false&&v.publicationAuthorized===false&&v.rssEnabled===false);
 console.log("PRIVATE_BRIDGE_READ_ONLY_CONFIRMED");
 const diag=await journalRequest("/api/core/diagnostics",{initData:params.toString()});
 requireOk(diag.ok,"diagnostics_unavailable");
 const dv=await diag.json();requireOk(dv.mode==="read_only"&&dv.productionEnabled===false&&dv.publicationAuthorized===false,"diagnostics_gate_failed");
 // Existing cron settings are not overwritten by a normal deployment.
 // Explicit operator configuration is required to enable a five-minute watchdog.
 console.log("WATCHDOG_CRON_REQUIRES_OPERATOR_CONFIGURATION");




 console.log("PRIVATE_REVIEW_CONFIRMED");
 console.log("LIVE_REVIEW_SYNC="+(dashboard.sync==="live"));
 console.log("IMAGE_PREVIEW_CONFIGURED="+(dashboard.imagePreview===true));
 console.log("MINIAPP_AUTH_CONFIRMED");
 console.log("MINIAPP_BALANCE_AVAILABLE="+(typeof dashboard.balance==="number"));
 if(webhookSecret){
 stage="verify_owner_reply";
 const ownerReply=await fetch(endpoint+"/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":webhookSecret,"Content-Type":"application/json"},body:JSON.stringify({message:{chat:{type:"private",id:Number(owner)},from:{id:Number(owner),is_bot:false},text:"/start"}}),redirect:"error",signal:AbortSignal.timeout(20000)});
 requireOk(ownerReply.ok,"owner_reply_http_"+ownerReply.status);
 console.log("OWNER_REPLY_CONFIRMED");
 }else console.log("OWNER_WEBHOOK_REPLY_REQUIRES_MANUAL_TEST");
 stage="miniapp_menu";
 requireOk(await telegram("setChatMenuButton",{chat_id:Number(owner),menu_button:{type:"web_app",text:"ZEN CONTROL",web_app:{url:endpoint+"/app?v=12"}}})===true,"miniapp_menu_failed");
 const menu=await telegram("getChatMenuButton",{chat_id:Number(owner)});
 requireOk(menu.type==="web_app"&&menu.web_app.url===endpoint+"/app?v=12","miniapp_menu_not_verified");
 console.log("MINIAPP_MENU_CONFIRMED");
 stage="send_inline_launch";
 const launch=await telegram("sendMessage",{chat_id:Number(owner),text:"ZEN CONTROL обновлён. Закройте старое окно и войдите через кнопку ниже.",reply_markup:{inline_keyboard:[[{text:"Открыть ZEN CONTROL",web_app:{url:endpoint+"/app?v=12"}}]]}});
 requireOk(Number.isInteger(launch?.message_id),"inline_launch_failed");
 console.log("INLINE_LAUNCH_SENT");
 console.log("MINIAPP_URL="+endpoint+"/app?v=12");
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
