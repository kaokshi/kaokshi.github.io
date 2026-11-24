import {HTML,CSS,JS} from "./ui.mjs";
const encoder=new TextEncoder();
async function hmac(key,data){
 const k=await crypto.subtle.importKey("raw",key,{name:"HMAC",hash:"SHA-256"},false,["sign"]);
 return new Uint8Array(await crypto.subtle.sign("HMAC",k,encoder.encode(data)));
}
export async function verifyInitData(raw,env,now=Date.now()/1000){
 if(typeof raw!=="string"||raw.length>12000||!env.TELEGRAM_BOT_TOKEN||!/^[1-9]\d{0,15}$/.test(env.TELEGRAM_OWNER_ID||""))return false;
 try{
  const p=new URLSearchParams(raw),keys=[...p.keys()];
  if(new Set(keys).size!==keys.length)return false;
  const hash=p.get("hash");
  if(!/^[0-9a-f]{64}$/.test(hash||""))return false;
  p.delete("hash");
  const data=[...p.entries()].sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,v])=>k+"="+v).join("\n");
  const key=await hmac(encoder.encode("WebAppData"),env.TELEGRAM_BOT_TOKEN);
  const vk=await crypto.subtle.importKey("raw",key,{name:"HMAC",hash:"SHA-256"},false,["verify"]);
  if(!await crypto.subtle.verify("HMAC",vk,Uint8Array.from(hash.match(/../g),h=>parseInt(h,16)),encoder.encode(data)))return false;
  const stamp=p.get("auth_date");
  if(!/^\d{1,12}$/.test(stamp||"")||Number(stamp)>now+30||now-Number(stamp)>3600)return false;
  const user=JSON.parse(p.get("user")||"null");
  return Number.isSafeInteger(user?.id)&&String(user.id)===env.TELEGRAM_OWNER_ID&&user.is_bot!==true;
 }catch{return false;}
}
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer"};
function serve(body,type){return new Response(body,{headers:{...headers,"Content-Type":type,"Content-Security-Policy":"default-src 'none'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"} });}
export async function miniApp(request,env,getBalance){
 const path=new URL(request.url).pathname;
 if(request.method==="GET"){
  if(path==="/"||path==="/app")return serve(HTML,"text/html; charset=utf-8");
  if(path==="/app.css")return serve(CSS,"text/css; charset=utf-8");
  if(path==="/app.js")return serve(JS,"application/javascript; charset=utf-8");
 }
 if(path!=="/api/dashboard")return null;
 if(request.method!=="POST")return new Response("Method not allowed",{status:405,headers});
 let data;
 try{if(Number(request.headers.get("Content-Length")||0)>16000)throw Error();const raw=await request.text();if(raw.length>16000)throw Error();data=JSON.parse(raw);}catch{return new Response("Bad request",{status:400,headers});}
 if(!await verifyInitData(data?.initData,env))return new Response("Unauthorized",{status:401,headers});
 let balance=null;
 try{balance=await getBalance(env);}catch{}
 return Response.json({balance,checkedAt:new Date().toISOString(),mode:"read_only"},{headers});
}
