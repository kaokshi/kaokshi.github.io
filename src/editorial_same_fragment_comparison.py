#!/usr/bin/env python3
"""Exact 1900-character same-text comparison to Sapling browser submissions.

Four existing detectors only. The three coefficients are loaded unchanged
from the successful research training runs. Never modifies article or RSS.
"""
import argparse
import hashlib
import html
import json
import tempfile
from pathlib import Path

from editorial_ai_check import extract_text, evaluate
from editorial_research_train import four_features, predict

SAMPLES = {
    "legends": ("legends-01-prohodnaya-17","legends-expanded-model.json",
        "e1247f5ede1f7ebebe48590dcf5ba1db30d1827d4d316415490fc29cae021457",86.6),
    "science": ("science-08-sinie-ogni-idzena","science-expanded-model.json",
        "d49533783b3be36af308740797f1e9aba8b1aeab65a174edb47328368df403e9",100.0),
    "cinema": ("cinema-01-truman-show-real-photos","cinema-plots-model.json",
        "ae939198a347e13c0b2a347d11e5b652e3483e3ebad3ca57d77ef3f8e57f91a3",99.9),
}
WHOLE_ARTICLE_INDICES={"legends":5.97,"science":92.71,"cinema":14.56}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--genre",choices=tuple(SAMPLES),required=True)
    p.add_argument("--models",default="editorial-models")
    p.add_argument("--out",default="editorial-same-fragment")
    a=p.parse_args()
    slug, filename, expected_hash, external_score=SAMPLES[a.genre]
    raw=Path("content/articles")/(slug+".json")
    original=json.loads(raw.read_text("utf-8"))
    fragment=extract_text(original["body_html"])[:1900]
    digest=hashlib.sha256(fragment.encode("utf-8")).hexdigest()
    if digest != expected_hash:
        raise RuntimeError("Input differs from proven Sapling web-form text! "+digest)
    model_path=Path(a.models)/filename
    model=json.loads(model_path.read_text("utf-8"))
    if model.get("genre")!=a.genre or model.get("status")!="research_only_unvalidated":
        raise RuntimeError("Invalid research model")
    with tempfile.TemporaryDirectory() as tmp:
        sample=Path(tmp)/"article.json"
        sample.write_text(json.dumps({
            "slug":slug,"channel":a.genre,"body_html":html.escape(fragment)
        },ensure_ascii=False),encoding="utf-8")
        checked=evaluate(sample.resolve())
    scores=four_features(checked)
    internal=round(100*predict(model,{"features":scores}),2)
    report={
        "genre":a.genre,"slug":slug,
        "comparison_scope":"the exact SAME FIRST 1900 extracted plaintext characters",
        "verified_fragment_sha256":digest,
        "internal_same_fragment_genre_index":internal,
        "external_sapling_same_fragment_score":external_score,
        "internal_full_article_index_for_reference_only":WHOLE_ARTICLE_INDICES[a.genre],
        "base_detector_scores_0_100":scores,
        "internal_model_artifact_sha256":hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "web_form_run":"https://github.com/kaokshi/kaokshi.github.io/actions/runs/44747699570",
        "status":"research_only_unvalidated",
        "note":"Different detectors' numeric scales do not have equivalent interpretation; neither proves authorship or Dzen response.",
        "publication_authorized":False
    }
    out=Path(a.out)
    out.mkdir(exist_ok=True)
    (out/(a.genre+"-same-fragment.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("SAME_FRAGMENT_COMPARISON",json.dumps(report,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
