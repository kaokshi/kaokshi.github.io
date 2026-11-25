import gc,json,sys,time,tempfile,resource,hashlib,subprocess
from pathlib import Path
import numpy as np
import torch
from scipy.optimize import linear_sum_assignment
from PIL import Image
import imagehash
from src.cinema_content_diagnostic import FILMS,candidates,get,j
from src.editorial_visual_matching import compare_pixels,describe_pixels,load_model,Scene,verify_caption
import imageio_ffmpeg
from urllib.parse import urlencode

def main():
    key=sys.argv[1];title,year,mid,chapters=FILMS[key]
    out=Path('comparison-output');out.mkdir(exist_ok=True)
    report={'film':title,'publication_authorized':False,'independent_human_labels':False,'variants':{}}
    def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    save()
    def stage(name,fn):
        start=time.monotonic()
        try:
            result=fn();report[name]={'status':'ok','seconds':round(time.monotonic()-start,2),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss};save();return result
        except Exception as e:
            report[name]={'status':'error','error_type':type(e).__name__,'seconds':round(time.monotonic()-start,2)};save();print('STAGE_FAILED',name,type(e).__name__,flush=True);return None
    def clean():
        load_model.cache_clear();gc.collect()
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        rows=stage('candidate_fetch',lambda:candidates(key,mid,title,year,tmp))
        if not rows:raise RuntimeError('candidate_fetch_failed')
        base_n=len(rows)
        report['baseline_candidate_count']=base_n
        # For open video, add shot-midpoint candidates; shared-pool comparison remains separate.
        if not mid:
            def shots():
                from scenedetect import detect,ContentDetector
                video=tmp/'film.webm'
                scenes=detect(str(video),ContentDetector(threshold=27.0))
                added=0
                for a,b in scenes[:40]:
                    t=(a.get_seconds()+b.get_seconds())/2
                    p=tmp/f'shot-{added}-{int(t*1000)}.jpg'
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=960:-1','-y',str(p)],check=True,timeout=30)
                    if not p.exists():continue
                    raw=p.read_bytes();im=Image.open(p).convert('RGB');ph=imagehash.phash(im)
                    if any(ph-r['phash']<=6 for r in rows):continue
                    rows.append(dict(url=rows[0]['url'],source='blender',time=t,sha256=hashlib.sha256(raw).hexdigest(),phash=ph,image=im));added+=1
                    if added>=8:break
                return {'detected_shots':len(scenes),'added':added}
            report['shot_detection_result']=stage('shot_detection',shots)
        report['candidate_count']=len(rows)
        labels=[x[1] for x in chapters]+['unrelated modern office photograph']
        scenes=[Scene(s,{'visible_subject':a},('watermark','movie poster','text overlay')) for _,s,a in chapters]
        def baseline():
            for kind in ('clip','siglip'):
                for r in rows:r[kind],_=compare_pixels(kind,r['image'],labels)
                clean()
            for r in rows:r['florence']=describe_pixels(r['image'])
            clean()
            used=set();chosen=[]
            for n,scene in enumerate(scenes):
                opts=[]
                for i,r in enumerate(rows[:base_n]):
                    if i in used:continue
                    clip=r['clip'];sig=r['siglip'];e=verify_caption(scene,r['florence'])
                    margin=sig[n]-max(sig[-1],*[v for k,v in enumerate(sig[:-1]) if k!=n])
                    passed=e['verdict']=='supported' and clip[n]>=.19 and clip[n]>clip[-1]+.015 and margin>0
                    opts.append((passed,margin,clip[n],i))
                if opts:
                    passed,margin,score,i=max(opts,key=lambda x:x[:3]);used.add(i);chosen.append((n,i,bool(passed)))
            return chosen
        a=stage('baseline_inference',baseline)
        def save_variant(name,chosen,caption_key):
            report['variants'][name]=[dict(chapter=chapters[n][0],candidate=i,sha256=rows[i]['sha256'],url=rows[i]['url'],time=rows[i]['time'],auto_supported=passed,caption=rows[i].get(caption_key),common_florence_supported=(verify_caption(scenes[n],rows[i]['florence'])['verdict']=='supported') if 'florence' in rows[i] else None) for n,i,passed in chosen]
            if not mid:
                folder=out/name;folder.mkdir(exist_ok=True)
                for n,i,p in chosen:rows[i]['image'].save(folder/f'{n+1}.jpg')
            save()
        if a is not None:save_variant('A',a,'florence')
        def qwen():
            from sentence_transformers import CrossEncoder
            model=CrossEncoder('Qwen/Qwen3-VL-Reranker-2B',device='cpu',model_kwargs={'dtype':torch.bfloat16})
            # Use local images, not remote image requests from the model.
            docs=[]
            for i,r in enumerate(rows):
                p=tmp/f'candidate-{i}.jpg';im=r['image'].copy();im.thumbnail((448,448));im.save(p);docs.append({'image':str(p)})
            scores=np.empty((7,len(rows)))
            for n,s in enumerate(labels[:7]):
                scores[n]=model.predict([(s,x) for x in docs],prompt='Retrieve movie frames that visibly match the described scene.',batch_size=1,show_progress_bar=False)
                print('QWEN_CHAPTER',n,flush=True)
            del model;gc.collect()
            return scores
        q=stage('qwen_inference',qwen)
        def smol():
            from transformers import AutoProcessor,AutoModelForImageTextToText
            name='HuggingFaceTB/SmolVLM2-2.2B-Instruct'
            processor=AutoProcessor.from_pretrained(name)
            model=AutoModelForImageTextToText.from_pretrained(name,dtype=torch.bfloat16).eval()
            for r in rows:
                im=r['image'].copy();im.thumbnail((448,448))
                messages=[{'role':'user','content':[{'type':'image'},{'type':'text','text':'Describe only visible objects, actions, setting and characters in this movie frame in one concise sentence. Do not guess names or plot.'}]}]
                text=processor.apply_chat_template(messages,add_generation_prompt=True)
                inputs=processor(text=text,images=[im],return_tensors='pt')
                with torch.inference_mode():ids=model.generate(**inputs,max_new_tokens=40,do_sample=False)
                r['smol']=processor.decode(ids[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True)
            del model,processor;gc.collect();return True
        s=stage('smol_inference',smol)
        def assign(name,score,n_candidates):
            # same caption-based acceptance criterion for B/C; not calibrated accuracy.
            valid=np.array([[verify_caption(scene,r['smol'])['verdict']=='supported' for r in rows[:n_candidates]] for scene in scenes])
            matrix=score[:,:n_candidates]
            # permit a missing match instead of forcing invalid selection
            objective=np.where(valid,matrix,-1e6)
            objective=np.concatenate([objective,np.full((7,7),-1000.)],axis=1)
            x,y=linear_sum_assignment(objective,maximize=True)
            chosen=[(int(n),int(i),True) for n,i in zip(x,y) if i<n_candidates and valid[n,i]]
            save_variant(name,chosen,'smol')
        if q is not None and s:
            assign('B_shared_pool',q,base_n)
            if len(rows)>base_n:assign('B_shot_pool',q,len(rows))
            if a is not None:
                def norm(m):
                    return (m-m.mean(axis=1,keepdims=True))/(m.std(axis=1,keepdims=True)+1e-9)
                clip=np.array([r['clip'][:7] for r in rows]).T
                sig=np.array([r['siglip'][:7] for r in rows]).T
                hybrid=norm(q)+.25*norm(clip)+.25*norm(sig)
                assign('C_shared_pool',hybrid,base_n)
                if len(rows)>base_n:assign('C_shot_pool',hybrid,len(rows))
        report['status']='complete' if a is not None and q is not None and s else 'partial_failure'
        save();print('COMPARISON_RESULT',key,{k:sum(x['auto_supported'] for x in v) for k,v in report['variants'].items()},flush=True)
        if report['status']!='complete':raise RuntimeError('incomplete_comparison')
if __name__=='__main__':main()
