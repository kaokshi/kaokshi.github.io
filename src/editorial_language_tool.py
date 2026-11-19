#!/usr/bin/env python3
"""Check requested articles with one local LanguageTool server, preserving failures."""
import argparse
import json
from pathlib import Path
import re
from editorial_ai_check import CONTENT, extract_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", required=True, help="May be repeated")
    ap.add_argument("--output", default="editorial-quality-reports/languagetool.json")
    args = ap.parse_args()
    reports = []
    documents = []
    for slug in dict.fromkeys(args.slug):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", slug):
            ap.error("Unsafe article slug")
        path = CONTENT / (slug + ".json")
        if not path.is_file():
            ap.error("Unknown article " + slug)
        a = json.loads(path.read_text(encoding="utf-8"))
        documents.append((slug, extract_text(a.get("body_html", ""))))
    try:
        import language_tool_python
        with language_tool_python.LanguageTool("ru") as tool:
            for slug, text in documents:
                try:
                    matches = tool.check(text[:12000])
                    reports.append({
                        "tool": "LanguageTool local Russian", "slug": slug,
                        "status": "completed",
                        "issue_count": len(matches),
                        "issues": [{
                            "rule_id": getattr(m, "rule_id", None),
                            "message": getattr(m, "message", "")[:200],
                            "offset": m.offset,
                            "length": m.error_length,
                            "replacements": list(m.replacements)[:3],
                        } for m in matches[:100]],
                        "notice": "Suggestions require editorial review, not proof of errors.",
                    })
                except Exception as exc:
                    reports.append({"tool": "LanguageTool local Russian", "slug": slug,
                                    "status": "unavailable", "error": str(exc)[:450]})
    except Exception as exc:
        reports = [{"tool": "LanguageTool local Russian", "slug": slug,
                    "status": "unavailable", "error": str(exc)[:450]} for slug, _ in documents]
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"articles": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for r in reports:
        print(f'LanguageTool {r["slug"]}: {r["status"]}, issues={r.get("issue_count", "N/A")}')
    if any(x["status"] != "completed" for x in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
