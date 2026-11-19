#!/usr/bin/env python3
"""Build a three-channel static site with gated publication and RSS 2.0."""
import html
import json
import re
import shutil
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "site"
ARTICLES = BASE / "content" / "articles"
CHANNELS = {
    "cinema": ("Кадр за Кадром", "Кино и детали, которые остаются после титров.", "🎬", "#e8bc6a"),
    "legends": ("Легенды тёмных улиц", "Авторские городские истории с холодком.", "☾", "#c8a9dc"),
    "science": ("За гранью очевидного", "Наука, космос и необычные явления природы.", "✦", "#79d0e8"),
}
def esc(s):
    return html.escape(str(s), quote=True)
def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
def config():
    result = json.loads((BASE / "config" / "site.json").read_text(encoding="utf-8"))
    url = result["site_url"].rstrip("/")
    if urlparse(url).scheme not in ("https", "http"):
        raise ValueError("Invalid public site_url")
    return result, url
def strip_tags(raw):
    return re.sub(r"<[^>]+>", "", raw)
def parse_date(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Publication time must have timezone")
    return dt
def read_posts(now):
    # Global publication freeze: NO articles or populated RSS without explicit approval.
    settings, _ = config()
    if settings.get('publication_enabled') is not True:
        return []
    published = []
    for path in sorted(ARTICLES.glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8"))
        if post.get("channel") not in CHANNELS:
            raise ValueError("Unknown channel in " + str(path))
        if post.get("status") != "approved":
            continue
        # A separate, per-article release command is mandatory.
        if post.get("publication_authorized") is not True or post.get("release_hold") is not False:
            continue
        if post.get("editorial_reviewed") is not True or post.get("rights_reviewed") is not True:
            continue
        # Legacy single-detector ai_score is not sufficient for publication.
        # A documented review of at least two independent completed checks is required.
        checks = post.get("ai_checks", [])
        if post.get("ai_reviewed") is not True or not isinstance(checks, list):
            continue
        providers = {
            check.get("provider", "").strip().lower()
            for check in checks
            if isinstance(check, dict)
            and check.get("status") == "completed"
            and isinstance(check.get("provider"), str)
            and check.get("provider", "").strip()
        }
        if len(providers) < 2:
            continue
        # Main editorial detector must have an actual traceable, completed review.
        # The other three checks are comparison-only, never averaged or thresholded.
        # This does NOT grant publication: user authorization remains mandatory.
        by_provider = {
            ch.get("provider", "").strip().lower(): ch
            for ch in checks
            if isinstance(ch, dict)
            and isinstance(ch.get("provider"), str)
            and ch.get("status") == "completed"
        }
        required = {"rubert_v4", "modernbert", "lightgbm", "ai_accent_detector"}
        if not required.issubset(by_provider):
            continue
        if any(not isinstance(by_provider[k].get("report_ref"), str)
               or not by_provider[k]["report_ref"].strip() for k in required):
            continue
        rubert_score = by_provider["rubert_v4"].get("score_percent")
        if (isinstance(rubert_score, bool)
                or not isinstance(rubert_score, (int, float))
                or not 0 <= rubert_score <= 25):
            continue
        if len(re.sub(r"\s", "", strip_tags(post.get("body_html", "")))) < 3000:
            continue
        images = post.get("image_files", [])
        if len(images) < 7 or any(not (BASE / f).is_file() for f in images):
            continue
        date = parse_date(post["publish_at"])
        if date > now:
            continue
        if not re.fullmatch("[a-z0-9][a-z0-9-]{1,100}", post.get("slug", "")):
            raise ValueError("Unsafe slug")
        post["date"] = date
        post["path"] = "/%s/%s/" % (post["channel"], post["slug"])
        published.append(post)
    return sorted(published, key=lambda a: a["date"], reverse=True)
CSS = r"""
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;background:#0c1422;color:#e8eef8}*{box-sizing:border-box}
body{margin:0}a{color:inherit;text-decoration:none}.container{max-width:1080px;margin:auto;padding:0 24px}
header{border-bottom:1px solid #ffffff20;background:#101a2a;position:sticky;top:0;z-index:10}nav{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:72px}
.brand{font-size:20px;font-weight:800;letter-spacing:.02em}.navlinks{display:flex;gap:20px;flex-wrap:wrap;color:#cbd9e8;font-size:14px}
.hero{padding:80px 0 55px;background:radial-gradient(ellipse at 82% 25%,#2a4c7770,transparent 60%),linear-gradient(145deg,#0a1729,#152c41)}
h1{font-size:clamp(32px,5vw,58px);line-height:1.11;margin:0 0 18px;letter-spacing:-.03em}h2{font-size:23px;margin:0 0 11px}.lead{max-width:720px;color:#bbcadb;font-size:19px;line-height:1.5}
.eyebrow{color:#83bfd4;letter-spacing:.12em;font-size:12px;font-weight:700;text-transform:uppercase}
main{min-height:55vh;padding-top:48px!important;padding-bottom:62px!important}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
.card,.post{border:1px solid #ffffff20;border-radius:16px;overflow:hidden;background:#172438;display:block;transition:transform .2s}.card:hover,.post:hover{transform:translateY(-3px)}
.art{height:170px;display:flex;align-items:center;justify-content:center;font-size:72px;background:radial-gradient(ellipse at 35% 35%,#507da9,#182c44)}
.art.legends{background:radial-gradient(ellipse at 80% 35%,#785f73,#15192d)}.art.science{background:radial-gradient(ellipse at 25% 35%,#43849b,#10213f)}
.info{padding:23px}.info p,.post p{color:#b8c7d8;line-height:1.5}.chip{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#a0b5d0}
.posts{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.post{padding:22px}.date{color:#8ca2ba;font-size:12px;margin-bottom:8px}
.rss{display:inline-block;margin:25px 0;padding:9px 15px;border:1px solid #50738e;border-radius:30px;color:#a4d6e9}.article{max-width:750px;margin:auto}
.article h1{font-size:clamp(30px,5vw,46px)}.article-body{font-size:18px;line-height:1.75}.article-body figure{margin:28px 0}.article-body img{width:100%;height:auto;border-radius:12px}
.empty{border:1px dashed #506078;border-radius:12px;padding:32px;color:#a9bdd3}footer{border-top:1px solid #ffffff20;padding:26px 0;color:#a1b4c9;font-size:14px}
@media(max-width:750px){.grid,.posts{grid-template-columns:1fr}.hero{padding:50px 0 30px}nav{flex-direction:column;padding:16px 0}.art{height:130px}}
"""
def shell(title, body, desc, canonical, site_url):
    return ( '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)} — Три истории мира</title>'
        f'<meta name="description" content="{esc(desc)}">'
        f'<link rel="canonical" href="{esc(canonical)}">'
        '<link rel="stylesheet" href="/styles.css">'
        '</head><body><header><nav class="container"><a href="/" class="brand">✦ Три истории мира</a>'
        '<div class="navlinks"><a href="/cinema/">Кино</a><a href="/legends/">Легенды</a>'
        '<a href="/science/">Наука</a></div></nav></header>'
        + body + '<footer><div class="container">© Редакция · <a href="/rss/">RSS</a> · '
        '<a href="/about/">О проекте</a></div></footer></body></html>' )
def abs_content(raw, url):
    return re.sub(r'(src|href)="/', lambda m: m.group(1)+'="'+url+'/', raw)
def rss(channel_key, channel, posts, site_url):
    name, desc, icon, color = channel
    root = ET.Element("rss", {"version":"2.0", "xmlns:content":"http://purl.org/rss/1.0/modules/content/"})
    c = ET.SubElement(root,"channel")
    for k,v in (("title",name),("link",site_url+"/"+channel_key+"/"),("description",desc),("language","ru"),("lastBuildDate",format_datetime(datetime.now(timezone.utc)))):
        ET.SubElement(c,k).text=v
    for post in posts[:500]:
        i=ET.SubElement(c,"item")
        for k,v in (("title",post["title"]),("link",site_url+post["path"]),("guid",site_url+post["path"]),("description",strip_tags(post["body_html"])[:320]),("pubDate",format_datetime(post["date"]))):
            ET.SubElement(i,k).text=v
        ET.SubElement(i,"{http://purl.org/rss/1.0/modules/content/}encoded").text=abs_content(post["body_html"],site_url)
        if post["image_files"]:
            image=post["image_files"][0].replace("content/assets/","assets/")
            ET.SubElement(i,"enclosure",{"url":site_url+"/"+image,"type":"image/jpeg","length":str((BASE/post["image_files"][0]).stat().st_size)})
    return '<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(root,encoding="unicode")
def build(now=None):
    now=now or datetime.now(timezone.utc)
    _,url=config()
    posts=read_posts(now)
    OUT.mkdir(parents=True,exist_ok=True)
    write(OUT/"styles.css",CSS)
    cards=''.join(f'<a class="card" href="/{k}/"><div class="art {k}">{v[2]}</div><div class="info"><span class="chip">Направление</span><h2>{esc(v[0])}</h2><p>{esc(v[1])}</p></div></a>' for k,v in CHANNELS.items())
    landing='<section class="hero"><div class="container"><p class="eyebrow">Независимая редакция</p><h1>Истории, которые меняют взгляд на мир.</h1><p class="lead">Кино, городские легенды и удивительные открытия. Три направления, каждый день новые идеи.</p></div></section><main class="container"><h2>Наши каналы</h2><div class="grid">'+cards+'</div></main>'
    write(OUT/"index.html",shell("Главная",landing,"Три тематических направления",url+"/",url))
    for key,ch in CHANNELS.items():
        subset=[p for p in posts if p["channel"]==key]
        listing=''.join(f'<a class="post" href="{esc(p["path"])}"><div class="date">{p["date"].strftime("%d.%m.%Y")}</div><h2>{esc(p["title"])}</h2><p>{esc(strip_tags(p["body_html"])[:150])}…</p></a>' for p in subset)
        content=f'<main class="container"><h1>{esc(ch[0])}</h1><p class="lead">{esc(ch[1])}</p><a class="rss" href="/feeds/{key}.xml">RSS-лента</a>'
        content+=(f'<div class="posts">{listing}</div>' if listing else '<div class="empty">Готовим первые публикации. Они появятся после редакционного утверждения.</div>')+'</main>'
        write(OUT/key/"index.html",shell(ch[0],content,ch[1],url+"/"+key+"/",url))
        for p in subset:
            body=f'<main class="container"><article class="article"><p class="date">{p["date"].strftime("%d.%m.%Y")}</p><h1>{esc(p["title"])}</h1><div class="article-body">{p["body_html"]}</div></article></main>'
            write(OUT/key/p["slug"]/"index.html",shell(p["title"],body,strip_tags(p["body_html"])[:150],url+p["path"],url))
        feed=rss(key,ch,subset,url)
        ET.fromstring(feed)
        write(OUT/"feeds"/(key+".xml"),feed)
    write(OUT/"rss"/"index.html",shell("RSS",'<main class="container"><h1>RSS-ленты</h1>'+''.join(f'<p><a class="rss" href="/feeds/{k}.xml">{esc(v[0])}</a></p>' for k,v in CHANNELS.items())+'</main>',"RSS-ленты",url+"/rss/",url))
    write(OUT/"about"/"index.html",shell("О проекте",'<main class="container"><h1>О проекте</h1><p class="lead">Три редакционных направления: кино, авторские художественные истории и доступный научпоп.</p></main>',"О проекте",url+"/about/",url))
    paths=["/","/rss/","/about/"]+[f"/{k}/" for k in CHANNELS]+[p["path"] for p in posts]
    sitemap=ET.Element("urlset",{"xmlns":"http://www.sitemaps.org/schemas/sitemap/0.9"})
    for path in paths:
        ET.SubElement(ET.SubElement(sitemap,"url"),"loc").text=url+path
    write(OUT/"sitemap.xml",'<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(sitemap,encoding="unicode"))
    write(OUT/"robots.txt",f"User-agent: *\nAllow: /\nSitemap: {url}/sitemap.xml\n")
    write(OUT/".nojekyll","")
    for p in posts:
        for f in p["image_files"]:
            dest=OUT/f.replace("content/assets/","assets/")
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(BASE/f,dest)
    print(json.dumps({"url":url,"published":len(posts),"feeds":[f"/feeds/{k}.xml" for k in CHANNELS]},ensure_ascii=False))
if __name__=="__main__":
    build()
