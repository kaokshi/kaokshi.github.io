#!/usr/bin/env python3
"""First-party editorial stage router: actionable revision vs review vs next stage.

Never publishes, mutates drafts or fabricates model checks. AI detectors are
diagnostics; only a separately validated and configured policy could flag them.
Evidence must match the current article AND image bytes via a SHA-256 digest.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from editorial_ai_check import extract_text
from editorial_auto_route import digest_article, valid_evidence
from editorial_ai_revision_policy import decide_revision

ACTIVE = {"legends", "science"}
REQUIRED_AI = ("rubert_v4", "modernbert", "lightgbm", "ai_accent_detector")
REQUIRED_STEPS = ("grammar", "style", "sources", "image_similarity", "image_rights",
                  "fact_check", "rss_dry_run")
REWORK_REASONS = {
    "short_article": "Увеличить содержательный объём текста до 3000+ знаков без пробелов.",
    "missing_images": "Добавить недостающие изображения по смыслу (не менее 7).",
    "repeated_images": "Заменить повторяющиеся изображения, проверить соответствие сценам.",
    "missing_image_file": "Восстановить недоступный файл изображения.",
}
REVIEW_REASONS = {
    "science_claims_need_review": "Проверить фактические утверждения по первоисточникам.",
    "fiction_needs_label": "Уточнить, что история художественная, если факты выдуманы.",
    "license_needs_review": "Проверить источник и разрешение использования каждого изображения.",
}


def stage_decision(article, evidence, ai_report=None, ai_policy=None):
    """Return deterministic verdict. The output is advisory, never release auth."""
    digest=digest_article(article)
    slug=article.get("slug")
    channel=article.get("channel")
    revision=[]
    review=[]
    waiting=[]
    if channel not in ACTIVE:
        return {"schema":"stage-gate-v1","slug":slug,"channel":channel,
                "disposition":"paused","next_stage":None,
                "revision_actions":[],"editor_review_actions":[],
                "awaiting_checks":[],"article_sha256":digest,
                "publication_authorized":False,"rss_modified":False}
    if article.get("status") not in ("draft", "in_revision", None):
        review.append("Article is not an editable draft")
    if article.get("release_hold") is True:
        # A release hold does not prevent review, but always prevents publication.
        pass
    if len(re.sub(r"\s","",extract_text(article.get("body_html",""))))<3000:
        revision.append(REWORK_REASONS["short_article"])
    imgs=article.get("image_files") or []
    if len(imgs)<7:
        revision.append(REWORK_REASONS["missing_images"])
    if len(set(imgs)) != len(imgs):
        revision.append(REWORK_REASONS["repeated_images"])
    if any(not isinstance(p,str) or not p.startswith("content/assets/")
           or ".." in Path(p).parts or not (ROOT/p).is_file() for p in imgs):
        revision.append(REWORK_REASONS["missing_image_file"])

    if channel=="science":
        claims=article.get("claims")
        if not isinstance(claims,list) or not claims:
            review.append(REVIEW_REASONS["science_claims_need_review"])
    if channel=="legends" and article.get("fiction") is not True:
        review.append(REVIEW_REASONS["fiction_needs_label"])
    sources=article.get("image_sources")
    if not article.get("rights_basis") or not isinstance(sources,list) or len(sources)!=len(imgs):
        review.append(REVIEW_REASONS["license_needs_review"])

    ai=ai_report if isinstance(ai_report,dict) else {}
    if (ai.get("article_sha256") != digest or ai.get("article_slug") != slug or
            ai.get("all_checks_completed") is not True or
            not isinstance(ai.get("checks"),dict) or
            any(not isinstance(ai["checks"].get(p),dict) or
                ai["checks"][p].get("status")!="completed" for p in REQUIRED_AI)):
        waiting.append("ai_four_github_models_missing_or_stale")
    else:
        policy_result=decide_revision(channel,ai,policy=ai_policy)
        if policy_result["decision"]=="targeted_editorial_revision":
            review.append("Проверить отмеченные AI-моделями места и исправить только реальные недостатки.")
        elif policy_result["decision"]=="incomplete_ai_tests":
            waiting.append("ai_revision_policy_input_incomplete")

    if not isinstance(evidence,dict):
        evidence={}
    if not valid_evidence(evidence,digest):
        waiting.append("editorial_evidence_missing_or_stale")
    else:
        checks=evidence.get("checks")
        if not isinstance(checks,dict):
            waiting.append("editorial_checks_missing")
        else:
            for check in REQUIRED_STEPS:
                row=checks.get(check)
                if not isinstance(row,dict) or row.get("status") not in ("completed","failed","needs_review"):
                    waiting.append("check_missing:"+check)
                elif row["status"]=="failed":
                    if not isinstance(row.get("findings"),list) or not row["findings"]:
                        waiting.append("check_failed_without_findings:"+check)
                    else:
                        for finding in row["findings"]:
                            if not isinstance(finding,str) or not finding.strip():
                                waiting.append("check_unactionable:"+check)
                            else:
                                revision.append(f"{check}: {finding}")
                elif row["status"]=="needs_review":
                    review.append("Требуется редакторская проверка: "+check)
            if channel=="science" and (
                evidence.get("verified_claim_ids") is None or
                set(evidence.get("verified_claim_ids",[])) != {
                    row.get("id") for row in article.get("claims",[])
                    if isinstance(row,dict)}):
                review.append(REVIEW_REASONS["science_claims_need_review"])
            if channel=="legends" and evidence.get("fiction_originality_reviewed") is not True:
                review.append("Подтвердить оригинальность и художественный характер рассказа.")

    # A previous revision may invalidate every attached receipt; always verify digest.
    if waiting:
        disposition="awaiting_evidence"
        next_stage="complete_github_checks"
    elif revision:
        disposition="needs_revision"
        next_stage="revise_draft_and_rerun_checks"
    elif review:
        disposition="needs_editor_review"
        next_stage="editor_checks"
    else:
        disposition="ready_for_next_stage"
        next_stage="owner_approval_preview"  # Never direct to RSS or Pages.
    return {"schema":"stage-gate-v1","slug":slug,"channel":channel,
            "article_sha256":digest,"disposition":disposition,
            "next_stage":next_stage,
            "revision_actions":sorted(set(revision)),
            "editor_review_actions":sorted(set(review)),
            "awaiting_checks":sorted(set(waiting)),
            "ai_rewrite_threshold_applied":False,
            "publication_authorized":False,"rss_modified":False,
            "notes":["Detector scores do not constitute authorship probabilities.",
                     "A ready verdict continues to an approval preview, not release or RSS."]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--slug",required=True)
    p.add_argument("--evidence-dir",default=str(ROOT/"editorial-stage-evidence"))
    p.add_argument("--ai-report-dir",default=str(ROOT/"editorial-check-reports"))
    p.add_argument("--output-dir",default=str(ROOT/"editorial-stage-results"))
    args=p.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}",args.slug):
        p.error("Invalid slug")
    path=ROOT/"content/articles"/(args.slug+".json")
    if not path.is_file():
        p.error("Article not found")
    article=json.loads(path.read_text("utf-8"))
    if article.get("slug")!=args.slug:
        p.error("Slug mismatch")
    def maybe_json(folder):
        file=Path(folder)/(args.slug+".json")
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
    verdict=stage_decision(article,maybe_json(args.evidence_dir),maybe_json(args.ai_report_dir))
    out=Path(args.output_dir)
    out.mkdir(parents=True,exist_ok=True)
    (out/(args.slug+".json")).write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(verdict,ensure_ascii=False))
    # No article or release settings modified. Nonpassing is a workflow outcome,
    # not a failed/green guess about model performance.

if __name__=="__main__":
    main()
