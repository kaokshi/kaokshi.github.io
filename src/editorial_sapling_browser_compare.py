#!/usr/bin/env python3
"""One-off Sapling web-FORM comparison (no API, no hidden endpoint).

Input texts are the repository's publicly saved draft articles.  If webpage
blocks automation, record the obstacle honestly and DO NOT bypass challenges.
This is diagnostic research ONLY, never publication clearance.
"""
import hashlib
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ARTICLES = (
    ("legends", "legends-01-prohodnaya-17", 5.97),
    ("science", "science-08-sinie-ogni-idzena", 92.71),
    ("cinema", "cinema-01-truman-show-real-photos", 14.56),
)
URL="https://sapling.ai/ai-content-detector"
OUT=Path("editorial-sapling-browser-compare")
OUT.mkdir(exist_ok=True)
LIMIT=1900  # conservative free tier; compare only the SAME text length when reporting.

def strip_html(html):
    from html.parser import HTMLParser
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(); self.x=[]
        def handle_data(self,s): self.x.append(s)
        def handle_starttag(self,t,a):
            if t in {"p","div","br","h1","h2","li"}: self.x.append("\n")
    obj=Text(); obj.feed(html)
    return re.sub(r"\n{3,}","\n\n","".join(obj.x)).strip()

def page_summary(page):
    return {
        "title":page.title(),
        "url":page.url,
        "textareas":page.locator("textarea").count(),
        "editable":page.locator('[contenteditable="true"]').count(),
        "textbox":page.get_by_role("textbox").count(),
        "visible_buttons":page.locator("button:visible").all_text_contents()[:18],
        "text_snippet":page.locator("body").inner_text()[:1500]
    }

def submit_one(page, text, slug):
    page.goto(URL,wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(3500)
    print("WEB_FORM_STATE",slug,json.dumps(page_summary(page),ensure_ascii=False),flush=True)
    page.screenshot(path=str(OUT/(slug+"-before.png")),full_page=False)
    target=None
    for selector in ('[contenteditable="true"]','textarea','[role="textbox"]'):
        loc=page.locator(selector)
        for i in range(min(loc.count(),8)):
            item=loc.nth(i)
            if item.is_visible():
                target=item
                print("WEB_FORM_EDITOR",slug,selector,i,flush=True)
                break
        if target is not None:break
    if target is None:
        return {"status":"no_visible_form","dom":page_summary(page)}
    try:
        target.fill(text,timeout=12000)
    except Exception as exc:
        return {"status":"form_fill_failed","error":type(exc).__name__+":"+str(exc)[:230]}
    page.wait_for_timeout(13000)
    print("WEB_FORM_AFTER_FILL",slug,json.dumps(page_summary(page),ensure_ascii=False)[:2800],flush=True)
    page.screenshot(path=str(OUT/(slug+"-after.png")),full_page=False)
    visible=page.locator("body").inner_text()
    # Only accept a literal visible result. Do not guess it from static percentage placeholders.
    matches=re.findall(r"(?:AI[- ](?:generation|detection) score|Fake|Вероятность ИИ|Оценка генерации ИИ)\s*:\s*([0-9]{1,3}(?:[.,][0-9]+)?)\s*%",visible,re.I)
    status="visible_score_found" if matches else "result_not_identified"
    return {"status":status,"visible_scores":matches,"result_excerpt":visible[:3800],
            "page_title":page.title(),"url":page.url}

def main():
    records=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=["--no-sandbox"])
        page=browser.new_page(viewport={"width":1280,"height":900},locale="ru-RU")
        for genre,slug,previous_full_index in ARTICLES:
            data=json.loads((Path("content/articles")/(slug+".json")).read_text("utf-8"))
            text=strip_html(data["body_html"])
            fragment=text[:LIMIT]
            result={
                "genre":genre,"slug":slug,"title":data["title"],
                "full_text_length":len(text),"checked_fragment_characters":len(fragment),
                "fragment_sha256":hashlib.sha256(fragment.encode()).hexdigest(),
                "internal_genre_index_on_FULL_TEXT":previous_full_index,
                "comparison_warning":"Internal index was from FULL TEXT, web check only FIRST 1900 chars: NOT directly comparable."
            }
            try:
                result.update(submit_one(page,fragment,slug))
            except Exception as e:
                result.update({"status":"web_browser_error","error":type(e).__name__+":"+str(e)[:350]})
            print("EXTERNAL_WEB_COMPARISON",json.dumps(result,ensure_ascii=False),flush=True)
            records.append(result)
        browser.close()
    (OUT/"comparison.json").write_text(json.dumps({
        "service":"Sapling free browser demo","url":URL,"no_api_used":True,
        "no_captcha_bypass":True,"results":records,
        "disclaimer":"Neither score proves author origin or Dzen platform classification"
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":main()
