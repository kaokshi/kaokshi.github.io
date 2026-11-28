import {createHmac} from "node:crypto";

const token = process.env.TELEGRAM_BOT_TOKEN;
const owner = process.env.TELEGRAM_OWNER_ID;
const desired = {model:"anthropic/claude-sonnet-5.5", reasoning:"medium", perCallRub:5, dailyRub:50};
const matches = settings => settings && Object.entries(desired).every(([key,value])=>settings[key]===value);
const check = (value,code) => { if (!value) throw new Error(code); };

async function request(path, data={}) {
 const params = new URLSearchParams({auth_date:String(Math.floor(Date.now()/1000)),
  user:JSON.stringify({id:Number(owner),is_bot:false}), query_id:"owner-recovery-check"});
 params.sort();
 const signed = Array.from(params, ([key,value])=>key+"="+value).join("\n");
 const key = createHmac("sha256","WebAppData").update(token).digest();
 params.set("hash",createHmac("sha256",key).update(signed).digest("hex"));
 const response = await fetch("https://zen-editorial-control.kvasrapr.workers.dev/api/core/"+path,{
  method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify({...data,initData:params.toString()}),redirect:"error",signal:AbortSignal.timeout(125000)});
 const body = await response.json();
 if (!response.ok) {
  const code = /^[a-z0-9_]{1,72}$/.test(body.error||"") ? body.error : "unknown";
  throw new Error("http_"+response.status+"_"+code);
 }
 return body;
}

try {
 check(token && /^\d+$/.test(owner||""),"owner_configuration_missing");
 const agent = await request("agent");
 check(agent.enabled===true && agent.chargeHold===false && agent.productionHold===false,"chat_gate_blocked");
 const {settings} = await request("agent/settings");
 if (!matches(settings)) {
  const saved = await request("agent/settings/write",{...desired,version:settings.version,confirm:true});
  check(matches(saved.settings),"sonnet_settings_write_unverified");
 }
 check(matches((await request("agent/settings")).settings),"sonnet_settings_unverified");
 console.log("SONNET_MEDIUM_LIMITS_VERIFIED");
 const {message} = await request("agent/chat",{id:"f6f8556a-622a-4f89-870c-202611280001", consent:true,
  text:"Ответь одной короткой фразой: связь с управляющим агентом работает?"});
 check(message?.status==="complete" && message.costVerified===true && Number.isFinite(message.cost)
   && message.cost>=0 && message.cost<=5 && typeof message.reply==="string" && message.reply.length>0,
  "sonnet_receipt_unverified");
 console.log("SONNET_CONFIRMED_RUB="+message.cost);
} catch(error) {
 console.log("SONNET_BLOCKED="+(/^[a-z0-9_]{1,100}$/.test(error.message)?error.message:"request_unavailable"));
 process.exitCode=1;
}
