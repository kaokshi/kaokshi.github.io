#!/usr/bin/env python3
"""Cinema-only isolated diagnostic; never publishes or asserts model validity."""
import argparse
import json
from pathlib import Path
import editorial_research_v2 as exp
from editorial_research_train import four_features as strict_features

def diagnosed_four_features(report):
    if not report.get("all_checks_completed"):
        statuses={}
        for name,row in report.get("checks",{}).items():
            statuses[name]={
                "status":row.get("status"),
                "exit_code":row.get("exit_code"),
                "reason":str(row.get("reason",""))[:500],
                "output_excerpt":str(row.get("output_excerpt",""))[:180],
            }
        print("CINEMA_DETECTOR_FAILURE",json.dumps({
            "slug_hash":str(report.get("article_slug",""))[:24],
            "text_length_without_spaces":report.get("characters_without_spaces"),
            "detector_results":statuses},ensure_ascii=False),flush=True)
    return strict_features(report)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",default="editorial-cinema-diagnostics")
    args=p.parse_args()
    exp.four_features=diagnosed_four_features
    exp.run("cinema",Path(args.out).resolve())

if __name__=="__main__":
    main()
