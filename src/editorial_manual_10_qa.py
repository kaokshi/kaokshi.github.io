#!/usr/bin/env python3
"""Read-only checks on the frozen 5+5 owner manual-launch batch.

No automatic article changes, no rights claims, no approval or release. Takes
hashes from actual working tree and reports four tools + Russian LanguageTool.
"""
import hashlib
import json
import subprocess
import sys
from editorial_ai_check import evaluate
from editorial_export_manual_10 import BATCH, ROOT

OUT=ROOT/"editorial-manual-10-qa"
OUT.mkdir(exist_ok=True)
report={
 "schema":"manual-ten-readonly-qa-v1",
 "release_authorized":False,
 "ai_scores_mean_ai_probability":False,
 "full_science_fact_check":False,
 "image_rights_verified":False,
 "articles":[],
}
for channel,slugs in BATCH.items():
    for slug in slugs:
        article=ROOT/"content"/"articles"/(slug+".json")
        raw=article.read_bytes()
        values=evaluate(article.resolve())
        checks={}
        for key,item in values["checks"].items():
            data=item.get("data") if isinstance(item,dict) else None
            checks[key]={
                "status":item.get("status"),
                "reason":str(item.get("reason") or "")[:250] if item.get("status")!="completed" else None,
                "data":data if item.get("status")=="completed" else None
            }
        result_path=OUT/(slug+"-languagetool.json")
        proc=subprocess.run([sys.executable,str(ROOT/"src"/"editorial_language_tool.py"),
             "--slug",slug,"--output",str(result_path)],capture_output=True,text=True,timeout=300)
        if result_path.exists():
            grammar=json.loads(result_path.read_text(encoding="utf-8"))
            grammar_report={"status":grammar.get("status"),"issue_count":grammar.get("issue_count"),
               "issues":grammar.get("issues",[])[:80]}
        else:
            grammar_report={"status":"incomplete","reason":(proc.stderr or proc.stdout)[-400:]}
        report["articles"].append({
           "slug":slug,"channel":channel,
           "source_sha256":hashlib.sha256(raw).hexdigest(),
           "character_count":values["characters_without_spaces"],
           "four_checks_complete":values["all_checks_completed"],
           "checks":checks,
           "grammar":grammar_report,
           "rights_reviewed":False,
           "facts_verified_by_this_workflow":False,
           "owner_approved":False,
        })
        print("MANUAL_10_QA",json.dumps({"slug":slug,"channel":channel,
          "four_checks_complete":values["all_checks_completed"],
          "detectors":{k:v["status"] for k,v in checks.items()},
          "grammar_status":grammar_report.get("status"),
          "grammar_issue_count":grammar_report.get("issue_count")},ensure_ascii=False),flush=True)
destination=OUT/"manual-ten-qa-report.json"
destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
completed=sum(x["four_checks_complete"] for x in report["articles"])
print("MANUAL_10_QA_SUMMARY",completed,"of",len(report["articles"]),"complete",flush=True)
if completed!=10 or any(r["grammar"].get("status")!="completed" for r in report["articles"]):
    raise SystemExit("At least one article has incomplete diagnostics; do not claim editor approval")
