#!/usr/bin/env python3
"""Optional language editing integration. Failing LanguageTool is reported, not masked."""
import argparse
import json
from pathlib import Path
from editorial_ai_check import CONTENT, extract_text


def serialize_match(m):
    """Accept current and legacy LanguageTool Match versions; never hide failures."""
    length = getattr(m, "error_length", None)
    if length is None:
        length = getattr(m, "length", None)
    if length is None:
        length = getattr(m, "errorLength", None)
    if length is None:
        raw = getattr(m, "__dict__", {})
        if isinstance(raw, dict):
            length = raw.get("errorLength", raw.get("error_length", raw.get("length")))
    offset = getattr(m, "offset", None)
    if not isinstance(length, int) or length < 0 or not isinstance(offset, int) or offset < 0:
        raise ValueError("LanguageTool Match offset/length is unavailable; attributes="+str(sorted(vars(m)))[:240])
    return {
        "rule_id": getattr(m, "rule_id", None),
        "message": str(getattr(m, "message", ""))[:200],
        "offset": offset,
        "length": length,
        "replacements": list(getattr(m, "replacements", []))[:3],
    }


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
            serialized = [serialize_match(m) for m in checks[:100]]
            report["issues"] = serialized
            report["issue_count"] = len(checks)
            report["status"] = "completed"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)[:450]
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f'LanguageTool status={report["status"]}, suggestions={len(report["issues"])}')
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
