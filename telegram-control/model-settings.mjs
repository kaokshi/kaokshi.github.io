// Owner model policy; no inference or publication.
import {STAGES} from "./production.mjs";
import {verifiedCatalogue} from "./model-prices.mjs";
export const POLICY_KEY="models:policy",ACK_KEY="models:executor-ack",HISTORY_PREFIX="models:history:";
export const ROLES=STAGES.filter(s=>s.model).map(s=>({id:s.id,role:s.role,capability:s.id==="images"?"image":"text",defaultModel:s.model}));
export const DEFAULT_MODELS=Object.freeze(Object.fromEntries(ROLES.map(r=>[r.id,r.defaultModel])));
export function defaultCatalogue(){
 const seen=new Map();
 for(const r of ROLES)if(!seen.has(r.defaultModel))seen.set(r.defaultModel,{id:r.defaultModel,capability:r.capability,available:true,priceVerified:false,priceSource:null,source:"telegram-control/production.mjs#STAGES"});
 return [...seen.values()];
}
export async function readCatalogue(storage,now=Date.now()){
 return verifiedCatalogue(defaultCatalogue(),await storage.get("models:verified-catalogue"),now);
}
const canonical=models=>JSON.stringify(ROLES.map(r=>[r.id,models[r.id]]));
export async function policyHash(models){
 const d=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(canonical(models)));
 return [...new Uint8Array(d)].map(b=>b.toString(16).padStart(2,"0")).join("");
}
export async function initialPolicy(){return {revision:0,models:{...DEFAULT_MODELS},hash:await policyHash(DEFAULT_MODELS),createdAt:null,action:"default",previousRevision:null,rollbackOf:null};}
const key=r=>HISTORY_PREFIX+String(r).padStart(12,"0");
const revisionOK=r=>Number.isSafeInteger(r)&&r>=0;
export function validateModels(models,catalogue=defaultCatalogue()){
 if(!models||typeof models!=="object"||Array.isArray(models))return {ok:false,error:"invalid_models"};
 if(Object.keys(models).length!==ROLES.length||!ROLES.every(r=>Object.hasOwn(models,r.id)))return {ok:false,error:"invalid_roles"};
 if(models.edit===models.draft)return {ok:false,error:"editor_not_independent",role:"edit"};
 for(const r of ROLES){
  const id=models[r.id],m=catalogue.find(x=>x.id===id);
  if(typeof id!=="string")return {ok:false,error:"invalid_models",role:r.id};
  if(!m)return {ok:false,error:"unknown_model",role:r.id};
  if(m.capability!==r.capability)return {ok:false,error:"wrong_capability",role:r.id};
  if(m.available!==true)return {ok:false,error:"model_unavailable",role:r.id};
  if(id!==r.defaultModel&&m.priceVerified!==true)return {ok:false,error:"unknown_price",role:r.id};
 }
 return {ok:true,models:Object.fromEntries(ROLES.map(r=>[r.id,models[r.id]]))};
}
async function resolve(storage,spec){
 if(!revisionOK(spec?.revision)||typeof spec.hash!=="string")throw Error("executor_settings_history_mismatch");
 const entry=spec.revision===0?await initialPolicy():await storage.get(key(spec.revision));
 if(!entry||entry.revision!==spec.revision||entry.hash!==spec.hash||!entry.models||Object.keys(entry.models).length!==ROLES.length||ROLES.some(r=>typeof entry.models[r.id]!=="string")||await policyHash(entry.models)!==spec.hash)throw Error("executor_settings_history_mismatch");
 return entry;
}
async function guards(storage){
 if(await storage.get("agent:charge-hold")||await storage.get("account:production-hold"))return "financial_hold";
 if((await storage.get("budget:executor-report"))?.budget?.unsettledRequest)return "in_flight_paid_request";
 return null;
}
export async function effectiveModels(storage){
 const ack=await storage.get(ACK_KEY);
 const e=await resolve(storage,ack||await initialPolicy());
 return {models:e.models,revision:e.revision,source:ack?"executor_ack":"defaults_executor_not_connected"};
}
export async function readModels(storage,catalogue){
 catalogue??=await readCatalogue(storage);
 const requested=await resolve(storage,await storage.get(POLICY_KEY)||await initialPolicy());
 const effective=await effectiveModels(storage);
 const history=[];
 for(let r=requested.revision;r>=1&&history.length<10&&r>requested.revision-10;r--){const h=await storage.get(key(r));if(h)history.push(h);}
 return {roles:ROLES,catalogue,requested,effective,pendingSync:requested.revision!==effective.revision||requested.hash!==await policyHash(effective.models),executorSync:effective.source==="executor_ack"?"acknowledged":"not_connected",history,executionEnabled:false,publicationAuthorized:false,rssEnabled:false,note:"Active orders keep their pinned revision."};
}
export async function writeModels(storage,data,now=Date.now(),catalogue){
 if(data?.confirm!==true)return {status:400,body:{error:"confirmation_required"}};
 if(!revisionOK(data.revision))return {status:400,body:{error:"invalid_revision"}};
 const result=await storage.transaction(async tx=>{
  const blocked=await guards(tx);if(blocked)return {status:423,body:{error:blocked}};
  const current=await resolve(tx,await tx.get(POLICY_KEY)||await initialPolicy());
  if(current.revision!==data.revision||current.revision===Number.MAX_SAFE_INTEGER)return {status:409,body:{error:"stale_revision"}};
  let target,rollbackOf=null;
  if(data.action==="set")target=data.models;
  else if(data.action==="rollback"){
   if(!revisionOK(data.targetRevision))return {status:400,body:{error:"invalid_rollback_target"}};
   const h=data.targetRevision===0?await initialPolicy():await tx.get(key(data.targetRevision));
   if(!h)return {status:404,body:{error:"unknown_revision"}};
   target=(await resolve(tx,h)).models;rollbackOf=data.targetRevision;
  }else return {status:400,body:{error:"invalid_action"}};
  const v=validateModels(target,catalogue??await readCatalogue(tx,now));
  if(!v.ok)return {status:400,body:{error:v.error,role:v.role??null}};
  const revision=current.revision+1;if(await tx.get(key(revision)))return {status:409,body:{error:"stale_revision"}};
  const updated={revision,models:v.models,hash:await policyHash(v.models),createdAt:new Date(now).toISOString(),action:data.action,previousRevision:current.revision,previousHash:current.hash,rollbackOf,actor:"owner"};
  await tx.put(key(revision),updated);await tx.put(POLICY_KEY,updated);return null;
 });
 return result??{status:200,body:await readModels(storage,catalogue)};
}
export async function acknowledgeExecutor(storage,ack,now=Date.now()){
 return storage.transaction(async tx=>{
  const blocked=await guards(tx);if(blocked)return {ok:false,error:blocked};
  const current=await resolve(tx,await tx.get(POLICY_KEY)||await initialPolicy());
  if(!revisionOK(ack?.revision)||ack.revision!==current.revision||ack.hash!==current.hash)return {ok:false,error:"executor_mismatch"};
  const prior=await tx.get(ACK_KEY);
  if(prior&&(prior.revision>ack.revision||prior.revision===ack.revision&&prior.hash!==ack.hash))return {ok:false,error:"stale_settings_ack"};
  await tx.put(ACK_KEY,{revision:ack.revision,hash:ack.hash,acknowledgedAt:new Date(now).toISOString()});return {ok:true};
 });
}
