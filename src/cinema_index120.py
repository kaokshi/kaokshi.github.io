"""Experimental two-phase cinema index. Never authorizes publication."""
import hashlib,json,sqlite3,sys,time,tempfile,subprocess,os,gc
from pathlib import Path
import numpy as np
import torch
from scipy.optimize import linear_sum_assignment
from src.cinema_content_diagnostic import FILMS,candidates
from src.editorial_visual_matching import load_model,Scene,verify_caption

VERSION='index-v1'
def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def batch_scores(kind,rows,labels):
    processor,model=load_model(kind)
    result=[]
    for start in range(0,len(rows),4):
        images=[r['image'] for r in rows[start:start+4]]
        kwargs=dict(padding=True,truncation=True,max_length=77) if kind=='clip' else dict(padding='max_length',truncation=True,max_length=64)
        inputs=processor(images=images,text=labels,return_tensors='pt',**kwargs)
        with torch.inference_mode():
            scores=model(**inputs).logits_per_image
            if kind=='clip':scores=scores/model.logit_scale.exp()
        result.extend(scores.float().tolist())
    load_model.cache_clear();gc.collect()
    return np.array(result).T

def main():
    mode,key=sys.argv[1:3]
    title,year,mid,chapters=FILMS[key]
    labels=[x[1] for x in chapters]+['unrelated modern office photograph']
    scenes=[Scene(s,{'visible_subject':a},('watermark','movie poster','text overlay')) for _,s,a in chapters]
    signature=digest([VERSION,key,chapters])
    out=Path('index-output');out.mkdir(exist_ok=True)
    db=sqlite3.connect(out/'index.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    def put(k,v):
        db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(k,json.dumps(v,ensure_ascii=False)));db.commit()
    def get(k):
        row=db.execute('SELECT value FROM cache WHERE key=?',(k,)).fetchone()
        return json.loads(row[0]) if row else None
    report={'film':title,'mode':mode,'publication_authorized':False,'independent_human_labels':False}
    def save(): (out/f'{mode}-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    def timed(name,fn):
        t=time.monotonic();print('START',name,flush=True)
        try:return fn()
        finally:
            report[name+'_seconds']=round(time.monotonic()-t,3);save();print('DONE',name,report[name+'_seconds'],flush=True)
    def norm(m):return (m-m.mean(axis=1,keepdims=True))/(m.std(axis=1,keepdims=True)+1e-9)
    start=time.monotonic()
    if mode=='prepare':
        with tempfile.TemporaryDirectory() as d:
            tmp=Path(d)
            rows=timed('fetch',lambda:candidates(key,mid,title,year,tmp))
            if not rows:raise RuntimeError('no_candidates')
            meta=[{k:r[k] for k in ('sha256','url','time')} for r in rows]
            pool=digest([r['sha256'] for r in rows])
            put(signature+':pool',pool)
            put(signature+':meta',meta)
            scores=[]
            for kind in ('clip','siglip'):
                ck=digest([VERSION,kind,pool,labels])
                m=get(ck)
                if m is None:
                    m=timed(kind+'_batch',lambda:batch_scores(kind,rows,labels)).tolist();put(ck,m)
                scores.append(np.array(m)[:7])
            fast=norm(scores[0])+norm(scores[1])
            pairs=[np.argsort(fast[n])[-3:].tolist() for n in range(7)]
            paths={}
            for i in sorted(set(sum(pairs,[]))):
                p=tmp/f'{i}.jpg';im=rows[i]['image'].copy();im.thumbnail((448,448));im.save(p);paths[i]=str(p)
            qkey=digest([VERSION,'qwen2b-bf16',pool,labels,pairs])
            q=get(qkey)
            if q is None:
                from sentence_transformers import CrossEncoder
                model=timed('qwen_load',lambda:CrossEncoder('Qwen/Qwen3-VL-Reranker-2B',device='cpu',model_kwargs={'dtype':torch.bfloat16}))
                def rank():
                    matrix=np.full((7,len(rows)),-1e6)
                    for n,ix in enumerate(pairs):
                        matrix[n,ix]=model.predict([(labels[n],{'image':paths[i]}) for i in ix],prompt='Retrieve movie frames that visibly match the described scene.',batch_size=1,show_progress_bar=False)
                    return matrix.tolist()
                q=timed('qwen_rank',rank);put(qkey,q);del model;gc.collect()
            model_id='HuggingFaceTB/SmolVLM2-500M-Video-Instruct'
            ids=sorted(paths)
            def capkey(i):return digest([VERSION,model_id,24,meta[i]['sha256']])
            missing=[i for i in ids if get(capkey(i)) is None]
            if missing:
                manifest=tmp/'input.json';result=tmp/'output.json'
                manifest.write_text(json.dumps([paths[i] for i in missing]))
                timed('smol_light',lambda:subprocess.run(['.smol-venv/bin/python','src/cinema_smol_worker.py',str(manifest),str(result)],env={**os.environ,'SMOL_MODEL_ID':model_id,'SMOL_MAX_TOKENS':'24'},check=True,timeout=900))
                captions=json.loads(result.read_text())
                if len(captions)!=len(missing):raise RuntimeError('caption_count_mismatch')
                for i,c in zip(missing,captions):put(capkey(i),c)
            caps={str(i):get(capkey(i)) for i in ids}
            def unresolved(captions):
                valid=np.zeros((7,len(rows)),dtype=bool)
                for n,ix in enumerate(pairs):
                    for i in ix:
                        c=captions.get(str(i))
                        valid[n,i]=bool(c and verify_caption(scenes[n],c)['verdict']=='supported')
                obj=np.concatenate([np.where(valid,np.array(q),-1e6),np.full((7,7),-1000.)],axis=1)
                x,y=linear_sum_assignment(obj,maximize=True)
                filled={int(n) for n,i in zip(x,y) if i<len(rows) and valid[n,i]}
                return [n for n in range(7) if n not in filled]
            gaps=unresolved(caps)
            # One heavy caption per unresolved chapter, deduplicated by frame.
            heavy_ids=sorted({max(pairs[n],key=lambda i:q[n][i]) for n in gaps})
            heavy_model='HuggingFaceTB/SmolVLM2-2.2B-Instruct'
            def heavykey(i):return digest([VERSION,heavy_model,24,meta[i]['sha256']])
            missing_heavy=[i for i in heavy_ids if get(heavykey(i)) is None]
            if missing_heavy:
                manifest=tmp/'heavy-input.json';result=tmp/'heavy-output.json'
                manifest.write_text(json.dumps([paths[i] for i in missing_heavy]))
                timed('smol_heavy',lambda:subprocess.run(['.smol-venv/bin/python','src/cinema_smol_worker.py',str(manifest),str(result)],env={**os.environ,'SMOL_MODEL_ID':heavy_model,'SMOL_MAX_TOKENS':'24'},check=True,timeout=600))
                values=json.loads(result.read_text())
                if len(values)!=len(missing_heavy):raise RuntimeError('heavy_caption_count_mismatch')
                for i,c in zip(missing_heavy,values):put(heavykey(i),c)
            # Do not silently accept a contradictory light/heavy result.
            heavy_caps={str(i):get(heavykey(i)) for i in heavy_ids}
            put(signature+':ready',{'pool':pool,'meta':meta,'pairs':pairs,'q':q,'caps':caps,'heavy_caps':heavy_caps})
            report['heavy_candidates']=len(heavy_ids)
            report['light_unresolved']=len(gaps)
            report['indexed_candidates']=len(ids)
            report['qwen_pairs']=sum(map(len,pairs))
    elif mode=='select':
        ready=get(signature+':ready')
        if ready is None:
            report['status']='index_missing';save();raise RuntimeError('index_missing')
        q=np.array(ready['q']);meta=ready['meta'];caps=ready['caps'];heavy=ready.get('heavy_caps',{})
        valid=np.zeros(q.shape,dtype=bool)
        for n,ix in enumerate(ready['pairs']):
            for i in ix:
                c=caps.get(str(i))
                light_verdict=verify_caption(scenes[n],c)['verdict'] if c else 'uncertain'
                h=heavy.get(str(i))
                heavy_verdict=verify_caption(scenes[n],h)['verdict'] if h else 'uncertain'
                valid[n,i]=('contradiction' not in (light_verdict,heavy_verdict) and 'supported' in (light_verdict,heavy_verdict))
        objective=np.concatenate([np.where(valid,q,-1e6),np.full((7,7),-1000.)],axis=1)
        x,y=linear_sum_assignment(objective,maximize=True)
        selected=[{'chapter':chapters[n][0],**meta[i],'caption':caps[str(i)],'heavy_caption':heavy.get(str(i))} for n,i in zip(x,y) if i<len(meta) and valid[n,i]]
        report['selected']=selected
        report['unresolved']=7-len(selected)
        report['needs_heavy_review']=len(selected)<7
        report['scope']='exact indexed chapter plan only; no image downloads or model inference in select phase'
    else:raise ValueError(mode)
    report['elapsed_seconds']=round(time.monotonic()-start,3)
    report['status']='complete';save();print('INDEX_RESULT',json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
