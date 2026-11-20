#!/usr/bin/env python3
"""Read-only, local SlopTotal diagnostic for three existing editorial articles.

Calls only an explicitly local SlopTotal instance; no public SlopTotal service.
AI scores are uncalibrated external diagnostics, not evidence of authorship.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SLUGS = (
    "legends-01-prohodnaya-17",
    "science-08-sinie-ogni-idzena",
    "cinema-01-truman-show-real-photos",
)

class Extract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
    def handle_starttag(self, tag, attrs):
        if tag in ("p", "h1", "h2", "h3", "li", "br"):
            self.items.append("\n")
    def handle_data(self, data):
        self.items.append(data)

def plain_text(markup):
    parser = Extract()
    parser.feed(markup)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.items)).strip()

def check_local_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != 8000 or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("SlopTotal URL must be local http://127.0.0.1:8000")
    return "http://127.0.0.1:8000"

def analyze(text, url, timeout=600, transport=urlopen):
    base = check_local_url(url)
    req = Request(base + "/api/analyze", data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with transport(req, timeout=timeout) as resp:
        return json.load(resp)

def compact_result(payload):
    # Fail closed if the actual structured result cannot be found.
    score = payload.get("overall_score")
    verdict = payload.get("overall_verdict")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
        raise ValueError("Missing or invalid overall_score: not a successful comparison")
    if not isinstance(verdict, str) or not verdict.strip():
        raise ValueError("Missing overall_verdict")
    # No excerpt, input text, or server-stored URL is included in our artifact.
    engines = payload.get("engines", payload.get("results"))
    return {"overall_score_0_100": score, "overall_verdict": verdict,
            "engines_count": len(engines) if isinstance(engines, (dict, list)) else None}

def run(slug, url, out):
    if slug not in SLUGS:
        raise ValueError("Only three frozen comparison slugs are permitted")
    article_path = ROOT / "content" / "articles" / (slug + ".json")
    article = json.loads(article_path.read_text(encoding="utf-8"))
    text = plain_text(article["body_html"])
    result = compact_result(analyze(text, url))
    item = {"schema": "sloptotal-local-experiment-v1",
            "article_slug": slug, "channel": article["channel"],
            "characters_including_spaces": len(text),
            "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "self_hosted": True, "source": "local_sloptotal",
            "diagnostic": result,
            "provenance_verified": False,
            "publication_authorized": False,
            "note": "Do not compare as calibrated AI probabilities or use as verified authorship."}
    out.mkdir(parents=True, exist_ok=True)
    (out / (slug + ".json")).write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return item

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--output", default="sloptotal-experiment-reports")
    args = p.parse_args()
    out = Path(args.output)
    failures = []
    summary = []
    for slug in SLUGS:
        try:
            item = run(slug, args.url, out)
            summary.append({"slug": slug, "score": item["diagnostic"]["overall_score_0_100"],
                            "verdict": item["diagnostic"]["overall_verdict"]})
            print(json.dumps(summary[-1], ensure_ascii=False))
        except Exception as e:
            failures.append({"slug": slug, "error_type": type(e).__name__})
            print(json.dumps(failures[-1], ensure_ascii=False))
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps({"status": "completed" if not failures else "incomplete",
      "results": summary, "failures": failures, "publication_authorized": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failures:
        raise SystemExit("SlopTotal comparison incomplete; do not claim scores for failed articles.")

if __name__ == "__main__":
    main()
