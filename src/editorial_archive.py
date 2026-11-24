"""Read-only index of source articles and legacy private editorial checkpoints."""
from .editorial_ai_check import CONTENT, ROOT, extract_text
from .editorial_memory import read_json
from .editorial_no_repeat import words

CHANNELS = ("legends", "science", "cinema")


def archive(root, exclude_day=None):
    titles = {c: [] for c in CHANNELS}
    prose = []
    paths = set()
    for path in sorted(CONTENT.glob("*.json")):
        row = read_json(path)
        title = row.get("title", "")
        if row.get("channel") in titles:
            titles[row["channel"]].append(title)
        prose.append((title, words(extract_text(row.get("body_html", "")))))
        for name in row.get("image_files", []):
            asset = ROOT / name
            if asset.is_file() and asset.resolve().is_relative_to(ROOT / "content"):
                paths.add(asset)
    for path in sorted(root.glob("????-??-??/state.json")):
        if path.parent.name == exclude_day:
            continue
        row = read_json(path)
        for article_id, entry in row["articles"].items():
            if title := entry.get("title"):
                titles[article_id.split("-")[0]].append(title)
                if entry.get("text_sha256"):
                    source = path.parent / article_id / "versions" / (entry["text_sha256"] + ".txt")
                    if source.exists():
                        prose.append((title, words(source.read_text(encoding="utf-8"))))
    return titles, prose, paths
