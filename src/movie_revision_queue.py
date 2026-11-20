#!/usr/bin/env python3
"""Produce an editorial correction queue from an actual Movie Fact Check report.

Only source-backed corrections are eligible. Semantic similarity is *never*
enough to rewrite a movie scene. Never changes article text or release flags.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from editorial_ai_check import CONTENT, ROOT

ISSUE_ADVICE = {
    "file_missing": "Восстановить оригинальный файл кадра или подобрать другой с проверенными правами",
    "source_url_missing": "Добавить проверяемый источник изображения",
    "not_used_in_article": "Уточнить расположение кадра в статье либо убрать лишний файл из списка",
    "imdb_title_id_conflict": "Заменить изображение на кадр из нужного фильма только после визуальной сверки",
    "source_caption_differs_from_article": "Сверить описание кадра и привести подписи к одному подтверждённому варианту",
}
ROOT_REPORT = ROOT / "editorial-movie-reports"


def build_queue(article, report):
    items = []
    semantic = report.get("semantic_plot_search", {})
    semantic_by_section = {row.get("section"): row for row in semantic.get("matches", [])}
    for row in report.get("article_sections", []):
        if row.get("status") not in ("requires_editor_review", "plot_section_unavailable", "source_unavailable"):
            continue
        section = row.get("section")
        candidate = semantic_by_section.get(section, {}).get("candidate_matches", [])[:2]
        items.append({
            "kind": "plot_claim",
            "section": section,
            "status": "review_required",
            "evidence_source": row.get("source_url") or row.get("source"),
            "suggested_rewrite": None,
            "proposed_action": (
                "Сверить конкретное утверждение с указанным источником; если есть прямое противоречие, "
                "исправить факт, сохранив авторскую интонацию и смысл. "
                "При отсутствии доказательств оставить статус неподтверждённым."
            ),
            "reference_candidates": candidate,
            "confidence": "not_established",
        })
    for image in report.get("stills", []):
        for warning in image.get("warnings", []):
            items.append({
                "kind": "image",
                "file": image.get("file"),
                "section": image.get("article_section"),
                "warning": warning,
                "status": "review_required",
                "evidence_source": image.get("source_url"),
                "suggested_rewrite": None,
                "proposed_action": ISSUE_ADVICE.get(warning, "Ручная сверка исходного кадра"),
                "confidence": "not_established",
            })
    for image in report.get("stills", []):
        if image.get("visual_scene_match") != "verified":
            items.append({
                "kind": "image_scene",
                "file": image.get("file"),
                "section": image.get("article_section"),
                "status": "unverified_not_error",
                "evidence_source": image.get("source_url"),
                "suggested_rewrite": None,
                "proposed_action": "Визуально сопоставить конкретный кадр с описанием сцены; отсутствие проверки не означает ошибку.",
            })
    return {
        "schema": "movie-correction-queue-v1",
        "article_slug": report.get("article_slug"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_report": report.get("source_file"),
        "article_title": article.get("title"),
        "items": items,
        "counts": {
            "review_required": sum(x["status"] == "review_required" for x in items),
            "unverified_not_error": sum(x["status"] == "unverified_not_error" for x in items),
            "automatically_rewritten": 0,
            "verified_contradictions": 0,
        },
        "publication_authorized": False,
        "notice": ("No exact plot contradiction has been established by lexical or embedding search. "
                   "Never silently change events to match a top-ranked snippet."),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--report-dir", default=str(ROOT_REPORT))
    parser.add_argument("--output", default=str(ROOT_REPORT))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.slug):
        parser.error("Unsafe slug")
    original = CONTENT / (args.slug + ".json")
    reviewed = Path(args.report_dir) / (args.slug + ".json")
    if not original.is_file() or not reviewed.is_file():
        parser.error("Missing original article or actual diagnostic report")
    article = json.loads(original.read_text(encoding="utf-8"))
    report = json.loads(reviewed.read_text(encoding="utf-8"))
    if article.get("channel") != "cinema" or report.get("article_slug") != args.slug:
        parser.error("Article/report mismatch")
    result = build_queue(article, report)
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / (args.slug + "-corrections.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"slug": args.slug, **result["counts"],
                      "publication_authorized": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
