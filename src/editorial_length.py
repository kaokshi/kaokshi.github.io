"""Length policy shared by drafting, delivery and the existing release gates."""
from html.parser import HTMLParser
import re

MIN_CHARS = 3000
STANDARD_MAX = 6000
HARD_MAX = 7000


def check_length(visible_text, reason=None):
    size = len(re.sub(r"\s", "", visible_text))
    if size < MIN_CHARS:
        return size, "short_text"
    if size > HARD_MAX:
        return size, "long_text"
    if size > STANDARD_MAX and (not isinstance(reason, str) or not reason.strip()):
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
