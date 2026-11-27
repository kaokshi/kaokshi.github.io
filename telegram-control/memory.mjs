// Persistent owner-confirmed editorial context; never grants execution authority.
// Keep notes small enough for the RouterAI request cost estimator.
export const emptyMemory=()=>({version:0,notes:[],updatedAt:null});
const kinds=new Set(["style","editorial","illustrations","workflow"]);
export async function readMemory(storage){return await storage.get("agent:memory")||emptyMemory();}
export function cleanNotes(notes){
 if(!Array.isArray(notes)||notes.length>8)return null;
 const result=[];
 for(const n of notes){
  if(!n||!kinds.has(n.kind)||typeof n.text!=="string")return null;
  const text=n.text.trim();
  if(!text||text.length>240||/[\x00-\x1f\x7f]/.test(text)||/(?:api[_ -]?key|bearer\s+|private key|парол[ья]|токен\s*[:=]|sk-[a-z0-9]{12,})/i.test(text))return null;
  if(result.some(x=>x.kind===n.kind&&x.text===text))return null;
  result.push({kind:n.kind,text});
 }
 return result;
}
export async function writeMemory(storage,d,now=Date.now()){
 const notes=cleanNotes(d?.notes);
 if(d?.confirm!==true||!Number.isSafeInteger(d.version)||!notes)return {status:400,body:{error:"invalid_memory"}};
 const result=await storage.transaction(async tx=>{
  const old=await readMemory(tx);if(old.version!==d.version)return null;
  const next={version:old.version+1,notes,updatedAt:new Date(now).toISOString()};
  await tx.put("agent:memory",next);
  // Audit only the count and revision; removed note text is not copied into the journal.
  await tx.put("agent:memory-event:"+String(next.version).padStart(12,"0"),{at:next.updatedAt,version:next.version,count:notes.length});
  return next;
 });
 return result?{status:200,body:{memory:result}}:{status:409,body:{error:"stale_memory"}};
}
