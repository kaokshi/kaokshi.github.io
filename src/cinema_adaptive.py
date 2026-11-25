import json,sys,time,tempfile,subprocess,gc,os,resource
from pathlib import Path
import numpy as np
import torch
from scipy.optimize import linear_sum_assignment
from src.cinema_content_diagnostic import FILMS,candidates
from src.editorial_visual_matching import compare_pixels,load_model,Scene,verify_caption

def main():
    key=sys.argv[1];title,year,mid,chapters=FILMS[key]
    out=Path('adaptive-output');out.mkdir(exist_ok=True)
    report={'film':title,'publication_authorized':False,'independent_human_labels':False,'variants':{}}
    def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    def timed(name,fn):
        t=time.monotonic();print('START',name,flush=True);v=fn();report[name+'_seconds']=round(time.monotonic()-t,2);save();print('DONE',name,report[name+'_seconds'],flush=True);return v
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
        mask=np.zeros((7,len(rows)),dtype=bool)
        for n in range(7):mask[n,np.argsort(fast[n])[-3:]]=True
        def rank():
            matrix=np.full((7,len(rows)),-1e6)
            for n,s in enumerate(labels[:7]):
                ix=np.flatnonzero(mask[n]);v=model.predict([(s,{'image':paths[i]}) for i in ix],prompt='Retrieve movie frames that visibly match the described scene.',batch_size=1,show_progress_bar=False)
                matrix[n,ix]=v
            return matrix
        q=timed('qwen_shortlist',rank)
        report['qwen_pairs']=int(mask.sum());save()
        del model;gc.collect()
        # Compare the same pixels, model, prompt, and token cap in the reference.
        def caption(ids,cap,tag,model_id):
            if not ids:return {}
            manifest=tmp/f'{tag}-input.json';output=tmp/f'{tag}-output.json'
            manifest.write_text(json.dumps([paths[i] for i in ids]))
            subprocess.run(['.smol-venv/bin/python','src/cinema_smol_worker.py',str(manifest),str(output)],env={**os.environ,'SMOL_MAX_TOKENS':str(cap),'SMOL_MODEL_ID':model_id},check=True,timeout=3600)
            values=json.loads(output.read_text())
            assert len(values)==len(ids)
            return dict(zip(ids,values))
        def assign(score,captions,mask):
            valid=np.array([[i in captions and verify_caption(s,captions[i])['verdict']=='supported' for i in range(len(rows))] for s in scenes]) & mask
            objective=np.concatenate([np.where(valid,score,-1e6),np.full((7,7),-1000.)],axis=1)
            x,y=linear_sum_assignment(objective,maximize=True)
            return [{'chapter':chapters[int(n)][0],'candidate':int(i),'url':rows[i]['url'],'sha256':rows[i]['sha256'],'caption':captions[i]} for n,i in zip(x,y) if i<len(rows) and valid[n,i]]
        ids=sorted(set(np.where(mask)[1].tolist()))
        report['unique_shortlist']=len(ids)
        score=q
        heavy='HuggingFaceTB/SmolVLM2-2.2B-Instruct'
        light='HuggingFaceTB/SmolVLM2-500M-Video-Instruct'
        # Light pass first; use identical pixels and token cap for both models.
        small=timed('smol_500m',lambda:caption(ids,24,'small',light))
        report['variants']['B_light']=assign(score,small,mask);save()
        # Escalate only candidates on chapters not assigned by the light pass.
        filled={v['chapter'] for v in report['variants']['B_light']}
        unresolved=[n for n in range(7) if chapters[n][0] not in filled]
        escalation=sorted(set(i for n in unresolved for i in np.flatnonzero(mask[n]).tolist()))
        report['escalation_count']=len(escalation)
        extra=timed('smol_hybrid_escalation',lambda:caption(escalation,24,'extra',heavy))
        report['variants']['C_hybrid']=assign(score,{**small,**extra},mask);save()
        big=timed('smol_2_2b',lambda:caption(ids,24,'big',heavy))
        report['variants']['A_heavy']=assign(score,big,mask);save()
        report['candidate_count']=len(rows)
        report['parent_peak_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report['status']='complete';save()
        print('ADAPTIVE_RESULT',json.dumps({k:len(v) for k,v in report['variants'].items()}),json.dumps({k:v for k,v in report.items() if k.endswith('_seconds') or k in ('qwen_pairs','unique_shortlist','escalation_count','parent_peak_rss_kib')}),flush=True)
if __name__=='__main__':main()
