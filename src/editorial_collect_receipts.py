#!/usr/bin/env python3
"""Collect verified same-run editorial diagnostics into a stage receipt.

Never fabricates a completed *check*. Missing input stays missing. Grammar
suggestions are not proof of mistakes; rights and science facts need a human.
The resulting receipt is not an authorization to publish or add RSS.
"""
import argparse
import json
from pathlib import Path
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_auto_route import digest_article


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def collect(article, quality, grammar, rss_valid, report_ref, *, style_limit=5):
    digest=digest_article(article)
    slug=article.get("slug")
    checks={k:{"status":"needs_review","reason":"No verified result supplied"}
        for k in ("grammar","style","sources","image_similarity","image_rights","fact_check","rss_dry_run")}
    if isinstance(grammar,dict) and grammar.get("slug")==slug and grammar.get("status")=="completed":
        issues=grammar.get("issues")
        if isinstance(issues,list) and isinstance(grammar.get("issue_count"),int):
            if grammar["issue_count"]>0:
                checks["grammar"]={"status":"needs_review","reason":
                    f"LanguageTool suggested {grammar['issue_count']} possible corrections; validate in context."}
            else:
                checks["grammar"]={"status":"completed","method":"local LanguageTool Russian",
                                   "note":"No suggestions in checked text"}
    results={}
    if isinstance(quality,dict):
        for row in quality.get("results",[]):
            if isinstance(row,dict) and row.get("slug")==slug:
                results=row
                break
    if results:
        style=results.get("style")
        if isinstance(style,dict):
            clichés=style.get("cliche_occurrences",{})
            long=style.get("long_sentences",[])
            if isinstance(clichés,dict) and isinstance(long,list):
                count=sum(v for v in clichés.values() if type(v) is int and v>0)
                if count>style_limit:
                    checks["style"]={"status":"failed","findings":[
                        "Избыточные штампованные обороты: "+str(count)+
                        ". Переписать по смыслу, без шаблонных конструкций."]}
                else:
                    checks["style"]={"status":"completed",
                        "note":"Heuristic cliché check completed; longer sentences are advisory",
                        "cliche_count":count,"long_sentence_count":len(long)}
        duplicate=quality.get("image_duplicates")
        if isinstance(duplicate,dict):
            images=results.get("images",{})
            if isinstance(images,dict) and images.get("count",0)>=7:
                # Some formats (e.g. SVG) are not covered by Pillow pHash.
                near=duplicate.get("near_duplicate_status")
                if near=="completed" and duplicate.get("scanned_images",0)>=images["count"]:
                    bad=[x for x in duplicate.get("duplicates",[]) if
                         isinstance(x,dict) and slug in x.get("articles",[])]
                    checks["image_similarity"]=(
                        {"status":"failed","findings":[
                            "Обнаружены совпадающие иллюстрации (по SHA-256 или pHash)."]}
                        if bad else {"status":"completed",
                             "method":"SHA-256 and pHash, all article images accounted for"})
        # Missing source URL validation is never treated as a pass.
        if not quality.get("external_links_verified"):
            checks["sources"]={"status":"needs_review",
                "reason":"Link validation or source relevancy has not been independently confirmed"}
        if not quality.get("image_rights_verified_by_this_check"):
            checks["image_rights"]={"status":"needs_review",
                "reason":"Copyright/license ownership must be substantiated by editorial records"}
        if article.get("channel")=="science":
            checks["fact_check"]={"status":"needs_review",
                "reason":"Scientific claims need relevant primary sources and verified IDs"}
        else:
            checks["fact_check"]={"status":"needs_review",
                "reason":"Original fiction and factual framing need confirmation"}
    if (isinstance(rss_valid,dict) and
            set(rss_valid)=={"cinema","legends","science"} and
            all(isinstance(v,dict) and v.get("valid") is True for v in rss_valid.values())):
        checks["rss_dry_run"]={"status":"completed",
            "method":"All three local RSS XML feeds parsed successfully; no deployment",
            "scope":"Technical feed syntax only, does not validate actual Zen import"}
    valid_ref=(isinstance(report_ref,str) and
        report_ref.startswith("https://github.com/") and "/actions/runs/" in report_ref)
    return {"schema":"editorial-stage-receipt-v1",
        "status":"completed" if valid_ref and results else "incomplete",
        "article_sha256":digest,
        "report_ref":report_ref if valid_ref else None,
        "checks":checks,
        "fiction_originality_reviewed":False,
        "verified_claim_ids":[],
        "publication_authorized":False,
        "notice":"Receipt collection completed is not proof that individual checks passed; needs_review remains open."}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--slug",required=True)
    p.add_argument("--quality-report",default="editorial-quality-reports/quality-report.json")
    p.add_argument("--grammar-report",default=None)
    p.add_argument("--rss-report",default="editorial-quality-reports/rss-validation.json")
    p.add_argument("--output-dir",default="editorial-stage-evidence")
    p.add_argument("--run-url",default=os.environ.get("GITHUB_RUN_URL",""))
    args=p.parse_args()
    file=ROOT/"content/articles"/(args.slug+".json")
    if not file.is_file():
        p.error("Article missing")
    article=json.loads(file.read_text("utf-8"))
    if article.get("slug")!=args.slug:
        p.error("Article slug mismatch")
    result=collect(article,read_json(args.quality_report),
        read_json(args.grammar_report or ("editorial-quality-reports/languagetool-"+args.slug+".json")),
        read_json(args.rss_report),args.run_url)
    out=Path(args.output_dir)
    out.mkdir(parents=True,exist_ok=True)
    (out/(args.slug+".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"slug":args.slug,"receipt_status":result["status"],
          "steps":{k:v["status"] for k,v in result["checks"].items()},
          "publication_authorized":False},ensure_ascii=False))
if __name__=="__main__":
    main()
