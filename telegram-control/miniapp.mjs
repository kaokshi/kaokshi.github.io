import {HTML,CSS,JS} from "./ui.mjs";
import {buildSnapshot,readPrivateImage} from "./snapshot.mjs";
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
function serve(body,type){return new Response(body,{headers:{...headers,"Content-Type":type,"Content-Security-Policy":"default-src 'none'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; base-uri 'none'; form-action 'none'"} });}
export function readSnapshot(env){
 try{
  const count=Number(env.REVIEW_SNAPSHOT_COUNT);
  if(!Number.isInteger(count)||count<1||count>36)return null;
  let b64="";
  for(let i=0;i<count;i++){const chunk=env["REVIEW_SNAPSHOT_"+i];if(typeof chunk!=="string"||chunk.length>3500)return null;b64+=chunk;}
  const raw=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
  const parsed=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(raw));
  if(!/^[0-9]{8,20}$/.test(parsed.run||"")||!Array.isArray(parsed.articles)||!Array.isArray(parsed.models))return null;
  return parsed;
 }catch{return null;}
}
export async function miniApp(request,env,getBalance){
 const path=new URL(request.url).pathname;
 if(request.method==="GET"){
  if(path==="/"||path==="/app")return serve(HTML,"text/html; charset=utf-8");
  if(path==="/app.css")return serve(CSS,"text/css; charset=utf-8");
  if(path==="/app.js")return serve(JS,"application/javascript; charset=utf-8");
 }
 if(!["/api/dashboard","/api/image"].includes(path))return null;
 if(request.method!=="POST")return new Response("Method not allowed",{status:405,headers});
 let data;
 try{if(Number(request.headers.get("Content-Length")||0)>16000)throw Error();const raw=await request.text();if(raw.length>16000)throw Error();data=JSON.parse(raw);}catch{return new Response("Bad request",{status:400,headers});}
 if(!await verifyInitData(data?.initData,env))return new Response("Unauthorized",{status:401,headers});
 const token=env.EDITORIAL_REVIEW_READ_TOKEN;
 if(path==="/api/image"){
  if(!token)return new Response("Not configured",{status:503,headers});
  let image=null;
  try{image=await readPrivateImage(token,data.revision,data.path);}catch{}
  if(!image)return new Response("Image unavailable",{status:404,headers});
  return new Response(image.bytes,{headers:{...headers,"Content-Type":image.type,"Content-Security-Policy":"default-src 'none'; sandbox"}});
 }
 let balance=null,review=readSnapshot(env),sync=token?"fallback":"snapshot";
 const jobs=[(async()=>{try{balance=await getBalance(env);}catch{}})()];
 if(token)jobs.push((async()=>{try{const live=await buildSnapshot(token);if(live){review=live;sync="live";}}catch{}})());
 await Promise.all(jobs);
 return Response.json({balance,checkedAt:new Date().toISOString(),mode:"read_only",review,sync,imagePreview:sync==="live"},{headers});
}
