// Private append-only editorial review log; never drives paid generation or publication.
const CHANNELS=new Set(["legends","science","cinema"]);
const ACTIONS=new Set(["approve","revise"]);
export function validateDecision(data,review){
 if(!data||!ACTIONS.has(data.action)||!CHANNELS.has(data.channel)||!/^[a-f0-9]{40}$/.test(data.revision||"")||!/^[0-9]{8,20}$/.test(data.run||"")||!/^[a-f0-9-]{36}$/.test(data.id||""))return {error:"invalid",status:400};
 if(!review||review.revision!==data.revision||review.run!==data.run)return {error:"stale_revision",status:409};
 const article=review.articles.find(a=>a.channel===data.channel);
 if(!article?.text)return {error:"article_missing",status:404};
 if(typeof data.note!=="string"||data.note.length>2000)return {error:"invalid_note",status:400};
 const note=data.note.trim();
 const target=data.target||"article";
 if(target!=="article"&&!article.images?.some(im=>im.path===target))return {error:"invalid_target",status:400};
 if(data.action==="revise"&&!note)return {error:"note_required",status:400};
 if(data.action==="approve"&&(target!=="article"||!article.assembled||article.textQA!=="passed"||article.imageQA!=="passed"||!article.assembledVerified))return {error:"not_ready",status:409};
 return {entry:{id:data.id,action:data.action,channel:data.channel,revision:data.revision,run:data.run,target,note,at:new Date().toISOString()}};
}
export class ReviewJournal {
 constructor(ctx){this.storage=ctx.storage;}
 async fetch(request){
  if(request.method==="GET"){const rows=await this.storage.list({prefix:"event:",reverse:true,limit:100});return Response.json({entries:[...rows.values()]});}
  if(request.method!=="POST")return new Response("Method not allowed",{status:405});
  const entry=await request.json();
  const result=await this.storage.transaction(async tx=>{
   const key="id:"+entry.id;
   const existing=await tx.get(key);
   if(existing){
    const fields=["action","channel","revision","run","target","note"];
    return fields.every(k=>existing[k]===entry[k])?{entry:existing}:{error:"id_conflict"};
   }
   await tx.put(key,entry);await tx.put("event:"+entry.at+":"+entry.id,entry);return {entry};
  });
  return Response.json(result,{status:result.error?409:200});
 }
}
export function journalStub(env){
 return env.REVIEW_JOURNAL?.get(env.REVIEW_JOURNAL.idFromName("owner-review-v1"));
}
