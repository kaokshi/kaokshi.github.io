// Runs only in GitHub Actions. The write-capable editorial token never enters Worker.
const REPO="kaokshi/zen-editorial-review";
const ROOT="routerai-validation";
const MAX=85000;
const validPath=p=>/^routerai-validation\/[0-9]{8,20}$/.test(p||"");
export async function buildSnapshot(token){
 if(!token)return null;
 async function get(path){
  const r=await fetch("https://api.github.com/repos/"+REPO+path,{headers:{Authorization:"Bearer "+token,Accept:"application/vnd.github+json","User-Agent":"Zen-Control-Snapshot","X-GitHub-Api-Version":"2022-11-28"},redirect:"error",signal:AbortSignal.timeout(15000)});
  if(r.status===404)return null;
  if(!r.ok)throw Error("private_snapshot_http_"+r.status);
  return r.json();
 }
 const info=await get("");
 if(info?.full_name!==REPO||info.private!==true||info.visibility!=="private")throw Error("private_repo_not_verified");
 const head=await get("/commits/"+encodeURIComponent(info.default_branch));
 if(!/^[0-9a-f]{40}$/.test(head?.sha||""))throw Error("private_revision_unverified");
 const ref="?ref="+head.sha;
 const list=await get("/contents/"+ROOT+ref);
 if(!Array.isArray(list))return null;
 const dirs=list.filter(x=>x.type==="dir"&&/^[0-9]{8,20}$/.test(x.name)).sort((a,b)=>a.name.length===b.name.length?b.name.localeCompare(a.name):b.name.length-a.name.length).slice(0,12);
 async function read(path,limit=50000){
  const obj=await get("/contents/"+path+ref);
  if(!obj)return null;
  if(obj.path!==path||obj.encoding!=="base64"||typeof obj.content!=="string"||obj.size>limit)throw Error("private_file_invalid");
  return Buffer.from(obj.content,"base64").toString("utf8");
 }
 let report=null,root=null;
 for(const dir of dirs){
  const path=ROOT+"/"+dir.name;
  const raw=await read(path+"/test-results.json",180000);
  if(raw){report=JSON.parse(raw);root=path;break;}
 }
 if(!report)return null;
 const roleNames={brain:"Qwen",draft:"Claude",edit:"DeepSeek",image:"Seedream"};
 const num=v=>v!==null&&v!==undefined&&v!==""&&Number.isFinite(Number(v))?Number(v):null;
 const safeText=(v,n=240)=>typeof v==="string"?v.slice(0,n):null;
 const models=Object.entries(roleNames).map(([role,name])=>{
  const m=report.spend?.by_role?.[role]||{};
  return {name,rub:num(m.rub),calls:num(m.calls),input:num(m.tokens_in),output:num(m.tokens_out),images:num(m.images)};
 });
 const articles=[];
 for(const [channel,name] of [["legends","Легенды тёмных улиц"],["science","За гранью очевидного"],["cinema","Кадр за Кадром"]]){
  const row=report.articles?.[channel];
  if(!row)continue;
  const source=validPath(row.evidence_path)?row.evidence_path:root;
  const dir=source+"/"+channel;
  const text=await read(dir+"/deepseek-edited.md",30000);
  const files=Array.isArray(row.accepted_files)?row.accepted_files.filter(x=>/^image-[0-9]{2}\.(?:jpg|jpeg|png)$/.test(x)).slice(0,12):[];
  articles.push({channel,name,text,assembled:row.assembled===true,textQA:safeText(row.text_qa),imageQA:safeText(row.image_qa),accepted:num(row.accepted_images),rub:num(report.spend?.by_channel_rub?.[channel]),sourceUrl:"https://github.com/"+REPO+"/tree/"+head.sha+"/"+dir,images:files.map(file=>({name:file,url:"https://github.com/"+REPO+"/blob/"+head.sha+"/"+dir+"/images/"+file}))});
 }
 const summary=await read(root+"/summary-ru.md",30000);
 const offline=await read(root+"/offline-image-review-ru.md",30000);
 const result={run:root.split("/").pop(),checkedAt:new Date().toISOString(),status:safeText(report.status),stage:safeText(report.stage),reason:safeText(report.reason),total:num(report.spend?.metered_total_rub),budget:num(report.budget_rub),models,articles,summary,offline,reportUrl:"https://github.com/"+REPO+"/blob/"+head.sha+"/"+root+"/test-results.json"};
 const bytes=Buffer.from(JSON.stringify(result));
 if(bytes.length>MAX)throw Error("snapshot_too_large");
 return result;
}
export function snapshotBindings(snapshot){
 if(!snapshot)return [];
 const data=Buffer.from(JSON.stringify(snapshot)).toString("base64");
 const chunks=data.match(/.{1,3500}/g)||[];
 if(chunks.length>36)throw Error("snapshot_binding_limit");
 return [{type:"plain_text",name:"REVIEW_SNAPSHOT_COUNT",text:String(chunks.length)},...chunks.map((text,i)=>({type:"secret_text",name:"REVIEW_SNAPSHOT_"+i,text}))];
}
