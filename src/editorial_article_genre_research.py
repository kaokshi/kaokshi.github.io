#!/usr/bin/env python3
"""Read-only genre research scores on every existing article in the repository.

Use the EXACT models trained in the successful 20 November 2026 Actions jobs.
No weights refit, no threshold activation, no publication, no article changes.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from editorial_ai_check import evaluate
from editorial_research_train import four_features, predict

ARTICLES = Path("content/articles")
SOURCES = {
    "legends": {"run": 44735041717, "model": "legends-expanded-model.json"},
    "science": {"run": 44735041762, "model": "science-expanded-model.json"},
    "cinema": {"run": 44735041824, "model": "cinema-plots-model.json"},
}

def run(genre, models, out):
    meta = SOURCES[genre]
    model_file = models / meta["model"]
    if not model_file.is_file():
        raise RuntimeError("Training model artifact unavailable: " + str(model_file))
    model = json.loads(model_file.read_text(encoding="utf-8"))
    if model.get("genre") != genre or model.get("status") != "research_only_unvalidated":
        raise RuntimeError("Unexpected model metadata: " + genre)
    if model.get("publication_authorized") is not False:
        raise RuntimeError("Research model must not authorize publication")
    paths = sorted(ARTICLES.glob(genre+"-*.json"))
    if not paths:
        raise RuntimeError("No source articles for " + genre)
    results = []
    for path in paths:
        article = json.loads(path.read_text(encoding="utf-8"))
        if article.get("channel") != genre or article.get("slug") != path.stem:
            raise RuntimeError("Article channel or slug mismatch: " + path.name)
        if not article.get("body_html"):
            raise RuntimeError("Missing article text: " + path.name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        response = evaluate(path.resolve())
        try:
            features = four_features(response)
            value = round(100*predict(model,{"features":features}), 2)
            record = {
                "article":article["slug"], "title":article["title"],
                "channel":genre, "file_sha256":digest,
                "characters_without_spaces":response["characters_without_spaces"],
                "rubert_v4":round(features["rubert_v4"],2),
                "modernbert":round(features["modernbert"],2),
                "lightgbm":round(features["lightgbm"],2),
                "ai_accent_detector":round(features["ai_accent_detector"],2),
                "genre_research_index_0_to_100":value,
                "above_planned_30":value>30,
                "all_four_checks_completed":True,
                "note":"UNCALIBRATED_DIAGNOSTIC_NOT_PERCENT_AUTHORSHIP"
            }
            results.append(record)
            print("ARTICLE_GENRE_RESULT",json.dumps(record,ensure_ascii=False),flush=True)
        except Exception as e:
            failures = {k:v.get("status") for k,v in response["checks"].items()}
            record = {
                "article":article["slug"],"title":article["title"],
                "channel":genre,"file_sha256":digest,
                "all_four_checks_completed":False,"error_type":type(e).__name__,
                "check_statuses":failures,
            }
            results.append(record)
            print("ARTICLE_GENRE_ERROR",json.dumps(record,ensure_ascii=False),flush=True)
    out.mkdir(parents=True,exist_ok=True)
    report = {
        "schema":"zen-article-genre-research-v1",
        "genre":genre,"source_run":meta["run"],
        "model_sha256":hashlib.sha256(model_file.read_bytes()).hexdigest(),
        "repository_articles":len(paths),
        "checks_complete":sum(bool(x["all_four_checks_completed"]) for x in results),
        "status":"research_only_unvalidated",
        "publication_authorized":False,"rss_modified":False,
        "threshold_applied_to_publication":False,
        "articles":results
    }
    (out/(genre+"-article-research.json")).write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"
    )
    fields=["article","title","channel","characters_without_spaces","rubert_v4",
            "modernbert","lightgbm","ai_accent_detector",
            "genre_research_index_0_to_100","above_planned_30",
            "all_four_checks_completed","file_sha256"]
    with (out/(genre+"-article-research.csv")).open("w",newline="",encoding="utf-8-sig") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print("ARTICLE_GENRE_SUMMARY",json.dumps({
        "genre":genre,"source_run":meta["run"],"articles":len(paths),
        "complete":report["checks_complete"],
        "above_30":sum(bool(x.get("above_planned_30")) for x in results),
        "no_release":True
    }),flush=True)
    if report["checks_complete"] != len(paths):
        raise RuntimeError("Not all detector runs completed: "+str(report["checks_complete"])+"/"+str(len(paths)))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--genre",choices=tuple(SOURCES),required=True)
    p.add_argument("--models",default="editorial-models")
    p.add_argument("--out",default="editorial-article-genre-results")
    a=p.parse_args()
    run(a.genre,Path(a.models),Path(a.out))

if __name__=="__main__":
    main()
