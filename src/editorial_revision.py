#!/usr/bin/env python3
"""Edit active-channel drafts and retain a resumable case outside the repository."""

import argparse
from contextlib import ExitStack
from fcntl import LOCK_EX, flock
from html import escape, unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from editorial_ai_check import ROOT
from editorial_agent_v1 import ACTIVE, MAX_AGENT, MAX_ASSISTANT, THRESHOLD, article_digest, validate_ai_report
from editorial_length import preserves_length
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
            if preserves_length(html, candidate["body_html"]):
                return candidate, "duplicate_paragraph", f"Удалено соседних дословных повторов абзацев: {len(edits)}."
    if "duplicate_comma" not in attempted:
        edits = [(start + 3, end - 4, DUPLICATE_COMMA.sub(",", fragment))
                 for start, end, fragment in paragraphs if DUPLICATE_COMMA.search(fragment)]
        if edits:
            candidate = {**article, "body_html": replace_spans(html, edits)}
            if preserves_length(html, candidate["body_html"]):
                return candidate, "duplicate_comma", f"Исправлены двойные запятые между словами: {len(edits)}."
    return None


def replace_spans(html, edits):
    for start, end, replacement in sorted(edits, reverse=True):
        html = html[:start] + replacement + html[end:]
    return html


def assistant_edit(article, response, *, allow_remove=False):
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
        remove = allow_remove and edit.get("remove") is True
        if (before != unescape(fragment) or not isinstance(reason, str) or not reason.strip()
                or not isinstance(after, str) or (remove and after != "") or (not remove and not after.strip())):
            raise ValueError("Edit must identify the exact paragraph and explain the change")
        if before.strip() == after.strip():
            raise ValueError("Assistant edit must change the text")
        used.add(number)
        spans.append((start, end, "") if remove else (start + 3, end - 4, escape(after, quote=False)))
        changes.append(reason)
    candidate = {**article, "body_html": replace_spans(html, spans)}
    if not preserves_length(html, candidate["body_html"]):
        raise ValueError("The edit would move the article farther from the permitted length")
    return candidate, changes


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


def run_case(case, check, save, response=None, *, semantic_editor=None):
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
        version["model_a_threshold_advisory_only"] = True
        previous_index = versions[-2].get("index") if len(versions) > 1 else None
        if isinstance(previous_index, (int, float)):
            delta = ai_index - previous_index
            version["model_a_delta_from_previous"] = delta
            version["model_a_trend"] = ("increased" if delta > 1e-8
                                         else "decreased" if delta < -1e-8 else "plateau")
            if version["stage"] == "agent" and delta > 1e-8:
                # The owner asked for ChatGPT help whenever a revision raises the index.
                # This is a diagnostic escalation, not proof that the edit is bad.
                case["recommended_text_sha256"] = versions[-2]["text_sha256"]
                handoff(case, "chatgpt", ["model_a_increased_after_agent_revision",
                                        "score_is_uncalibrated_advisory_only"])
                save(case)
                return
            if version["stage"] == "agent" and abs(delta) <= 1e-8:
                # A plateau ends score-trend revisions. ChatGPT receives the
                # current text for quality review; the score alone proves nothing.
                handoff(case, "chatgpt", ["model_a_plateau_after_agent_revision",
                                         "score_trend_stopped"])
                save(case)
                return
        reasons = list(report["findings"])
        if report["grammar"]["issues"]:
            reasons.append("grammar_review")
        if report["vale"]["issues"]:
            reasons.append("style_review")
        if ai_index > THRESHOLD:
            reasons.append("model_a_editorial_review_advisory")
        if len(versions) > 1:
            previous = versions[-2]
            if {"short_article", "long_article"}.intersection(previous["report"]["findings"]):
                case.pop("recommended_text_sha256", None)
            new_findings = sorted(set(report["findings"]) - set(previous["report"]["findings"]))
            if new_findings:
                version["new_findings"] = new_findings
                if not {"short_article", "long_article"}.intersection(previous["report"]["findings"]):
                    case["recommended_text_sha256"] = previous["text_sha256"]
                reasons.append("revision_introduced_new_findings")
        agent_versions = [v for v in versions if v["stage"] == "agent"]
        attempted = {v["rule"] for v in agent_versions}
        edit = None
        can_edit = version["stage"] != "assistant" and len(agent_versions) < MAX_AGENT
        semantic = report.get("semantic", {})
        facts = report.get("facts", {})
        size_issue = next((issue for issue in ("short_article", "long_article") if issue in reasons), None)
        if can_edit and semantic_editor is not None and (semantic.get("issues") or size_issue):
            candidate = semantic_editor(article, report)
            if candidate:
                draft, changes = candidate
                if article_digest(draft) not in {v["text_sha256"] for v in versions}:
                    append_version(case, draft, "agent", changes,
                                   "article_length" if size_issue else "semantic")
                    save(case)
                    continue
                report["semantic_edit_status"] = "repeats_previous_version"
        edit = automatic_edit(article, attempted)
        if can_edit and edit:
            candidate, rule, change = edit
            append_version(case, candidate, "agent", [change], rule)
            save(case)
            continue
        pending_edit = report.get("semantic_edit_status")
        if can_edit and pending_edit == "provider_error":
            # Local provider had a bounded retry already; escalate rather than stall.
            handoff(case, "chatgpt", ["local_agent_provider_error",
                                    report.get("semantic_edit_error", "unresolved_agent_problem")])
            save(case)
            return
        if can_edit and pending_edit == "fact_check_pending":
            case["decision"] = "fact_check_pending"
            save(case)
            return
        if semantic.get("issues"):
            reasons.append("semantic_issues")
        if facts.get("status") == "completed" and any(c["verdict"] != "confirmed" for c in facts["claims"]):
            reasons.append("open_claims")
        if facts.get("status") == "fact_check_pending":
            reasons.append("fact_check_pending")
        if semantic and semantic.get("status") != "completed":
            reasons.append("semantic_review_pending")
        unresolved_text = bool(edit) or bool({
            "short_article", "long_article", "fiction_claim_of_reality_review", "similar_article",
            "grammar_review", "style_review", "revision_introduced_new_findings", "semantic_issues", "open_claims",
        }.intersection(reasons))
        if version["stage"] != "assistant" and unresolved_text:
            handoff(case, "chatgpt", ["local_edits_exhausted", *reasons])
        elif semantic_editor is not None and (
            facts.get("status") == "fact_check_pending" or semantic.get("status") != "completed"
        ) and version["stage"] != "assistant":
            case["decision"] = ("fact_check_pending" if facts.get("status") == "fact_check_pending"
                                else "semantic_review_pending")
        else:
            handoff(case, "owner", [*reasons, *report["manual_checks_pending"]])
        save(case)
        return


def save_case(path, case):
    save_text(path, json.dumps(case, ensure_ascii=False, indent=2) + "\n")


def save_text(path, content):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
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
