import json,os,sys,time,tempfile,subprocess
from pathlib import Path
from src.cinema_content_diagnostic import FILMS,candidates
from src.editorial_visual_matching import Scene,verify_caption

def main():
    mode=sys.argv[1]
    title,year,mid,chapters=FILMS['dune']
    out=Path('int8-output');out.mkdir(exist_ok=True)
    report={'mode':mode,'film':title,'publication_authorized':False,'independent_human_labels':False}
    def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        rows=candidates('dune',mid,title,year,tmp)
        # Deterministic sample, report hashes to confirm cross-job equality.
        rows=sorted(rows,key=lambda r:r['sha256'])[:3]
        if len(rows)!=3:raise RuntimeError('insufficient_candidates')
        paths=[]
        for i,r in enumerate(rows):
            p=tmp/f'{i}.jpg';im=r['image'].copy();im.thumbnail((448,448));im.save(p);paths.append(str(p))
        report['hashes']=[r['sha256'] for r in rows];save()
        manifest=tmp/'input.json';result=tmp/'output.json'
        manifest.write_text(json.dumps(paths))
        env={**os.environ,'SMOL_MODEL_ID':'HuggingFaceTB/SmolVLM2-500M-Video-Instruct','SMOL_MAX_TOKENS':'24','SMOL_INT8':'selective' if mode=='int8' else '0','SMOL_FP32':'1' if mode=='fp32' else '0'}
        t=time.monotonic()
        try:
            subprocess.run(['.smol-venv/bin/python','src/cinema_smol_worker.py',str(manifest),str(result)],env=env,check=True,timeout=600)
        finally:
            report['worker_seconds']=round(time.monotonic()-t,2);save()
        caps=json.loads(result.read_text())
        if len(caps)!=3:raise RuntimeError('caption_count_mismatch')
        scenes=[Scene(s,{'visible_subject':a},('watermark','movie poster','text overlay')) for _,s,a in chapters]
        report['frames']=[{'sha256':r['sha256'],'url':r['url'],'caption':c,'supported_chapters':[chapters[n][0] for n,s in enumerate(scenes) if c and verify_caption(s,c)['verdict']=='supported']} for r,c in zip(rows,caps)]
        report['empty_captions']=sum(not c for c in caps)
        report['status']='complete' if all(caps) else 'quality_failure';save();print('INT8_RESULT',json.dumps(report,ensure_ascii=False),flush=True)
        if not all(caps):raise RuntimeError('empty_caption_quality_failure')
if __name__=='__main__':main()
