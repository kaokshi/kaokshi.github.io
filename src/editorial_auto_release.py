#!/usr/bin/env python3
"""Apply already-verified automatic release to one NEW active-channel draft.

Never creates verification evidence. A missing/failed report routes to owner.
The caller must commit changed article/config and trigger guarded Pages build.
"""
import argparse
import json
from pathlib import Path
import re
from datetime import datetime, timezone

from editorial_ai_check import ROOT, CONTENT
from editorial_auto_route import decide, digest_article


def officially_launched(settings, policy=None):
    if policy is None:
        policy = json.loads((ROOT / "config/ai_revision_policy.json").read_text())
    rss = policy.get("future_rss_delivery", {})
    return (settings.get("publication_enabled") is True and rss.get("project_launched") is True
            and rss.get("currently_active") is True
            and policy.get("editorial_escalation", {}).get("automatic_publication_authorized") is True)


def prepare_release(article, evidence, settings, *, policy=None):
    decision = decide(article, evidence, launched=officially_launched(settings, policy))
    if decision["disposition"] != "automatic_release_candidate":
        return {"decision": decision, "article": None, "settings": None}
    if article.get("status") != "draft":
        decision["blockers"].append("only_new_drafts_can_auto_publish")
        decision["disposition"] = "send_for_owner_review"
        return {"decision": decision, "article": None, "settings": None}
    if settings.get("auto_release_enabled") is not True:
        decision["blockers"].append("auto_release_feature_not_enabled")
        decision["disposition"] = "send_for_owner_review"
        return {"decision": decision, "article": None, "settings": None}
    new = dict(article)
    new.update({
        "status": "approved",
        "editorial_reviewed": True,
        "rights_reviewed": True,
        "ai_reviewed": True,
        "publication_authorized": True,
        "release_hold": False,
        "release_mode": "automatic_with_verified_evidence",
        "auto_release_record": {
            "article_sha256": digest_article(article),
            "editorial_risk_score": decision["risk_score"],
            "risk_threshold": decision["review_if_above"],
            "evidence_version": "editorial-auto-route-v1",
            "released_at": datetime.now(timezone.utc).isoformat(),
        },
        "ai_checks": [
            {"provider": provider, "status": "completed",
             "report_ref": evidence["ai"]["report_ref"]}
            for provider in ("modernbert", "lightgbm", "transformer", "ai_accent_detector")
        ],
    })
    return {"decision": decision, "article": new, "settings": dict(settings)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--evidence-dir", default=str(ROOT / "editorial-release-evidence"))
    p.add_argument("--output", default=str(ROOT / "editorial-routing-reports"))
    p.add_argument("--apply", action="store_true", help="Only after all evidence verified")
    args = p.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.slug):
        p.error("Invalid slug")
    path = CONTENT / (args.slug + ".json")
    if not path.is_file():
        p.error("Article missing")
    post = json.loads(path.read_text("utf-8"))
    if post.get("slug") != args.slug:
        p.error("Slug mismatch")
    evidence_path = Path(args.evidence_dir) / (args.slug + ".json")
    evidence = json.loads(evidence_path.read_text("utf-8")) if evidence_path.is_file() else {}
    settings_path = ROOT / "config/site.json"
    settings = json.loads(settings_path.read_text("utf-8"))
    prepared = prepare_release(post, evidence, settings)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    decision = prepared["decision"]
    (out / (args.slug + ".json")).write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.apply and prepared["article"] is not None:
        path.write_text(json.dumps(prepared["article"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        print("RELEASE_ELIGIBLE", args.slug, "risk", decision["risk_score"])
    else:
        print("ROUTE", args.slug, decision["disposition"], json.dumps(decision["blockers"], ensure_ascii=False))
    # Being sent to owner review is not a tool failure; nothing is released.

if __name__ == "__main__":
    main()
