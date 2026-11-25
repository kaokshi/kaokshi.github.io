// Explicit operator rollback only; no model calls, no forced deployment, no data reset.
import {activeDeployment,rollbackBody} from "./deploy-guard.mjs";
const script="zen-editorial-control";
const account=process.env.CLOUDFLARE_ACCOUNT_ID||"";
const token=process.env.CLOUDFLARE_API_TOKEN||"";
async function main(){
 if(process.env.ZEN_CONTROL_CONFIRM_ROLLBACK!=="true")throw Error("explicit_rollback_confirmation_required");
 if(!/^[a-f0-9]{32}$/i.test(account)||!token)throw Error("cloudflare_config_missing");
 const previous={version:process.env.ZEN_CONTROL_PREVIOUS_VERSION};
 const expected={id:process.env.ZEN_CONTROL_EXPECTED_DEPLOYMENT,version:process.env.ZEN_CONTROL_EXPECTED_VERSION};
 const url="https://api.cloudflare.com/client/v4/accounts/"+account+"/workers/scripts/"+script+"/deployments";
 async function api(method,body){
  const r=await fetch(url,{method,headers:{Authorization:"Bearer "+token,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined,redirect:"error",signal:AbortSignal.timeout(20000)});
  const d=await r.json();if(!r.ok||d.success!==true)throw Error("cloudflare_rollback_request_failed");return d.result;
 }
 const current=activeDeployment(await api("GET"));
 const body=rollbackBody(previous,current,expected);
 await api("POST",body);
 const after=activeDeployment(await api("GET"));
 if(after.version!==previous.version)throw Error("rollback_not_verified");
 console.log("ROLLBACK_VERSION_CONFIRMED="+after.version);
 console.log("VERIFY_TELEGRAM_MANUALLY");
}
try{await main();}catch(e){const v=String(e?.message||"");console.log("ROLLBACK_BLOCKED reason="+(/^[a-z][a-z0-9_]{2,80}$/.test(v)?v:"operation_failed"));process.exitCode=1;}
