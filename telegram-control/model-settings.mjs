// Owner model policy; no inference or publication.
// The catalogue lists candidate models. A model becomes selectable only after the
// live RouterAI snapshot verifies its price; nothing is assumed about a price.
import {STAGES} from "./production.mjs";
import {verifiedCatalogue} from "./model-prices.mjs";
export const POLICY_KEY="models:policy",ACK_KEY="models:executor-ack",HISTORY_PREFIX="models:history:";
export const REASONING_LEVELS=Object.freeze(["off","low","medium","high"]);
// Candidate models the owner may choose between. Availability and price come from the snapshot.
export const CANDIDATE_MODELS=Object.freeze([
 {id:"qwen/qwen3.6-plus",capability:"text",label:"Qwen 3.6 Plus"},
 {id:"anthropic/claude-haiku-5.5",capability:"text",label:"Claude Haiku 5.5"},
 {id:"anthropic/claude-sonnet-5.5",capability:"text",label:"Claude Sonnet 5.5"},
 {id:"anthropic/claude-opus-5.5",capability:"text",label:"Claude Opus 5.5"},
 {id:"deepseek/deepseek-v4-pro-0813",capability:"text",label:"DeepSeek V4 Pro"},
 {id:"bytedance-seed/seedream-5-0-flash",capability:"image",label:"Seedream 5.0 Flash"},
]);
const DEFAULT_REASONING=Object.freeze({plan:"off",draft:"low",edit:"low",visual_plan:"low",images:"off"});
export const ROLES=STAGES.filter(s=>s.model).map(s=>({id:s.id,role:s.role,capability:s.id==="images"?"image":"text",defaultModel:s.model,defaultReasoning:DEFAULT_REASONING[s.id]??"off",candidates:CANDIDATE_MODELS.filter(m=>m.capability===(s.id==="images"?"image":"text")).map(m=>m.id)}));
export const DEFAULT_MODELS=Object.freeze(Object.fromEntries(ROLES.map(r=>[r.id,r.defaultModel])));
export const DEFAULT_REASONING_POLICY=Object.freeze(Object.fromEntries(ROLES.map(r=>[r.id,r.defaultReasoning])));
export function defaultCatalogue(){
 const seen=new Map();
 for(const r of ROLES)for(const id of [r.defaultModel,...r.candidates]){
  const base=CANDIDATE_MODELS.find(m=>m.id===id);
  if(base&&!seen.has(id))seen.set(id,{id,capability:base.capability,label:base.label,available:true,priceVerified:false,priceSource:null,source:"telegram-control/model-settings.mjs#CANDIDATE_MODELS"});
 }
 return [...seen.values()];
}
export async function readCatalogue(storage,now=Date.now()){
 return verifiedCatalogue(defaultCatalogue(),await storage.get("models:verified-catalogue"),now);
}
const canonical=(models,reasoning)=>JSON.stringify(ROLES.map(r=>[r.id,models[r.id],reasoning?.[r.id]??"off"]));
export async function policyHash(models,reasoning){
 const d=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(canonical(models,reasoning)));
 return [...new Uint8Array(d)].map(b=>b.toString(16).padStart(2,"0")).join("");
}
export async function initialPolicy(){return {revision:0,models:{...DEFAULT_MODELS},reasoning:{...DEFAULT_REASONING_POLICY},hash:await policyHash(DEFAULT_MODELS,DEFAULT_REASONING_POLICY),createdAt:null,action:"default",previousRevision:null,rollbackOf:null};}
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
  if(!r.candidates.includes(id))return {ok:false,error:"model_not_allowed_for_role",role:r.id};
 }
 return {ok:true,models:Object.fromEntries(ROLES.map(r=>[r.id,models[r.id]]))};
}
export function validateReasoning(reasoning){
 if(!reasoning||typeof reasoning!=="object"||Array.isArray(reasoning))return {ok:false,error:"invalid_reasoning"};
 if(Object.keys(reasoning).length!==ROLES.length||!ROLES.every(r=>Object.hasOwn(reasoning,r.id)))return {ok:false,error:"invalid_reasoning"};
 for(const r of ROLES){
  const level=reasoning[r.id];
  if(!REASONING_LEVELS.includes(level))return {ok:false,error:"invalid_reasoning",role:r.id};
  if(r.capability==="image"&&level!=="off")return {ok:false,error:"reasoning_not_supported",role:r.id};
 }
 return {ok:true,reasoning:Object.fromEntries(ROLES.map(r=>[r.id,reasoning[r.id]]))};
}
async function resolve(storage,spec){
 if(!revisionOK(spec?.revision)||typeof spec.hash!=="string")throw Error("executor_settings_history_mismatch");
 const entry=spec.revision===0?await initialPolicy():await storage.get(key(spec.revision));
 if(!entry||entry.revision!==spec.revision||entry.hash!==spec.hash||!entry.models||Object.keys(entry.models).length!==ROLES.length||ROLES.some(r=>typeof entry.models[r.id]!=="string")||!entry.reasoning||await policyHash(entry.models,entry.reasoning)!==spec.hash)throw Error("executor_settings_history_mismatch");
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
 return {models:e.models,reasoning:e.reasoning,hash:e.hash,revision:e.revision,source:ack?"executor_ack":"defaults_executor_not_connected"};
}
export async function readModels(storage,catalogue){
 catalogue??=await readCatalogue(storage);
 const requested=await resolve(storage,await storage.get(POLICY_KEY)||await initialPolicy());
 const effective=await effectiveModels(storage);
 const history=[];
 for(let r=requested.revision;r>=1&&history.length<10&&r>requested.revision-10;r--){const h=await storage.get(key(r));if(h)history.push(h);}
 return {roles:ROLES,catalogue,reasoningLevels:REASONING_LEVELS,requested,effective,pendingSync:requested.revision!==effective.revision||requested.hash!==effective.hash,executorSync:effective.source==="executor_ack"?"acknowledged":"not_connected",history,executionEnabled:false,publicationAuthorized:false,rssEnabled:false,note:"Active orders keep their pinned revision."};
}
export async function writeModels(storage,data,now=Date.now(),catalogue){
 if(data?.confirm!==true)return {status:400,body:{error:"confirmation_required"}};
 if(!revisionOK(data.revision))return {status:400,body:{error:"invalid_revision"}};
 const result=await storage.transaction(async tx=>{
  const blocked=await guards(tx);if(blocked)return {status:423,body:{error:blocked}};
  const current=await resolve(tx,await tx.get(POLICY_KEY)||await initialPolicy());
  if(current.revision!==data.revision||current.revision===Number.MAX_SAFE_INTEGER)return {status:409,body:{error:"stale_revision"}};
  let target,reasoning=data.reasoning,rollbackOf=null;
  if(data.action==="set")target=data.models;
  else if(data.action==="rollback"){
   if(!revisionOK(data.targetRevision))return {status:400,body:{error:"invalid_rollback_target"}};
   const h=data.targetRevision===0?await initialPolicy():await tx.get(key(data.targetRevision));
   if(!h)return {status:404,body:{error:"unknown_revision"}};
   const prior=await resolve(tx,h);
   target=prior.models;reasoning=prior.reasoning;rollbackOf=data.targetRevision;
  }else return {status:400,body:{error:"invalid_action"}};
  const v=validateModels(target,catalogue??await readCatalogue(tx,now));
  if(!v.ok)return {status:400,body:{error:v.error,role:v.role??null}};
  const rv=validateReasoning(reasoning??current.reasoning);
  if(!rv.ok)return {status:400,body:{error:rv.error,role:rv.role??null}};
  const revision=current.revision+1;if(await tx.get(key(revision)))return {status:409,body:{error:"stale_revision"}};
  const updated={revision,models:v.models,reasoning:rv.reasoning,hash:await policyHash(v.models,rv.reasoning),createdAt:new Date(now).toISOString(),action:data.action,previousRevision:current.revision,previousHash:current.hash,rollbackOf,actor:"owner"};
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
