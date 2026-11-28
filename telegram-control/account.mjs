// One durable production charge lane, shared with the existing owner chat hold.
// Call only inside the same Durable Object transaction as the control decision.
const LOCK="account:production-hold";
const valid=d=>typeof d?.operation==="string"&&/^[-a-zA-Z0-9:_/]{1,180}$/.test(d.operation)&&typeof d.id==="string"&&/^[-a-zA-Z0-9:]{1,120}$/.test(d.id)&&d.operation.startsWith(d.id+"/");
const fail=(status,error)=>({status,body:{error}});
export async function claimProduction(tx,d,now){
 if(!valid(d))return fail(400,"invalid_account_operation");
 if(await tx.get("agent:charge-hold")||await tx.get(LOCK))return fail(409,"account_charge_hold");
 if(await tx.get("account:closed:"+d.operation))return fail(409,"account_operation_seen");
 const hold={id:d.id,operation:d.operation,startedAt:now};
 await tx.put(LOCK,hold);
 return {status:200,body:{schema:"zen-core/account-v1",claimed:true}};
}
export async function closeProduction(tx,d,now){
 if(!valid(d)||!["settled","not_sent","bounded_uncertain"].includes(d.outcome))return fail(400,"invalid_account_outcome");
 const key="account:closed:"+d.operation,old=await tx.get(key);
 if(old)return old.outcome===d.outcome?{status:200,body:{closed:true,replay:true}}:fail(409,"account_outcome_conflict");
 const hold=await tx.get(LOCK);
 if(!hold||hold.operation!==d.operation||hold.id!==d.id)return fail(409,"account_hold_mismatch");
 await tx.put(key,{outcome:d.outcome,closedAt:now});
 await tx.delete(LOCK);
 return {status:200,body:{closed:true,replay:false}};
}

export async function accountStatus(tx,now,executionEnabled=false){
 const production=await tx.get(LOCK),chat=await tx.get("agent:charge-hold");
 return {status:200,body:{schema:"zen-core/account-status-v1",observedAt:now,
 production:production?{id:production.id,operation:production.operation,startedAt:production.startedAt}:null,
 chat:chat?{id:chat.id,startedAt:chat.startedAt,status:chat.status}:null,executionEnabled:executionEnabled===true}};
}
