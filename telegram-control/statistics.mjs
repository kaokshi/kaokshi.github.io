// Private, owner-confirmed aggregate Dzen Studio imports. No scraping or paid calls.
const channels=["cinema","legends","science"],DAY=86400000;
const fields=["impressions","feed_opens","opens","reads","subscribers_delta","reading_seconds","revenue_kopecks"];
const key="stats:dzen:v1",metaKey="stats:dzen:v2:meta";
const rowKey=(channel,month)=>"stats:dzen:v2:"+channel+":"+month;
async function readState(tx){
 const meta=await tx.get(metaKey);
 if(!meta)return await tx.get(key)||empty();
 const rows=[];for(const k of meta.keys){const part=await tx.get(k);if(!Array.isArray(part))throw Error("stats_partition_missing");rows.push(...part);}
 return {...meta,rows};
}
const dayAt=now=>new Date(now+10800000).toISOString().slice(0,10);
const dateOK=s=>typeof s==="string"&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&Number.isFinite(Date.parse(s))&&new Date(s).toISOString().slice(0,10)===s;
const empty=()=>({version:0,updatedAt:null,rows:[]});
function clean(rows,now){
 if(!Array.isArray(rows)||!rows.length||rows.length>90)return null;
 const seen=new Set(),out=[];
 for(const r of rows){
  if(!r||Object.keys(r).some(k=>!["date","channel",...fields].includes(k))||!dateOK(r.date)||r.date>dayAt(now)||r.date<dayAt(now-365*DAY)||!channels.includes(r.channel))return null;
  const id=r.date+":"+r.channel;if(seen.has(id))return null;seen.add(id);
  const item={date:r.date,channel:r.channel};let provided=false;
  for(const f of fields){const v=r[f]??null;if(v!==null&&(!Number.isSafeInteger(v)||Math.abs(v)>1e12||(f!=="subscribers_delta"&&v<0)))return null;item[f]=v;provided ||= v!==null;}
  if(item.feed_opens!==null&&(item.impressions!==null&&item.feed_opens>item.impressions||item.opens!==null&&item.feed_opens>item.opens))return null;
  if(!provided)return null;out.push(item);
 }return out;
}
export async function importStats(storage,d,now=Date.now()){
 const rows=clean(d?.rows,now);
 if(d?.confirm!==true||!Number.isSafeInteger(d.version)||!rows)return {status:400,body:{error:"invalid_stats_import"}};
 return storage.transaction(async tx=>{
  const old=await readState(tx);
  if(old.version!==d.version)return {status:409,body:{error:"stale_stats"}};
  const map=new Map(old.rows.filter(r=>r.date>=dayAt(now-365*DAY)).map(r=>[r.date+":"+r.channel,r]));
  for(const r of rows)map.set(r.date+":"+r.channel,r);
  const next={version:old.version+1,updatedAt:new Date(now).toISOString(),rows:[...map.values()].sort((a,b)=>a.date.localeCompare(b.date)||a.channel.localeCompare(b.channel))};
  const partitions=new Map();for(const row of next.rows){const k=rowKey(row.channel,row.date.slice(0,7));if(!partitions.has(k))partitions.set(k,[]);partitions.get(k).push(row);}
  for(const [k,part] of partitions){if(new TextEncoder().encode(JSON.stringify(part)).length>100000)throw Error("stats_partition_too_large");await tx.put(k,part);}
  await tx.put(metaKey,{version:next.version,updatedAt:next.updatedAt,keys:[...partitions.keys()]});
  if(typeof tx.delete==="function"){for(const k of old.keys||[])if(!partitions.has(k))await tx.delete(k);await tx.delete(key);}
  return {status:200,body:{version:next.version,imported:rows.length}};
 });
}
function aggregate(rows,days){
 // Missing dates or fields never silently become zero; coverage accompanies every sum.
 const metrics={},coverage={};
 for(const f of fields){const vals=rows.map(r=>r[f]).filter(v=>v!==null&&v!==undefined);coverage[f]=vals.length;metrics[f]=vals.length===days?vals.reduce((a,b)=>a+b,0):null;}
 const ratio=(a,b)=>a!==null&&b!==null&&b>0?a/b:null;
 return {metrics,coverage,days,ctr:ratio(metrics.feed_opens,metrics.impressions),completion:ratio(metrics.reads,metrics.opens),avgReadingSeconds:ratio(metrics.reading_seconds,metrics.opens)};
}
function advice(current,previous){
 const out=[];
 // Only compare complete matched periods with at least 100 opens; no causal claims.
 if(current.metrics.opens>=100&&previous.metrics.opens>=100&&current.completion!==null&&previous.completion!==null&&current.completion<previous.completion-.05)out.push("Доля дочитываний снизилась более чем на 5 п.п. Проверьте вступления, структуру и соответствие заголовков содержанию.");
 if(current.metrics.feed_opens!==null&&previous.metrics.feed_opens!==null&&current.metrics.impressions>=1000&&previous.metrics.impressions>=1000&&current.ctr!==null&&previous.ctr!==null&&current.ctr<previous.ctr-.02)out.push("CTR снизился более чем на 2 п.п. Сравните обложки и заголовки с содержанием материалов.");
 return out;
}
export async function statsView(storage,period=7,now=Date.now()){
 if(![7,30,90].includes(period))period=7;
 const s=await storage.transaction(tx=>readState(tx)),end=dayAt(now-DAY),start=dayAt(now-period*DAY),prevStart=dayAt(now-period*2*DAY),prevEnd=dayAt(now-(period+1)*DAY);
 const reports=channels.map(channel=>{
  const rows=s.rows.filter(r=>r.channel===channel);
  const currentRows=rows.filter(r=>r.date>=start&&r.date<=end),previousRows=rows.filter(r=>r.date>=prevStart&&r.date<=prevEnd);
  const current=aggregate(currentRows,period),previous=aggregate(previousRows,period);
  return {channel,current,previous,series:Array.from({length:period},(_,i)=>{const date=dayAt(now-(period-i)*DAY),r=currentRows.find(x=>x.date===date);return {date,opens:r?.opens??null};}),recommendations:advice(current,previous)};
 });
 return {schema:"zen-stats/v1",source:s.updatedAt?"owner_confirmed_dzen_export":null,automaticSync:false,version:s.version,updatedAt:s.updatedAt,period,start,end,prevStart,prevEnd,reports};
}
