#!/usr/bin/env python3
"""Read-only test of original SVG artwork and pilot document safeguards."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_ai_check import extract_text
from editorial_auto_route import decide
from editorial_quality import inspect_article

SLUG="legends-11-devyaty-etazh-na-rechnoy"

def main():
    article_path=ROOT/"content/articles"/(SLUG+".json")
    article=json.loads(article_path.read_text("utf-8"))
    assert article["channel"]=="legends"
    assert article.get("test_only") is True
    assert article.get("status")=="draft"
    assert article.get("release_hold") is True
    assert article.get("auto_release_candidate") is False
    assert article.get("publication_authorized") is False
    images=article["image_files"]
    assert len(images)==7 and len(set(images))==7
    digest_map={}
    for name in images:
        full=ROOT/name
        assert full.exists(),name
        assert full.suffix==".svg"
        root=ET.parse(full).getroot()
        assert root.tag.endswith("svg"),name
        assert root.attrib.get("viewBox")=="0 0 1400 900",name
        digest_map[name]=hashlib.sha256(full.read_bytes()).hexdigest()
    assert len(set(digest_map.values()))==7
    text=extract_text(article["body_html"])
    chars=len("".join(text.split()))
    assert chars>=3000
    quality=inspect_article(article_path)
    decision=decide(article,{})
    assert decision["disposition"]=="send_for_owner_review"
    settings=json.loads((ROOT/"config/site.json").read_text("utf-8"))
    assert settings["publication_enabled"] is False
    result={
        "article_slug":SLUG,
        "character_count_no_whitespace":chars,
        "illustrations_svg":len(images),
        "all_svg_files_valid_xml":True,
        "all_image_hashes_distinct":True,
        "original_image_sha256":digest_map,
        "local_quality_warnings":quality["warnings"],
        "local_style":quality["style"],
        "route_decision_without_unsupported_evidence":decision,
        "rss_site_modified":False,
        "publication_authorized":False,
        "notice":"This pilot is an original illustrated story. ImageHash Pillow JPEG/PNG-only tests cannot confirm SVG visual similarity; exact SHA256 uniqueness has been checked."
    }
    output=ROOT/"editorial-pilot-reports"
    output.mkdir(exist_ok=True)
    (output/"pilot-local-audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("article_slug","character_count_no_whitespace","illustrations_svg",
            "all_svg_files_valid_xml","all_image_hashes_distinct","local_quality_warnings",
            "rss_site_modified","publication_authorized")},ensure_ascii=False))
    print("ROUTING",decision["disposition"],json.dumps(decision["blockers"],ensure_ascii=False))

if __name__=="__main__":
    main()
