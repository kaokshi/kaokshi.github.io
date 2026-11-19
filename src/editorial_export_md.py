#!/usr/bin/env python3
"""Create temporary text copies for style linting, without changing original drafts."""
import json
from pathlib import Path
import re
from editorial_ai_check import CONTENT, extract_text

output = Path("editorial-quality-reports/vale-input")
output.mkdir(parents=True, exist_ok=True)
for path in sorted(CONTENT.glob("*.json")):
    article = json.loads(path.read_text(encoding="utf-8"))
    if article.get("channel") not in {"cinema", "legends", "science"}:
        continue
    text = extract_text(article.get("body_html", ""))
    dest = output / (re.sub(r"[^a-z0-9-]", "", path.stem) + ".md")
    dest.write_text("# " + article.get("title", "") + "\n\n" + text + "\n", encoding="utf-8")
print("Exported", len(list(output.glob("*.md"))), "drafts for Vale")
