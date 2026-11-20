#!/usr/bin/env python3
"""Read-only editorial audit: never changes publication state or submits RSS."""
from pathlib import Path
import json
from editorial_length import count_characters, length_finding

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / "content" / "articles"
def main():
    files = sorted(ARTICLES.glob("*.json"))
    summary = {"total":len(files),"drafts":0,"approved":0,"channels":{},"short_text":[],"long_text":[],"missing_images":[],"pending_rights":[],"pending_editorial":[],"ai_review_pending":[],"independent_ai_checks_missing":[]}
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        name = d.get("title",p.name)
        channel = d.get("channel","unknown")
        summary["channels"][channel] = summary["channels"].get(channel,0)+1
        summary["drafts" if d.get("status") == "draft" else "approved"] += 1
        count = count_characters(d.get("body_html", ""))
        issue = length_finding(count)
        if issue:
            summary["short_text" if issue == "short_article" else "long_text"].append(
                {"article": name, "characters": count})
        images = d.get("image_files",[])
        if len(images)<7:summary["missing_images"].append({"article":name,"count":len(images)})
        if d.get("rights_reviewed") is not True: summary["pending_rights"].append(name)
        if d.get("editorial_reviewed") is not True: summary["pending_editorial"].append(name)
        if d.get("ai_reviewed") is not True:summary["ai_review_pending"].append(name)
        checks=d.get("ai_checks",[])
        providers={x.get("provider","").strip().lower() for x in checks if isinstance(x,dict) and x.get("status")=="completed" and isinstance(x.get("provider"),str) and x.get("provider","").strip()} if isinstance(checks,list) else set()
        if len(providers)<2:summary["independent_ai_checks_missing"].append(name)
    print("EDITORIAL AUDIT — SAFE, READ-ONLY",flush=True)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    (ROOT/"editorial-audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
