#!/usr/bin/env python3
"""Verify built RSS feeds for each channel; never populate or publish a feed."""
from pathlib import Path
from xml.etree import ElementTree as ET
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
FEEDS = {"cinema", "legends", "science"}


def validate():
    result = {}
    for channel in sorted(FEEDS):
        path = SITE / "feeds" / (channel + ".xml")
        if not path.is_file():
            result[channel] = {"valid": False, "reason": "missing_feed"}
            continue
        try:
            root = ET.parse(path).getroot()
            target = root.find("channel")
            if root.tag != "rss" or target is None or not target.findtext("title"):
                raise ValueError("Not a valid RSS 2.0 channel")
            items = target.findall("item")
            for item in items:
                if not all(item.findtext(name) for name in ("title", "link", "guid", "pubDate")):
                    raise ValueError("Incomplete RSS item")
            result[channel] = {"valid": True, "item_count": len(items)}
        except (ET.ParseError, OSError, ValueError) as exc:
            result[channel] = {"valid": False, "reason": str(exc)}
    return result


if __name__ == "__main__":
    checks = validate()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if not all(row["valid"] for row in checks.values()):
        sys.exit(1)
