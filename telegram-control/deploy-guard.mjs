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
