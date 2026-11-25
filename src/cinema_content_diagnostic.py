import asyncio, hashlib, json, os, re, subprocess, sys, tempfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from io import BytesIO
from PIL import Image
import imagehash
import imageio_ffmpeg
from src.editorial_visual_matching import compare_pixels, describe_pixels, load_model, Scene, verify_caption
from docx import Document
from docx.shared import Inches

FILMS={
'dune':('Dune: Part Two',2024,693134,[
('Пустынный мир','vast desert dunes',['sand','desert','dunes']),
('Жизнь среди песков','people wearing desert clothing',['people','person','man','woman']),
('Песчаные черви','giant sandworm in desert',['worm','sandworm','creature']),
('Политический конфликт','people in a dark monumental interior',['people','man','woman']),
('Военная угроза','soldiers or military vehicles',['soldier','soldiers','army','vehicle']),
('Противостояние','two people facing one another',['two','people','men']),
('Масштаб Арракиса','wide rocky desert landscape',['desert','sand','rocks','mountains'])]),
'wild_robot':('The Wild Robot',2024,1184918,[
('Необычный остров','robot in a forest',['robot']),
('Первые встречи','robot among wild animals',['robot']),
('Маленький гусь','small goose or gosling',['goose','gosling','bird']),
('Забота','robot holding or near a bird',['robot']),
('Лесное сообщество','group of forest animals',['animals','animal']),
('Испытание','robot in a snowy or stormy landscape',['robot']),
('Природа и технологии','wide forest landscape with robot',['forest','trees','robot'])]),
'wing_it':('Wing It!',2023,None,[
('Начало истории','animated anthropomorphic animal indoors',['animal','character','cat','dog']),
('Необычная встреча','two animated animal characters',['two','characters','animals']),
('План и подготовка','animated character near machinery or objects',['character','animal','cat','dog']),
('Действие набирает темп','animated characters in motion',['character','characters','animal']),
('Неожиданное препятствие','animated animal in a dynamic situation',['animal','character','cat','dog']),
('Совместное действие','two animated animals together',['two','animals','characters']),
('Последние кадры','animated animal character in a wide scene',['animal','character','cat','dog'])])}
def get(url,headers=None,limit=8_000_000):
    with urlopen(Request(url,headers={'User-Agent':'ZEN-Control-diagnostic/1.0 (editorial test)',**(headers or {})}),timeout=30) as r:
        b=r.read(limit+1)
        if len(b)>limit: raise ValueError('response_too_large')
        return b
def j(url,h=None):return json.loads(get(url,h))
async def outnow(title,year):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client('https://outnow.ch/mcp') as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            result=await s.call_tool('search_content',{'query':title,'language':'en','type':'title'})
            rows=(result.structuredContent or {}).get('results',[])
            return [x for x in rows if x.get('title','').casefold()==title.casefold() and x.get('year')==year]
def candidates(key,mid,title,year,tmp):
    rows=[]
    if mid:
        token=os.environ['TMDB_API_TOKEN'].strip()
        short=bool(re.fullmatch(r'[a-fA-F0-9]{32}',token))
        h={} if short else {'Authorization':'Bearer '+token}
        def api(path):
            return j('https://api.themoviedb.org/3'+path+('?' + urlencode({'api_key':token}) if short else ''),h)
        info=api('/movie/'+str(mid))
        assert info['original_title']==title and info['release_date'].startswith(str(year))
        raw=api('/movie/'+str(mid)+'/images')['backdrops']
        pool=sorted([x for x in raw if x.get('iso_639_1') is None and x['width']>=1280],key=lambda x:x['vote_average'],reverse=True)[:20]
        for x in pool:
            url='https://image.tmdb.org/t/p/w1280'+x['file_path']
            rows.append((url,get(url),'tmdb',None))
    else:
        q=urlencode({'action':'query','format':'json','titles':'File:WING IT! - Blender Open Movie-full movie.webm','prop':'imageinfo','iiprop':'url|size|extmetadata'})
        info=next(iter(j('https://commons.wikimedia.org/w/api.php?'+q)['query']['pages'].values()))['imageinfo'][0]
        assert info['extmetadata']['LicenseShortName']['value']=='CC BY 4.0'
        video=tmp/'film.webm';video.write_bytes(get(info['url'],limit=50_000_000))
        for t in range(10,190,9):
            p=tmp/f'{t}.jpg'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=960:-1','-y',str(p)],check=True,timeout=30)
            if p.exists():rows.append((info['url'],p.read_bytes(),'blender',t))
    out=[]
    for url,raw,source,t in rows:
        im=Image.open(BytesIO(raw)).convert('RGB'); ph=imagehash.phash(im)
        if any(ph-x['phash']<=6 for x in out):continue
        out.append(dict(url=url,source=source,time=t,sha256=hashlib.sha256(raw).hexdigest(),phash=ph,image=im))
    return out
def main():
    key=sys.argv[1];title,year,mid,chapters=FILMS[key]
    out=Path('diagnostic-output');out.mkdir(exist_ok=True)
    report={'title':title,'year':year,'publication_authorized':False,'status':'diagnostic','chapters':[]}
    def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    save()
    # OutNow permits occasional searches; no gallery downloads or bulk crawling.
    if key=='dune':
        try:report['outnow']=asyncio.run(asyncio.wait_for(outnow(title,year),40))
        except Exception as e:report['outnow_error']=type(e).__name__
    with tempfile.TemporaryDirectory() as d:
        rows=candidates(key,mid,title,year,Path(d))
        report['unique_candidates']=len(rows);save()
        labels=[x[1] for x in chapters]+['unrelated modern office photograph']
        for kind in ('clip','siglip'):
            for row in rows:
                row[kind],_=compare_pixels(kind,row['image'],labels)
            load_model.cache_clear()
            print(kind,'completed',len(rows),flush=True)
        for row in rows:row['caption']=describe_pixels(row['image'])
        load_model.cache_clear()
        used=set();doc=Document();doc.add_heading(f'{title} ({year}) — тест иллюстраций',0)
        doc.add_paragraph('Диагностический макет, не готовая статья и не разрешение на публикацию.')
        for n,(heading,subject,aliases) in enumerate(chapters):
            options=[]
            scene=Scene(subject,{'visible_subject':aliases},('watermark','movie poster','text overlay'))
            for idx,row in enumerate(rows):
                if idx in used:continue
                clip=row['clip'];sig=row['siglip']
                evidence=verify_caption(scene,row['caption'])
                margin=sig[n]-max(sig[-1],*[v for k,v in enumerate(sig[:-1]) if k!=n])
                passed=evidence['verdict']=='supported' and clip[n]>=.19 and clip[n]>clip[-1]+.015 and margin>0
                options.append((passed,margin,clip[n],idx,evidence))
            doc.add_heading(heading,1)
            if not options:
                report['chapters'].append({'heading':heading,'status':'missing'});continue
            passed,margin,clip,idx,evidence=max(options,key=lambda x:x[:3]);used.add(idx);row=rows[idx]
            item={k:v for k,v in row.items() if k not in ('image','phash','clip','siglip')}
            item.update(heading=heading,subject=subject,status='supported' if passed else 'review_required',clip=clip,siglip=row['siglip'][n],siglip_margin=margin,caption_check=evidence,phash=str(row['phash']))
            report['chapters'].append(item)
            # Only open licensed Blender pixels are retained in downloadable artifacts.
            if not mid:
                p=out/f'{n+1:02}.jpg';row['image'].save(p,quality=90);doc.add_picture(str(p),width=Inches(5.8))
            else:doc.add_paragraph('Кандидат TMDB: '+row['url'])
            doc.add_paragraph('Сцена для проверки: '+subject)
            doc.add_paragraph('Статус: '+item['status']+'; '+row['caption'])
            save()
        report['supported']=sum(x['status']=='supported' for x in report['chapters'])
        report['status']='completed_review_required' if report['supported']<7 else 'technical_scene_checks_pass'
        if not mid:doc.add_paragraph('Wing It! — Blender Studio, CC BY 4.0; кадры извлечены из видео Wikimedia Commons.')
        doc.save(out/'illustration-test.docx');save()
        print('RESULT',key,report['supported'],len(used),flush=True)
if __name__=='__main__':main()
