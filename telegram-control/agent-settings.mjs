// Owner-controlled settings for the managing agent: provider, model, thinking level and limits.
import {claudyModel} from "./claudy-adapter.mjs";
// The agent budget is separate from the article production budget and from development spend.
export const AGENT_KEY="agent:settings",AGENT_HISTORY_PREFIX="agent:settings-history:";
export const DEFAULT_MODEL="anthropic/claude-sonnet-5.5";
export const OPUS_MODEL="anthropic/claude-opus-5.5";
export const DEFAULT_DAILY_RUB=50,DEFAULT_PER_CALL_RUB=5;
// The owner may pick any verified text model for the managing agent.
export const AGENT_MODELS=Object.freeze([
 {id:"anthropic/claude-sonnet-5.5",label:"Claude Sonnet 5.5"},
 {id:"anthropic/claude-opus-5.5",label:"Claude Opus 5.5"},
 {id:"anthropic/claude-haiku-5.5",label:"Claude Haiku 5.5"},
 {id:"qwen/qwen3.6-plus",label:"Qwen 3.6 Plus"},
 {id:"deepseek/deepseek-v4-pro-0813",label:"DeepSeek V4 Pro"},
]);
// RouterAI stays the default. Claudy (native Messages API) is an owner-selectable
// second provider for the owner chat only; it serves the Claude models it maps.
export const AGENT_PROVIDERS=Object.freeze([
 {id:"routerai",label:"RouterAI",models:AGENT_MODELS.map(m=>m.id)},
 {id:"claudy",label:"Claudy",models:AGENT_MODELS.map(m=>m.id).filter(id=>claudyModel(id))},
]);
export const DEFAULT_PROVIDER="routerai";
export const providerServes=(provider,model)=>AGENT_PROVIDERS.some(p=>p.id===provider&&p.models.includes(model));
export const AGENT_REASONING=Object.freeze(["off","low","medium","high"]);
export const MAX_OUTPUT=700;
export const MAX_DAILY_RUB=1000,MAX_PER_CALL_RUB=100;
// A chat call may never exceed this share of the daily agent budget.
export const MAX_CALLS_PER_DAY=20;

export const initialAgentSettings=()=>({
 version:0,provider:DEFAULT_PROVIDER,model:DEFAULT_MODEL,reasoning:"medium",dailyRub:DEFAULT_DAILY_RUB,perCallRub:DEFAULT_PER_CALL_RUB,opusDailyRub:0,
 updatedAt:null,actor:"owner"});

const money=v=>typeof v==="number"&&Number.isFinite(v)&&v>=0;
export function validateAgentSettings(data){
 if(!data||typeof data!=="object")return {ok:false,error:"invalid_agent_settings"};
 const model=data.model;
 if(typeof model!=="string"||!AGENT_MODELS.some(m=>m.id===model))return {ok:false,error:"unknown_agent_model"};
 if(data.provider!==undefined&&!AGENT_PROVIDERS.some(p=>p.id===data.provider))return {ok:false,error:"unknown_agent_provider"};
 if(data.provider!==undefined&&!providerServes(data.provider,model))return {ok:false,error:"model_not_served_by_provider"};
 if(!AGENT_REASONING.includes(data.reasoning))return {ok:false,error:"invalid_agent_reasoning"};
 if(!money(data.dailyRub)||data.dailyRub<=0||data.dailyRub>MAX_DAILY_RUB)return {ok:false,error:"invalid_agent_daily_limit"};
 if(!money(data.perCallRub)||data.perCallRub<=0||data.perCallRub>MAX_PER_CALL_RUB)return {ok:false,error:"invalid_agent_call_limit"};
 if(data.opusDailyRub!==undefined&&(!money(data.opusDailyRub)||data.opusDailyRub>data.dailyRub))return {ok:false,error:"invalid_opus_daily_limit"};
 if(data.perCallRub>data.dailyRub)return {ok:false,error:"invalid_agent_limits"};
 return {ok:true,settings:{provider:data.provider,model,reasoning:data.reasoning,dailyRub:data.dailyRub,perCallRub:data.perCallRub,opusDailyRub:data.opusDailyRub}};
}
export async function readAgentSettings(storage){
 const saved=await storage.get(AGENT_KEY);
 const base=initialAgentSettings();
 if(!saved||typeof saved!=="object")return base;
 const merged={...base,...saved,version:Number.isSafeInteger(saved.version)?saved.version:0};
 // Older saved settings have no provider. Anything unknown falls back to RouterAI.
 if(!AGENT_PROVIDERS.some(p=>p.id===merged.provider))merged.provider=DEFAULT_PROVIDER;
 return merged;
}
export async function writeAgentSettings(storage,data,now=Date.now()){
 const v=validateAgentSettings(data);
 if(data?.confirm!==true||!v.ok)return {status:400,body:{error:v.ok?"confirmation_required":v.error}};
 const result=await storage.transaction(async tx=>{
  const current=await readAgentSettings(tx);
  if(!Number.isSafeInteger(data.version)||data.version!==current.version)return null;
  // A production hold blocks spend on the shared account but does not change
  // the chat ledger or prevent saving settings for future chat requests.
  if(await tx.get("agent:charge-hold"))return {status:423,body:{error:"charge_reconciliation_required"}};
  const opusDailyRub=v.settings.opusDailyRub??current.opusDailyRub;
  if(opusDailyRub>v.settings.dailyRub)return {status:400,body:{error:"invalid_opus_daily_limit"}};
  // An older client that sends no provider keeps the current one.
  const provider=v.settings.provider??current.provider;
  if(!providerServes(provider,v.settings.model))return {status:400,body:{error:"model_not_served_by_provider"}};
  const next={...v.settings,provider,opusDailyRub,version:current.version+1,updatedAt:new Date(now).toISOString(),actor:"owner"};
  await tx.put(AGENT_KEY,next);
  await tx.put(AGENT_HISTORY_PREFIX+String(next.version).padStart(12,"0"),{version:next.version,at:next.updatedAt,provider,model:next.model,reasoning:next.reasoning,dailyRub:next.dailyRub,perCallRub:next.perCallRub,opusDailyRub});
  return {status:200,body:null};
 });
 if(result?.body)return result;
 if(!result)return {status:409,body:{error:"stale_agent_settings"}};
 return {status:200,body:{settings:await readAgentSettings(storage)}};
}
export async function agentSettingsHistory(storage){
 const current=await readAgentSettings(storage);
 const rows=[];
 for(let v=current.version;v>=1&&rows.length<10;v--){
  const entry=await storage.get(AGENT_HISTORY_PREFIX+String(v).padStart(12,"0"));
  if(entry)rows.push(entry);
 }
 return rows;
}
