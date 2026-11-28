// Role isolation: never fall back to another role or legacy test key.
const names=Object.freeze({chat:"CLAUDY_SONNET_API_KEY",development:"CLAUDY_OPUS_API_KEY",production:"CLAUDY_EDITORIAL_API_KEY"});
export function claudyRoleKey(env,role){
 if(!Object.hasOwn(names,role))return null;
 const key=env?.[names[role]];
 return typeof key==="string"&&key.trim()===key&&key.length>0?key:null;
}
export function claudyRoleStatus(env){
 const keys=Object.keys(names).map(role=>claudyRoleKey(env,role));
 const configured=keys.every(Boolean);
 return {chat:Boolean(keys[0]),development:Boolean(keys[1]),production:Boolean(keys[2]),distinct:configured&&new Set(keys).size===3,liveEnabled:false};
}
