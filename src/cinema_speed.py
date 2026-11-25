import json,sys,time,tempfile,subprocess,gc,os
from pathlib import Path
import numpy as np
import torch
from scipy.optimize import linear_sum_assignment
from src.cinema_content_diagnostic import FILMS,candidates
from src.editorial_visual_matching import compare_pixels,load_model,Scene,verify_caption

def main():
    key=sys.argv[1];title,year,mid,chapters=FILMS[key]
    out=Path('speed-output');out.mkdir(exist_ok=True)
    report={'film':title,'publication_authorized':False,'independent_human_labels':False,'variants':{}}
    def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    def timed(name,fn):
        t=time.monotonic();v=fn();report[name+'_seconds']=round(time.monotonic()-t,2);save();return v
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        rows=timed('fetch',lambda:candidates(key,mid,title,year,tmp))
        labels=[x[1] for x in chapters]+['unrelated modern office photograph']
        scenes=[Scene(s,{'visible_subject':a},('watermark','movie poster','text overlay')) for _,s,a in chapters]
        def prefilter():
            for kind in ('clip','siglip'):
                for r in rows:r[kind],_=compare_pixels(kind,r['image'],labels)
                load_model.cache_clear();gc.collect()
        timed('prefilter',prefilter)
        def norm(m):return (m-m.mean(axis=1,keepdims=True))/(m.std(axis=1,keepdims=True)+1e-9)
        clip=np.array([r['clip'][:7] for r in rows]).T
        sig=np.array([r['siglip'][:7] for r in rows]).T
        fast=norm(clip)+norm(sig)
        paths=[]
        for i,r in enumerate(rows):
            p=tmp/f'{i}.jpg';im=r['image'].copy();im.thumbnail((448,448));im.save(p);paths.append(str(p))
        from sentence_transformers import CrossEncoder
        model=timed('qwen_load',lambda:CrossEncoder('Qwen/Qwen3-VL-Reranker-2B',device='cpu',model_kwargs={'dtype':torch.bfloat16}))
        def rank():
            matrix=np.empty((7,len(rows)))
            for n,s in enumerate(labels[:7]):
                matrix[n]=model.predict([(s,{'image':p}) for p in paths],prompt='Retrieve movie frames that visibly match the described scene.',batch_size=1,show_progress_bar=False)
            return matrix
        q=timed('qwen_full',rank)
        del model;gc.collect()
        # Compare the same pixels, model, prompt, and token cap in the reference.
        def caption(ids,cap,tag):
            manifest=tmp/f'{tag}-input.json';output=tmp/f'{tag}-output.json'
            manifest.write_text(json.dumps([paths[i] for i in ids]))
            subprocess.run(['.smol-venv/bin/python','src/cinema_smol_worker.py',str(manifest),str(output)],env={**os.environ,'SMOL_MAX_TOKENS':str(cap)},check=True,timeout=3600)
            values=json.loads(output.read_text())
            assert len(values)==len(ids)
            return dict(zip(ids,values))
        def assign(score,captions,mask):
            valid=np.array([[i in captions and verify_caption(s,captions[i])['verdict']=='supported' for i in range(len(rows))] for s in scenes]) & mask
            objective=np.concatenate([np.where(valid,score,-1e6),np.full((7,7),-1000.)],axis=1)
            x,y=linear_sum_assignment(objective,maximize=True)
            return [{'chapter':chapters[int(n)][0],'candidate':int(i),'url':rows[i]['url'],'sha256':rows[i]['sha256'],'caption':captions[i]} for n,i in zip(x,y) if i<len(rows) and valid[n,i]]
        full=timed('smol_full',lambda:caption(list(range(len(rows))),40,'full'))
        allmask=np.ones_like(q,dtype=bool)
        score=norm(q)+.25*norm(clip)+.25*norm(sig)
        report['variants']['reference']=assign(score,full,allmask);save()
        # Simulate shortlist recall using full Qwen scores, then measure actual
        # reduced Smol inference separately. No claim that Qwen time was saved here.
        for k in (3,5):
            mask=np.zeros_like(q,dtype=bool)
            for n in range(7):mask[n,np.argsort(fast[n])[-k:]]=True
            ids=sorted(set(np.where(mask)[1].tolist()))
            report[f'top{k}_pairs']=int(mask.sum());report[f'top{k}_unique']=len(ids)
            report['variants'][f'top{k}_same_captions']=assign(score,full,mask);save()
        mask=np.zeros_like(q,dtype=bool)
        for n in range(7):mask[n,np.argsort(fast[n])[-5:]]=True
        ids=sorted(set(np.where(mask)[1].tolist()))
        short=timed('smol_top5_24tokens',lambda:caption(ids,24,'short'))
        report['variants']['top5_24tokens']=assign(score,short,mask)
        report['candidate_count']=len(rows)
        report['status']='complete';save()
        print('SPEED_RESULT',json.dumps({k:len(v) for k,v in report['variants'].items()}),json.dumps({k:v for k,v in report.items() if k.endswith('_seconds') or k.endswith('_pairs') or k.endswith('_unique')}),flush=True)
if __name__=='__main__':main()
