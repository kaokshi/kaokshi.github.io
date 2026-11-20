"""Visible article-body text and the shared non-whitespace character standard."""

from html.parser import HTMLParser
import json
from pathlib import Path
import re

_policy = Path(__file__).resolve().parents[1] / "config" / "ai_revision_policy.json"
_bounds = json.loads(_policy.read_text(encoding="utf-8"))["article_length"]
MIN_CHARS = _bounds["min_non_whitespace_chars"]
MAX_CHARS = _bounds["max_non_whitespace_chars"]

HIDDEN = {"head", "script", "style", "template", "noscript"}
BLOCKS = {"p", "div", "br", "h1", "h2", "h3", "li"}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN:
            self.hidden.append(tag)
        elif not self.hidden and tag in BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag in BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()


def extract_text(html):
    parser = TextExtractor()
    parser.feed(html)
    parser.close()
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def count_characters(body_html):
    return sum(not ch.isspace() for ch in extract_text(body_html))


def length_finding(count):
    if count < MIN_CHARS:
        return "short_article"
    if count > MAX_CHARS:
        return "long_article"
    return None


def length_distance(count):
    return max(MIN_CHARS - count, count - MAX_CHARS, 0)


def preserves_length(before, after):
    return length_distance(count_characters(after)) <= length_distance(count_characters(before))
