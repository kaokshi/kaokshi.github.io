// Versioned editorial executor synchronization. Opus limits are separate.
import {POLICY_KEY,ACK_KEY,HISTORY_PREFIX,ROLES,policyHash,initialPolicy} from "./model-settings.mjs";
export const SETTINGS_SCHEMA="zen-core/settings-v1";
export const SYNC_KINDS=Object.freeze(["models"]);
export const isHash=v=>typeof v==="string"&&/^[0-9a-f]{64}$/.test(v);
const valid=s=>Number.isSafeInteger(s?.revision)&&s.revision>=0&&isHash(s.hash);
export function validateSettingsReport(report){
 if(!report||typeof report!=="object"||Array.isArray(report)||Object.keys(report).length!==1||!valid(report.models))return {ok:false,error:"invalid_settings_report"};
 return {ok:true,report:{models:{revision:report.models.revision,hash:report.models.hash}}};
}
async function resolveIn(tx,spec){
 if(!valid(spec))return null;
 const entry=spec.revision===0?await initialPolicy():await tx.get(HISTORY_PREFIX+String(spec.revision).padStart(12,"0"));
 if(!entry||entry.revision!==spec.revision||entry.hash!==spec.hash||!entry.models||Object.keys(entry.models).length!==ROLES.length||ROLES.some(r=>typeof entry.models[r.id]!=="string")||await policyHash(entry.models)!==spec.hash)return null;
 return {revision:spec.revision,hash:spec.hash,value:Object.fromEntries(ROLES.map(r=>[r.id,entry.models[r.id]]))};
}
export async function effectiveSettingsIn(tx){
 const ack=await tx.get(ACK_KEY),resolved=await resolveIn(tx,ack||await initialPolicy());
 if(!resolved)throw Error("executor_settings_history_mismatch");
 return {models:{...resolved,source:ack?"executor_ack":"defaults_executor_not_connected",acknowledgedAt:ack?.acknowledgedAt??null}};
}
export async function requestedSettingsIn(tx){
 const resolved=await resolveIn(tx,await tx.get(POLICY_KEY)||await initialPolicy());
 if(!resolved)throw Error("requested_settings_history_mismatch");
 return {models:resolved};
}
export async function financialHoldIn(tx){
 if(await tx.get("agent:charge-hold")||await tx.get("account:production-hold"))return "financial_hold";
 if((await tx.get("budget:executor-report"))?.budget?.unsettledRequest)return "in_flight_paid_request";
 return null;
}
export async function acknowledgeSettingsIn(tx,report,now,{jobId=null,pin=null}={}){
 const v=validateSettingsReport(report);if(!v.ok)return v;
 const spec=v.report.models;
 if(pin){
  if(!valid(pin.models)||pin.models.revision!==spec.revision||pin.models.hash!==spec.hash)return {ok:false,error:"order_settings_mismatch"};
 }else{
  const hold=await financialHoldIn(tx);if(hold)return {ok:false,error:hold};
  const prior=await tx.get(ACK_KEY);
  if(prior&&(!valid(prior)||spec.revision<prior.revision||spec.revision===prior.revision&&spec.hash!==prior.hash))return {ok:false,error:"stale_settings_ack"};
  const wanted=(await requestedSettingsIn(tx)).models;
  if(wanted.revision!==spec.revision||wanted.hash!==spec.hash)return {ok:false,error:"settings_not_requested"};
 }
 const resolved=await resolveIn(tx,spec);if(!resolved)return {ok:false,error:"executor_settings_mismatch"};
 const ack={revision:spec.revision,hash:spec.hash,acknowledgedAt:new Date(now).toISOString(),jobId};
 if(!pin)await tx.put(ACK_KEY,ack);
 return {ok:true,acks:{models:ack}};
}
export async function pinFor(tx){
 const {models}=await effectiveSettingsIn(tx);return {models:{revision:models.revision,hash:models.hash}};
}
export async function settingsForPinIn(tx,pin){
 const resolved=await resolveIn(tx,pin?.models);if(!resolved)throw Error("order_settings_history_mismatch");return {models:resolved};
}
export async function checkReportAgainstPin(tx,report,pin,now){
 if(!pin)return {ok:false,error:"order_settings_pin_missing"};
 return acknowledgeSettingsIn(tx,report,now,{pin});
}
export async function settingsSyncView(storage){
 const wanted=(await requestedSettingsIn(storage)).models,ran=(await effectiveSettingsIn(storage)).models;
 const pending=wanted.revision!==ran.revision||wanted.hash!==ran.hash;
 return {schema:SETTINGS_SCHEMA,synchronized:{models:{requested:wanted.revision,requestedHash:wanted.hash,effective:ran.revision,effectiveHash:ran.hash,pending,source:ran.source,acknowledgedAt:ran.acknowledgedAt}},pending,executorTransport:"not_connected",executionEnabled:false,publicationAuthorized:false,rssEnabled:false};
}
