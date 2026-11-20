#!/usr/bin/env python3
"""Edit active-channel drafts and retain a resumable case outside the repository."""

import argparse
from contextlib import ExitStack
from fcntl import LOCK_EX, flock
from html import escape, unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from editorial_ai_check import ROOT, extract_text
from editorial_agent_v1 import ACTIVE, MAX_AGENT, MAX_ASSISTANT, THRESHOLD, article_digest, validate_ai_report
from editorial_quality import MIN_CHARS
from editorial_revision_checks import check_draft


DUPLICATE_COMMA = re.compile(r"(?<=[а-яё]),,(?=[ \t]+[а-яё])", re.IGNORECASE)


class PlainParagraphs(HTMLParser):
    """Locate editable paragraph spans without reserializing the original HTML."""

    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.line_offsets = [0, *(match.end() for match in re.finditer("\n", html))]
        self.paragraphs = []
        self.start = None
        self.quoted = 0
        self.feed(html)
        self.close()

    def html_offset(self):
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        self.start = None
        if tag == "blockquote":
            self.quoted += 1
        if tag == "p" and not attrs and not self.quoted and self.get_starttag_text().lower() == "<p>":
            self.start = self.html_offset()

    def handle_endtag(self, tag):
        if tag == "p" and self.start is not None:
            stop = self.html_offset()
            end = stop + len("</p>")
            fragment = self.html[self.start + len("<p>"):stop]
            if self.html[stop:end].lower() == "</p>" and "<" not in fragment:
                self.paragraphs.append((self.start, end, fragment))
        if tag == "blockquote":
            self.quoted = max(0, self.quoted - 1)
        self.start = None

    def handle_startendtag(self, tag, attrs):
        self.start = None


def automatic_edit(article, attempted):
    html = article["body_html"]
    paragraphs = PlainParagraphs(html).paragraphs
    if "duplicate_paragraph" not in attempted:
        edits = []
        for (start, end, fragment), (_, previous_end, previous) in zip(paragraphs[1:], paragraphs):
            if (fragment == previous and len(unescape(fragment).strip()) >= 40
                    and not html[previous_end:start].strip()
                    and not fragment.lstrip().startswith(("«", "–", "—", '"'))):
                edits.append((start, end, ""))
        if edits:
            candidate = {**article, "body_html": replace_spans(html, edits)}
            chars = len(re.sub(r"\s", "", extract_text(candidate["body_html"])))
            if chars >= MIN_CHARS:
                return candidate, "duplicate_paragraph", f"Удалено соседних дословных повторов абзацев: {len(edits)}."
    if "duplicate_comma" not in attempted:
        edits = [(start + 3, end - 4, DUPLICATE_COMMA.sub(",", fragment))
                 for start, end, fragment in paragraphs if DUPLICATE_COMMA.search(fragment)]
        if edits:
            return ({**article, "body_html": replace_spans(html, edits)}, "duplicate_comma",
                    f"Исправлены двойные запятые между словами в абзацах: {len(edits)}.")
    return None


def replace_spans(html, edits):
    for start, end, replacement in sorted(edits, reverse=True):
        html = html[:start] + replacement + html[end:]
    return html


def assistant_edit(article, response):
    if response.get("base_text_sha256") != article_digest(article):
        raise ValueError("Assistant response does not match the current text SHA-256")
    if response.get("decision") == "no_revision":
        if response.get("edits") or not isinstance(response.get("reason"), str) or not response["reason"].strip():
            raise ValueError("A declined revision requires a reason and no edits")
        return None, [response["reason"]]
    edits = response.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("Assistant response requires paragraph edits")
    html = article["body_html"]
    paragraphs = PlainParagraphs(html).paragraphs
    changes, spans, used = [], [], set()
    for edit in edits:
        number = edit["paragraph"]
        if type(number) is not int or not 1 <= number <= len(paragraphs) or number in used:
            raise ValueError("Invalid or repeated editable paragraph number")
        start, end, fragment = paragraphs[number - 1]
        before, after, reason = edit["before"], edit["after"], edit["reason"]
        if before != unescape(fragment) or not all(isinstance(s, str) and s.strip() for s in (after, reason)):
            raise ValueError("Assistant edit must identify the exact paragraph and explain the change")
        if before.strip() == after.strip():
            raise ValueError("Assistant edit must change the text")
        used.add(number)
        spans.append((start + 3, end - 4, escape(after, quote=False)))
        changes.append(reason)
    return {**article, "body_html": replace_spans(html, spans)}, changes


def append_version(case, article, stage, changes, rule=None):
    digest = article_digest(article)
    previous = case["versions"][-1]
    if any(version["text_sha256"] == digest for version in case["versions"]):
        raise ValueError("Revision would repeat a previous text")
    case["versions"].append({"article": article, "stage": stage,
                             "before_text_sha256": previous["text_sha256"], "text_sha256": digest,
                             "changes": changes, "rule": rule})
    case["handoff"] = None
    case["decision"] = "checks_pending"


def handoff(case, recipient, reasons):
    version = case["versions"][-1]
    case["decision"] = "assistant_handoff_pending" if recipient == "chatgpt" else "owner_review_pending"
    case["handoff"] = {
        "recipient": recipient, "transport_status": "not_connected",
        "text_sha256": version["text_sha256"], "reasons": reasons,
    }
    if recipient == "chatgpt":
        case["handoff"]["editable_paragraphs"] = [
            {"paragraph": i, "before": unescape(fragment)}
            for i, (_, _, fragment) in enumerate(PlainParagraphs(version["article"]["body_html"]).paragraphs, 1)
        ]


def run_case(case, check, save, response=None):
    versions = case["versions"]
    if response is not None:
        if case.get("decision") != "assistant_handoff_pending":
            raise ValueError("There is no pending ChatGPT handoff")
        if sum(v["stage"] == "assistant" for v in versions) >= MAX_ASSISTANT:
            raise ValueError("ChatGPT revision limit exceeded")
        candidate, changes = assistant_edit(versions[-1]["article"], response)
        if candidate is None:
            case["assistant_decline"] = changes[0]
            handoff(case, "owner", ["assistant_declined", *case["handoff"]["reasons"]])
            save(case)
            return
        append_version(case, candidate, "assistant", changes)
        save(case)
    elif case.get("decision") in {"assistant_handoff_pending", "owner_review_pending"}:
        return
    while True:
        version = versions[-1]
        article = version["article"]
        report = check(article)
        version["report"] = report
        case["decision"] = "checks_pending"
        ai_index = validate_ai_report(article, report.get("ai"))
        if (report.get("text_sha256") != version["text_sha256"]
                or report.get("automated_checks_completed") is not True or ai_index is None):
            save(case)
            return
        version["index"] = ai_index
        version["model_a_review_triggered"] = ai_index > THRESHOLD
        reasons = list(report["findings"])
        if report["grammar"]["issues"]:
            reasons.append("grammar_review")
        if report["vale"]["issues"]:
            reasons.append("style_review")
        if ai_index > THRESHOLD:
            reasons.append("model_a_editorial_review")
        if len(versions) > 1:
            previous = versions[-2]
            new_findings = sorted(set(report["findings"]) - set(previous["report"]["findings"]))
            if new_findings:
                version["new_findings"] = new_findings
                case["recommended_text_sha256"] = previous["text_sha256"]
                reasons.append("revision_introduced_new_findings")
        agent_versions = [v for v in versions if v["stage"] == "agent"]
        attempted = {v["rule"] for v in agent_versions}
        edit = automatic_edit(article, attempted)
        if version["stage"] != "assistant" and len(agent_versions) < MAX_AGENT and edit:
            candidate, rule, change = edit
            append_version(case, candidate, "agent", [change], rule)
            save(case)
            continue
        unresolved_text = bool(edit) or bool({
            "short_article", "fiction_claim_of_reality_review", "similar_article",
            "grammar_review", "style_review", "revision_introduced_new_findings",
        }.intersection(reasons))
        if version["stage"] != "assistant" and unresolved_text:
            handoff(case, "chatgpt", ["local_edits_exhausted", *reasons])
        else:
            handoff(case, "owner", [*reasons, *report["manual_checks_pending"]])
        save(case)
        return


def save_case(path, case):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(case, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--article", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--assistant-response", type=Path)
    args = parser.parse_args()
    try:
        article = json.loads(args.article.read_text(encoding="utf-8"))
        if article.get("channel") not in ACTIVE:
            print(json.dumps({"decision": "paused_channel"}))
            return
        if article.get("status") != "draft" or article.get("publication_authorized") is True:
            raise ValueError("Only drafts without publication authorization can be edited")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", article.get("slug", "")):
            raise ValueError("Invalid article slug")
        directory = args.case_dir.resolve()
        if directory.is_relative_to(ROOT):
            raise ValueError("The case directory must be outside the repository")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / ".lock").open("a") as lock, ExitStack() as stack:
            flock(lock, LOCK_EX)
            path = directory / "case.json"
            if path.exists():
                case = json.loads(path.read_text(encoding="utf-8"))
                if case["versions"][0]["article"] != article:
                    raise ValueError("Case directory belongs to another original draft")
            else:
                case = {"schema": "zen-editorial-revision-case-v1", "decision": "checks_pending",
                        "publication_authorized": False, "rss_delivery_authorized": False,
                        "handoff": None, "versions": [{"article": article, "stage": "original",
                                                      "text_sha256": article_digest(article)}]}
            response = (json.loads(args.assistant_response.read_text(encoding="utf-8"))
                        if args.assistant_response else None)
            tool = None

            def grammar(text):
                nonlocal tool
                if tool is None:
                    import language_tool_python
                    tool = stack.enter_context(language_tool_python.LanguageTool("ru"))
                return tool.check(text)

            run_case(case, lambda draft: check_draft(draft, grammar), lambda data: save_case(path, data), response)
            print(json.dumps({"decision": case["decision"], "case": str(path)}))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
