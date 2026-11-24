// Owner-only view of a fixed private aggregate report. No write credential in Worker.
const BASE="https://api.github.com/repos/kaokshi/zen-editorial-review";
const PATH="zen-analytics/collector-validation.json";
const IDS={cinema:114654979,science:114655465,legends:114655507};
const statuses=["binding_unverified","mixed_channel_traffic","unavailable"];
const metricNames=["ym:s:visits","ym:s:users","ym:s:pageviews","ym:s:bounceRate","ym:s:pageDepth","ym:s:avgVisitDurationSeconds"];
const validDate=s=>typeof s==="string"&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&Number.isFinite(Date.parse(s))&&new Date(s).toISOString().slice(0,10)===s;
const nums=(a,n)=>Array.isArray(a)&&a.length===n&&a.every(x=>typeof x==="number"&&Number.isFinite(x)&&x>=0&&x<=1e12);
const counts=(a,n)=>nums(a,n)&&a.every(Number.isSafeInteger);
export function sanitizeMetrika(raw,now=Date.now()){
 if(raw?.schema!=="zen-metrika/v1"||typeof raw.collected_at!=="string"||!Number.isFinite(Date.parse(raw.collected_at))||Date.parse(raw.collected_at)>now+60000)return null;
 const reports=[];
 for(const [channel,id] of Object.entries(IDS)){
  const r=raw.channels?.[channel];
  if(r?.counter_id!==id||r.channel!==channel||r.source!=="yandex_metrika"||!statuses.includes(r.status))return null;
  const periods={};
  for(const n of [7,30]){
   const p=r.periods?.[String(n)],a=p?.totals?.totals;
   if(!validDate(p?.start)||!validDate(p?.end)||Date.parse(p.end)-Date.parse(p.start)!==(n-1)*86400000||JSON.stringify(p.totals?.metrics)!==JSON.stringify(metricNames)||!nums(a,6)||!counts(a.slice(0,3),3)||a[3]>100)continue;
   const daily=p.daily?.rows;
   if(!Array.isArray(daily)||daily.length>n||JSON.stringify(p.daily?.metrics)!==JSON.stringify(["ym:s:visits","ym:s:pageviews"]))continue;
   const seen=new Set(),series=[];
   let bad=false;
   for(const row of daily){
    const date=row?.labels?.[0];
    if(!validDate(date)||date<p.start||date>p.end||seen.has(date)||!counts(row.metrics,2)){bad=true;break;}
    seen.add(date);series.push({date,visits:row.metrics[0],pageviews:row.metrics[1]});
   }
   if(bad)continue;
   periods[n]={start:p.start,end:p.end,visits:a[0],users:a[1],pageviews:a[2],bounceRate:a[0]?a[3]:null,depth:a[0]?a[4]:null,avgVisitSeconds:a[0]?a[5]:null,series:series.sort((x,y)=>x.date.localeCompare(y.date))};
  }
  let intraday=null;
  const t=r.intraday,a=t?.totals;
  if(t?.provisional===true&&validDate(t.date)&&["available","totals_only"].includes(t.status)&&JSON.stringify(t.metrics)===JSON.stringify(metricNames)&&nums(a,6)&&counts(a.slice(0,3),3)&&a[3]<=100){
   let hourly=null;
   if(t.status==="available"&&Array.isArray(t.hourly)&&t.hourly.length<=24){
    const seen=new Set();
    if(t.hourly.every(x=>Number.isInteger(x.hour)&&x.hour>=0&&x.hour<=23&&!seen.has(x.hour)&&seen.add(x.hour)&&counts([x.visits,x.pageviews],2)))hourly=t.hourly.map(x=>({hour:x.hour,visits:x.visits,pageviews:x.pageviews})).sort((x,y)=>x.hour-y.hour);
   }
   intraday={date:t.date,timezone:typeof t.timezone==="string"?t.timezone.slice(0,80):null,provisional:true,visits:a[0],users:a[1],pageviews:a[2],bounceRate:a[0]?a[3]:null,depth:a[0]?a[4]:null,avgVisitSeconds:a[0]?a[5]:null,hourly};
  }
  reports.push({channel,counterId:id,status:r.status,bindingVerified:false,usableForEditorialAdvice:false,periods,intraday});
 }
 return {source:"yandex_metrika",collectedAt:raw.collected_at,stale:now-Date.parse(raw.collected_at)>7200000,automaticSync:false,reports};
}
export async function readMetrika(token,fetcher=fetch,now=Date.now()){
 if(!token)return null;
 const opts={headers:{Authorization:"Bearer "+token,Accept:"application/vnd.github+json","User-Agent":"ZEN-Control-Analytics"},redirect:"manual",signal:AbortSignal.timeout(12000)};
 try{
  const meta=await fetcher(BASE,opts);
  if(!meta.ok)return null;
  const info=await meta.json();
  if(info.full_name!=="kaokshi/zen-editorial-review"||info.private!==true||info.visibility!=="private")return null;
  const r=await fetcher(BASE+"/contents/"+PATH,opts);
  if(!r.ok)return null;
  const f=await r.json();
  if(f.path!==PATH||f.type!=="file"||f.encoding!=="base64"||!Number.isSafeInteger(f.size)||f.size<1||f.size>100000||typeof f.content!=="string"||f.content.length>150000)return null;
  const bytes=Uint8Array.from(atob(f.content),x=>x.charCodeAt(0));
  if(bytes.length!==f.size)return null;
  return sanitizeMetrika(JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes)),now);
 }catch{return null;}
}
