// Normalize a trusted RouterAI /models response. Never infer prices or capabilities.
// A catalogue snapshot is valid only for the specified observation window.
const MAX_AGE_MS=300000;
const decimal=v=>{
 if(typeof v!=="string"&&typeof v!=="number")return null;
 if(typeof v==="number"&&(!Number.isFinite(v)||v<0))return null;
 const s=String(v);
 return /^(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(s)&&Number.isFinite(Number(s))&&Number(s)>=0?s:null;
};
export function verifiedCatalogue(defaults,snapshot,now=Date.now()){
 const rows=snapshot?.data;
 const at=snapshot?.observedAt;
 const fresh=snapshot?.source==="routerai:/models"&&Number.isSafeInteger(at)&&Number.isSafeInteger(now)&&now>=at&&now-at<=MAX_AGE_MS&&Array.isArray(rows);
 const counts=new Map();
 if(fresh)for(const row of rows)if(typeof row?.id==="string")counts.set(row.id,(counts.get(row.id)||0)+1);
 return defaults.map(base=>{
  const unverified={...base,priceVerified:false,priceSource:null,pricing:null,observedAt:null};
  if(!fresh)return unverified;
  if(counts.get(base.id)!==1)return {...unverified,available:false};
  const row=rows.find(x=>x?.id===base.id);
  const fields=base.capability==="image"?{image_output:"image"}:base.capability==="text"?{prompt:"token",completion:"token"}:null;
  if(!fields)return {...unverified,available:false};
  const pricing={};
  for(const [key,unit] of Object.entries(fields)){
   const value=decimal(row?.pricing?.[key]);
   if(value===null||row?.pricing_units?.[key]!==unit)return unverified;
   if(base.capability==="image"&&Number(value)<=0)return unverified;
   pricing[key]={value,unit};
  }
  if(base.capability==="text")for(const key of ["input_cache_write","input_cache_write_1h"]){
   if(!Object.hasOwn(row.pricing,key))continue;
   const value=decimal(row.pricing[key]);
   if(value===null||row?.pricing_units?.[key]!=="token")return unverified;
   pricing[key]={value,unit:"token"};
  }
  return {...base,priceVerified:true,priceSource:"routerai:/models",pricing,observedAt:at};
 });
}
