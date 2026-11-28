"""Length policy shared by drafting, delivery and the existing release gates."""
from html.parser import HTMLParser
import re

from contextlib import contextmanager

# Versioned length policies (non-whitespace characters): (minimum, standard maximum,
# exceptional maximum that needs a recorded length_exception_reason).
# v1: legacy rule; orders created before 2026-11-29 stay on it so their saved,
#     already-paid prompts and checkpoints keep matching.
# v2: owner rule of 2026-11-29: 4000-6000, up to 6500 only when necessary and
#     justified; volume follows the material instead of gravitating to the minimum.
POLICIES = {"v1": (3000, 6000, 7000), "v2": (4000, 6000, 6500)}
DEFAULT_POLICY = "v1"
CURRENT_POLICY = "v2"  # assigned to newly submitted orders

# Legacy constants (v1) for callers that validate historical material.
MIN_CHARS, STANDARD_MAX, HARD_MAX = POLICIES["v1"]

_active = DEFAULT_POLICY


def active_policy():
    return _active


def limits():
    return POLICIES[_active]


@contextmanager
def length_policy(name):
    """Apply one order's pinned length policy for the duration of its pipeline run."""
    global _active
    if name not in POLICIES:
        raise ValueError("unknown_length_policy")
    previous, _active = _active, name
    try:
        yield
    finally:
        _active = previous


def check_length(visible_text, reason=None):
    minimum, standard, hard = limits()
    size = len(re.sub(r"\s", "", visible_text))
    if size < minimum:
        return size, "short_text"
    if size > hard:
        return size, "long_text"
    if size > standard and (not isinstance(reason, str) or not reason.strip()):
        return size, "length_exception_reason_missing"
    return size, None


class VisibleBody(HTMLParser):
    HIDDEN = {"script", "style", "head", "template", "noscript", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def check_html_length(html, reason=None):
    body = VisibleBody()
    body.feed(html)
    body.close()
    return check_length(" ".join(body.text), reason)
