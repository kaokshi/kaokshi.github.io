// Cloudflare deployment guard. Never force rollback or touch unrelated deployments.
const uuid=v=>typeof v==="string"&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(v);
export function activeDeployment(result){
 const list=result?.deployments;
 if(!Array.isArray(list)||!list.length)throw Error("deployment_history_missing");
 if(list.some(d=>!uuid(d.id)||!Number.isFinite(Date.parse(d.created_on))))throw Error("deployment_history_invalid");
 const sorted=[...list].sort((a,b)=>Date.parse(b.created_on)-Date.parse(a.created_on));
 if(sorted.length>1&&Date.parse(sorted[0].created_on)===Date.parse(sorted[1].created_on))throw Error("deployment_order_ambiguous");
 const d=sorted[0];
 if(!Array.isArray(d.versions)||d.versions.length!==1||d.versions[0].percentage!==100||!uuid(d.versions[0].version_id))throw Error("single_active_version_required");
 return {id:d.id,version:d.versions[0].version_id};
}
export function rollbackBody(previous,current,expected){
 if(!uuid(previous?.version)||!uuid(expected?.version)||!uuid(expected?.id)||!uuid(current?.id)||!uuid(current?.version)||current.id!==expected.id||current.version!==expected.version||previous.version===current.version)throw Error("rollback_target_not_verified");
 return {strategy:"percentage",versions:[{version_id:previous.version,percentage:100}]};
}

export function latestVersion(result){
 const rows=result?.items;
 if(!Array.isArray(rows)||!rows.length||rows.some(v=>!uuid(v.id)||!Number.isSafeInteger(v.number)||v.number<1))throw Error("version_history_unverified");
 const sorted=[...rows].sort((a,b)=>b.number-a.number);
 if(sorted.length>1&&sorted[0].number===sorted[1].number)throw Error("version_order_ambiguous");
 return sorted[0].id;
}


// Retry only explicitly read-only bridge probes after a transient response.
// Authentication/validation errors and malformed successful payloads are never retried.
export async function readBridgeProbe(fetchProbe,validate,{attempts=4,pause=()=>new Promise(r=>setTimeout(r,1500))}={}){
 if(!Number.isInteger(attempts)||attempts<1||attempts>4)throw Error("invalid_probe_attempts");
 for(let i=0;i<attempts;i++){
  let response;
  try{response=await fetchProbe();}catch{
   if(i+1===attempts)throw Error("bridge_transport_unavailable");
   await pause();continue;
  }
  if(response.ok){
   const body=await response.json();
   if(!validate(body))throw Error("bridge_invalid_response");
   return body;
  }
  if(![429,502,503,504].includes(response.status)||i+1===attempts)throw Error("bridge_http_"+response.status);
  await pause();
 }
}
