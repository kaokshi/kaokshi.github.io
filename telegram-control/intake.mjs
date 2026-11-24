// Read-only bridge from confirmed Telegram jobs to the existing Python Core intake.
import {validateJob} from "./production.mjs";
const encoder=new TextEncoder();
async function matches(secret,header){
 if(typeof secret!=="string"||! /^[a-fA-F0-9]{64}$/.test(secret)||typeof header!=="string"||!/^Bearer [a-fA-F0-9]{64}$/.test(header))return false;
 const key=await crypto.subtle.importKey("raw",encoder.encode(secret),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
 const a=new Uint8Array(await crypto.subtle.sign("HMAC",key,encoder.encode(header.slice(7))));
 const b=new Uint8Array(await crypto.subtle.sign("HMAC",key,encoder.encode(secret)));
 let diff=0;for(let i=0;i<a.length;i++)diff|=a[i]^b[i];return diff===0;
}
export function exportIntent(job){
 if(!job?.preview||job.status!=="blocked_prelaunch"||job.executed!==false||job.publicationAuthorized!==false||typeof job.id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(job.id))return null;
 const plan=validateJob(job.plan);
 if(!plan||plan.items.some(x=>x.channel==="cinema"))return null;
 let releaseAt=null;
 if(job.releaseAt!=null){if(!Number.isSafeInteger(job.releaseAt)||!Number.isFinite(new Date(job.releaseAt).getTime()))return null;releaseAt=new Date(job.releaseAt).toISOString();}
 const mode=job.preview.executorMode??"manual";
 if(!["manual","semi_autonomous","autonomous"].includes(mode))return null;
 return {id:job.id,plan,releaseAt,mode};
}
export async function intakeView(storage,id){
 if(typeof id!=="string"||!/^[-a-zA-Z0-9:]{1,120}$/.test(id))return {status:400,body:{error:"invalid_job_id"}};
 return storage.transaction(async tx=>{
  if((await tx.get("ops:state"))?.stopped===true)return {status:409,body:{error:"emergency_paused"}};
  const intent=exportIntent(await tx.get("production:job:"+id));
  return intent?{status:200,body:{schema:"zen-core/intake-v1",mode:"intents_only",intent,executionEnabled:false,publicationAuthorized:false,rssEnabled:false}}:{status:404,body:{error:"intent_not_available"}};
 });
}
export async function bridgeRequest(request,env){
 const path=new URL(request.url).pathname;
 if(!["/internal/zen-core/intake","/internal/zen-core/receipt"].includes(path))return null;
 const receipt=path.endsWith("/receipt");
 const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"};
 if(request.method!=="POST")return Response.json({error:"method_not_allowed"},{status:405,headers});
 if(!await matches(env.ZEN_CORE_INTAKE_SECRET,request.headers.get("Authorization")))return Response.json({error:"unauthorized"},{status:401,headers});
 let d;try{const raw=await request.text();if(raw.length>(receipt?16384:512))throw Error();d=JSON.parse(raw);}catch{return Response.json({error:"invalid_request"},{status:400,headers});}
 const stub=env.REVIEW_JOURNAL?.get(env.REVIEW_JOURNAL.idFromName("zen-core-scheduler-v1"));
 if(!stub)return Response.json({error:"core_unavailable"},{status:503,headers});
 try{const r=await stub.fetch("https://core.internal/core/"+(receipt?"receipt":"intake"),{method:"POST",body:JSON.stringify(receipt?d:{id:d?.id})});return Response.json(await r.json(),{status:r.status,headers});}catch{return Response.json({error:"core_unavailable"},{status:503,headers});}
}
