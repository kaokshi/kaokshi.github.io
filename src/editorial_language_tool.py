#!/usr/bin/env python3
"""Optional language editing integration. Failing LanguageTool is reported, not masked."""
import argparse
import json
from pathlib import Path
from editorial_ai_check import CONTENT, extract_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--output", default="editorial-quality-reports/languagetool.json")
    args = ap.parse_args()
    path = CONTENT / (args.slug + ".json")
    if not path.is_file():
        ap.error("article not found")
    a = json.loads(path.read_text(encoding="utf-8"))
    text = extract_text(a.get("body_html", ""))
    report = {"tool": "LanguageTool local Russian", "slug": args.slug,
              "status": "unavailable", "issues": [], "notice": "Suggestions require editorial review"}
    try:
        import language_tool_python
        with language_tool_python.LanguageTool("ru") as tool:
            # Restrict diagnostic input to avoid lengthy downloads/checks on all historic drafts.
            checks = tool.check(text[:12000])
            report["status"] = "completed"
            report["issues"] = [{
                "rule_id": getattr(m, "rule_id", None),
                "message": getattr(m, "message", "")[:200],
                "offset": m.offset,
                "length": m.error_length,
                "replacements": list(m.replacements)[:3],
            } for m in checks[:100]]
            report["issue_count"] = len(checks)
    except Exception as exc:
        report["error"] = str(exc)[:450]
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f'LanguageTool status={report["status"]}, suggestions={len(report["issues"])}')
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
