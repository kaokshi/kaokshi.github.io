#!/usr/bin/env python3
"""Isolated, read-only tool benchmark on existing Zen drafts.

Records both completed and unavailable stages. Never approves, rewrites,
commits, publishes or transmits user draft bodies to outside services.
"""
import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import re
from time import perf_counter
import traceback
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/"src"))
from editorial_ai_check import extract_text

ARTICLES = ROOT/"content/articles"
OUT = ROOT/"editorial-benchmark-results"
SLUGS = {
    "legends": "legends-01-prohodnaya-17",
    "science": "science-01-krasnye-spraity",
    "cinema": "cinema-01-truman-show-real-photos"
}

def probe(label, fn):
    started=perf_counter()
    try:
        data=fn()
        return {"tool":label,"status":"completed","duration_seconds":round(perf_counter()-started,3),"result":data}
    except Exception as exc:
        return {"tool":label,"status":"unavailable","duration_seconds":round(perf_counter()-started,3),
                "error":type(exc).__name__+": "+str(exc)[:330]}

def read_articles():
    result={}
    for channel,slug in SLUGS.items():
        obj=json.loads((ARTICLES/(slug+".json")).read_text(encoding="utf-8"))
        assert obj["channel"]==channel
        result[channel]={"slug":slug,"text":extract_text(obj["body_html"]),"title":obj["title"]}
    return result

def readable(text,preset):
    from ruts import ReadabilityStats
    analysis=ReadabilityStats(text,preset=preset)
    return {
       "preset":preset,
       "flesch_reading_easy":round(float(analysis.flesch_reading_easy),2),
       "note":"Formula is an advisory readability metric, not a quality grade."
    }

def natasha_entities(text):
    from natasha import Segmenter, NewsEmbedding, NewsNERTagger, Doc
    doc=Doc(text[:11000])
    doc.segment(Segmenter())
    doc.tag_ner(NewsNERTagger(NewsEmbedding()))
    spans=[{"text":span.text,"type":span.type} for span in doc.spans[:24]]
    return {"entities_total":len(doc.spans),"sample":spans,
            "warning":"NER signals candidate names, not validated facts."}

def parse_feed():
    import feedparser
    sample='<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Наука — тест</title><link>https://example.org</link><description>Тест</description><item><title>Открытие</title><link>https://example.org/1</link><description>Новость</description></item></channel></rss>'
    parsed=feedparser.parse(sample)
    return {"fixture_items":len(parsed.entries),"bozo":bool(parsed.bozo),
            "network_discovery_tested":False}

def pyalex_search():
    import pyalex
    # Do not search user's unpublished content. Generic public science topic.
    old=pyalex.config.email
    # Unauthenticated/unauthorized calls must be marked unavailable.
    works=pyalex.Works().search("red sprites upper atmosphere").get(per_page=3)
    return {"works": [{"id":x.get("id"),"title":x.get("display_name"),"year":x.get("publication_year"),
                       "doi":x.get("doi")} for x in works],
            "topic":"red sprites upper atmosphere",
            "caution":"Search relevance is not scientific fact validation."}

def candidate_info():
    return {
      "actionlint":"CI yaml/static shell lint; GitHub Actions only",
      "gitleaks":"Scan checkout for committed secrets, read-only",
      "create-pull-request":"Needs separate write-permission rehearsal; do not auto-create PR from user's articles",
      "reviewdog":"Needs annotated PR and permission-aware reporter test, not ordinary script",
      "playwright-mcp":"Requires separate browser environment, consent, and site-specific policy",
      "n8n":"Requires maintained worker/server; not needed while GitHub Actions+Automations suffice",
      "changedetection.io":"Requires permanent service, free GitHub runner alone cannot monitor continuously",
      "CLIP":"Vision model requires GPU/CPU weights and verified reference images; defer",
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default=str(OUT))
    args=parser.parse_args()
    articles=read_articles()
    result={"schema":"editorial-tools-benchmark-v1",
      "created_utc":datetime.now(timezone.utc).isoformat(),
      "branch_only":True,"site_modified":False,"publication_authorized":False,
      "samples":{k:{"slug":v["slug"],"characters":len(v["text"])} for k,v in articles.items()},
      "probes":[],
      "candidates_not_executed":candidate_info()}
    for channel,article in articles.items():
        preset={"legends":"fiction","science":"academic","cinema":"plainrussian"}[channel]
        result["probes"].append({"channel":channel,**probe("ruTS",lambda a=article,p=preset:readable(a["text"],p))})
        result["probes"].append({"channel":channel,**probe("Natasha NER",lambda a=article:natasha_entities(a["text"]))})
    result["probes"].append({"channel":"science",**probe("PyAlex OpenAlex public literature query",pyalex_search)})
    result["probes"].append({"channel":"discovery",**probe("feedparser RSS fixture",parse_feed)})
    dest=Path(args.output)
    dest.mkdir(parents=True,exist_ok=True)
    (dest/"benchmark.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in result["probes"]:
        print("BENCH",json.dumps(row,ensure_ascii=False)[:2000])
    print("TOTAL",json.dumps({"completed":sum(x["status"]=="completed" for x in result["probes"]),
          "unavailable":sum(x["status"]=="unavailable" for x in result["probes"]),"publication_authorized":False}))

if __name__=="__main__":
    main()
