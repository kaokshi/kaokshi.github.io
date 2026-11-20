#!/usr/bin/env python3
"""Build traceable reviewdog messages for actual editorial paragraphs.

No article changes. Any message maps to an exported, line-oriented preview.
Diagnostics are *suggestions*, not factual errors or release blockers.
"""
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_ai_check import extract_text

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"editorial-remaining-tests/reviewdog"
SLUGS=[
 "legends-01-prohodnaya-17","science-01-krasnye-spraity",
 "cinema-01-truman-show-real-photos"]
CANDIDATE_CLICHES=["стоит отметить","в современном мире","таким образом",
                  "погрузимся в","важно понимать"]

def diagnostics(paragraphs, slug, min_words=24):
    notes=[]
    for line,para in enumerate(paragraphs,1):
        # No artificial complaints: flag only an actual long sentence/phrase.
        sentences=re.split(r"(?<=[.!?])\s+",para)
        long=max((len(s.split()) for s in sentences),default=0)
        if long>=min_words:
            notes.append({"slug":slug,"line":line,"column":1,
                          "code":"long_sentence",
                          "message":f"Редакторская подсказка: предложение из {long} слов. Проверьте ритм; исправление необязательно."})
        lowered=para.lower()
        for phrase in CANDIDATE_CLICHES:
            if phrase in lowered:
                notes.append({"slug":slug,"line":line,"column":max(1,lowered.index(phrase)+1),
                              "code":"possible_cliche",
                              "message":f"Редакторская подсказка: оборот «{phrase}» может быть шаблонным; оцените контекст."})
    return notes

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    all_notes=[]
    for slug in SLUGS:
        article=json.loads((ROOT/"content/articles"/(slug+".json")).read_text(encoding="utf-8"))
        # One line per real paragraph, preserving meaningful error locations.
        pieces=re.findall(r"<p(?:\s+[^>]*)?>(.*?)</p>",article["body_html"],re.DOTALL|re.IGNORECASE)
        paragraphs=[re.sub(r"\s+"," ",extract_text(p)).strip() for p in pieces]
        paragraphs=[p for p in paragraphs if p]
        path=OUT/(slug+".md")
        path.write_text("\n".join(paragraphs)+"\n",encoding="utf-8")
        notes=diagnostics(paragraphs,slug)
        for item in notes:
            item["preview_path"]=str(path.relative_to(ROOT))
        all_notes.extend(notes)
    patterns=[f'{n["preview_path"]}:{n["line"]}:{n["column"]}:{n["message"]}' for n in all_notes]
    (OUT/"diagnostics.txt").write_text("\n".join(patterns)+("\n" if patterns else ""),encoding="utf-8")
    (OUT/"diagnostics.json").write_text(json.dumps({
        "schema":"reviewdog-real-article-preview-v1","notes":all_notes,
        "total":len(all_notes),"publication_authorized":False,
        "source_articles_modified":False,
        "note":"Locations apply to preview .md, not to single-line JSON body_html."},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"article_count":len(SLUGS),"diagnostics":len(all_notes),
          "publication_authorized":False},ensure_ascii=False))
if __name__=="__main__":
    main()
