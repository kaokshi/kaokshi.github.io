#!/usr/bin/env python3
"""Conservative automatic editorial routing for active Zen channels.

The aggregate score is a *workflow triage index*, not a calibrated probability
of AI authorship or scientific correctness. Hard blockers always override it.
No files are modified by the decision engine.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from editorial_ai_check import ROOT, CONTENT
from editorial_layout import layout_findings
from editorial_length import count_characters, length_finding

ACTIVE_CHANNELS = frozenset({"legends", "science"})
PAUSED_CHANNELS = frozenset({"cinema"})
RISK_THRESHOLD = 20  # 0-100 editorial triage points; >20 -> human review.
PRIMARY_AI_LIMIT = None  # No unvalidated AI-authorhip threshold; keep diagnostics only.
REQUIRED_AI = frozenset({"modernbert", "lightgbm", "transformer", "ai_accent_detector"})
REQUIRED_QUALITY = frozenset({"language_tool", "vale", "links", "image_similarity", "rss_preview"})
ALLOWED_RIGHTS = frozenset({"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "PUBLIC_DOMAIN", "OWN_WORK", "LICENSED"})
LEGEND_SAFETY = frozenset({"original_fiction_confirmed", "fiction_not_presented_as_fact"})
SCIENTIFIC_SAFETY = frozenset({"claim_sources_checked", "no_unresolved_factual_contradictions"})


def digest_article(article):
    """Release digest includes content, images and provenance, not mutable flags."""
    fields = {name: article.get(name) for name in (
        "channel", "slug", "title", "body_html", "image_files", "image_sources",
        "rights_basis", "claims", "source_docx", "cover_selection")}
    fields["image_content_sha256"] = {
        name: (hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
               if isinstance(name, str) and (ROOT / name).is_file() else "MISSING")
        for name in (article.get("image_files") or []) if isinstance(name, str)
    }
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def finite_percent(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= 100 and value == value and value != float("inf"))


def valid_evidence(report, digest):
    return (isinstance(report, dict)
            and report.get("status") == "completed"
            and report.get("article_sha256") == digest
            and isinstance(report.get("report_ref"), str)
            and bool(report["report_ref"].strip()))


def decide(article, evidence):
    """Fail closed; route to 'approval' unless everything necessary is evidenced."""
    blockers = layout_findings(article, ROOT)
    slug = article.get("slug")
    channel = article.get("channel")
    if channel not in ACTIVE_CHANNELS:
        blockers.append("channel_paused_or_unknown")
    if article.get("auto_release_candidate") is not True:
        blockers.append("not_a_new_auto_release_candidate")
    if article.get("release_hold") is True or article.get("status") in {"rejected", "published"}:
        blockers.append("explicit_release_hold_or_terminal_status")
    issue = length_finding(count_characters(article.get("body_html", "")))
    if issue:
        blockers.append("short_text" if issue == "short_article" else "long_text")
    imgs = article.get("image_files") or []
    if len(imgs) < 7 or len(set(imgs)) != len(imgs):
        blockers.append("images_count_or_duplicate")
    for name in imgs:
        if (not isinstance(name, str) or not name.startswith("content/assets/")
                or Path(name).is_absolute() or ".." in Path(name).parts
                or not (ROOT / name).is_file()):
            blockers.append("image_missing_or_unsafe")
            break
    src = {x.get("file"): x for x in article.get("image_sources", [])
           if isinstance(x, dict) and isinstance(x.get("file"), str)}
    if not article.get("rights_basis") or len(imgs) != len(src):
        blockers.append("rights_evidence_missing")
    for name in imgs:
        item = src.get(name, {})
        if (item.get("license") not in ALLOWED_RIGHTS or
                not isinstance(item.get("source_page"), str) or
                not item["source_page"].startswith("https://") or
                item.get("license_verified") is not True):
            blockers.append("image_license_unverified")
            break
    dg = digest_article(article)
    ai = evidence.get("ai")
    if not valid_evidence(ai, dg):
        blockers.append("ai_report_missing_or_stale")
        ai_checks = {}
    else:
        ai_checks = ai.get("checks") or {}
        if (not isinstance(ai_checks, dict) or
                not REQUIRED_AI.issubset(ai_checks) or
                any(ai_checks[p].get("status") != "completed" for p in REQUIRED_AI)):
            blockers.append("ai_check_incomplete")
    # Model A score requires three actual, complete classifier values.
    component_scores = []
    for model in ("modernbert", "lightgbm", "transformer"):
        item = ai_checks.get(model, {}) if isinstance(ai_checks, dict) else {}
        value = item.get("score_percent") if isinstance(item, dict) else None
        if not finite_percent(value):
            blockers.append("model_a_component_missing")
        else:
            component_scores.append(value)
    model_a_index = round(sum(component_scores) / 3, 2) if len(component_scores) == 3 else None
    if model_a_index is not None and model_a_index > 80:
        blockers.append("model_a_editorial_review_above_80")
    if evidence.get("ai_editorial_escalation_unresolved") is True:
        blockers.append("unresolved_ai_editorial_escalation")
    quality = evidence.get("quality")
    if not valid_evidence(quality, dg):
        blockers.append("quality_report_missing_or_stale")
        tests = {}
    else:
        tests = quality.get("checks") or {}
        if (not isinstance(tests, dict) or not REQUIRED_QUALITY.issubset(tests)
                or any(not isinstance(tests[p], dict) or tests[p].get("status") != "completed" for p in REQUIRED_QUALITY)):
            blockers.append("quality_check_incomplete")
    facts = evidence.get("factual")
    if not valid_evidence(facts, dg):
        blockers.append("factual_report_missing_or_stale")
    else:
        required = LEGEND_SAFETY if channel == "legends" else SCIENTIFIC_SAFETY
        if any(facts.get(flag) is not True for flag in required):
            blockers.append("fiction_or_science_not_verified")
        if channel == "science":
            claims = article.get("claims") or []
            checked = facts.get("verified_claim_ids") or []
            if (not claims or not isinstance(claims, list) or
                    any(not isinstance(x, dict) or not x.get("id") or not x.get("source_urls") for x in claims)
                    or any(not isinstance(x.get("source_urls"), list) for x in claims)
                    or set(x["id"] for x in claims) != set(checked)):
                blockers.append("science_claim_coverage_missing")
    if evidence.get("unresolved_critical_findings"):
        blockers.append("unresolved_critical_findings")
    # The composite index is a conservative workflow triage heuristic,
    # NOT a probabilistic blend of incompatible authorship detectors.
    # AI detector scores have no independently validated rewrite thresholds.
    # Editorial quality warnings are kept separately; never mix them into AI probability.
    style = evidence.get("style_warning_count")
    image_warn = evidence.get("image_warning_count")
    source_warn = evidence.get("source_warning_count")
    valid_counts = all(isinstance(x, int) and not isinstance(x, bool) and x >= 0
                       for x in (style, image_warn, source_warn))
    if not valid_counts:
        blockers.append("warning_counts_missing_or_invalid")
        style = image_warn = source_warn = 0
    comparison = {}
    for name in ("modernbert", "lightgbm"):
        check = ai_checks.get(name, {}) if isinstance(ai_checks, dict) else {}
        val = check.get("score_percent") if isinstance(check, dict) else None
        if not finite_percent(val):
            blockers.append("comparison_score_missing")
        else:
            comparison[name] = val
    if len(comparison) == 2:
        difference = abs(comparison["modernbert"] - comparison["lightgbm"])
        disagreement_points = 10 if difference >= 50 else 5 if difference >= 25 else 0
    else:
        disagreement_points = 0
    components = {
        "model_a_review_signal": 0,  # handled as separate owner-selected review flag
        "model_disagreement_advisory": 0,  # not independent evidence of AI
        "style_warnings": min(25, style * 3),
        "image_warnings": min(25, image_warn * 8),
        "source_warnings": min(25, source_warn * 8),
    }
    # No calibrated AI threshold exists yet; a score-only decision cannot
    # authorize unattended publication, even when all quality counts are zero.
    blockers.append("ai_auto_release_threshold_not_validated")
    risk = (round(sum(components.values()), 2)
            if all(isinstance(v, (int, float)) for v in components.values()) else None)
    if risk is not None and risk > RISK_THRESHOLD:
        blockers.append("aggregate_risk_above_20")
    disposition = "automatic_release_candidate" if not blockers else "send_for_owner_review"
    return {
        "schema": "editorial-auto-route-v1", "slug": slug,
        "channel": channel, "article_sha256": dg,
        "risk_score": risk, "risk_components": components, "risk_scale": "0-100", "review_if_above": RISK_THRESHOLD,
        "model_a_index_0_100": model_a_index,
        "model_a_editorial_review_if_above": 80,
        "ai_rewrite_required_by_detector": False,
        "ai_rewrite_threshold": None,
        "disposition": disposition,
        "blockers": sorted(set(blockers)),
        "publication_authorized": False,
        "notes": [
            "A low risk score is NOT scientific fact-checking, legal rights verification, or proof of human authorship.",
            "Model A >80 routes to editorial review; only verified substantive defects justify actual rewrites.",
            "This module routes articles; a separate verified release workflow must check the decision before deployment.",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--evidence-dir", default=str(ROOT / "editorial-release-evidence"))
    ap.add_argument("--output", default=str(ROOT / "editorial-routing-reports"))
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.slug):
        ap.error("Invalid slug")
    path = CONTENT / (args.slug + ".json")
    if not path.is_file():
        ap.error("Article missing")
    article = json.loads(path.read_text(encoding="utf-8"))
    if article.get("slug") != args.slug:
        ap.error("Slug mismatch")
    evidence_path = Path(args.evidence_dir) / (args.slug + ".json")
    evidence = json.loads(evidence_path.read_text("utf-8")) if evidence_path.is_file() else {}
    decision = decide(article, evidence)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / (args.slug + ".json")).write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"slug": args.slug, "disposition": decision["disposition"],
                      "risk": decision["risk_score"], "blockers": decision["blockers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
