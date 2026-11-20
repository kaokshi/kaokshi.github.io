#!/usr/bin/env python3
"""Prepare a private, resumable editorial dossier for an active Zen channel."""

import argparse
from contextlib import ExitStack
from fcntl import LOCK_EX, flock
from hashlib import sha256
import json
from pathlib import Path
import re

from editorial_agent_v1 import ACTIVE, article_digest
from editorial_ai_check import CONTENT
from editorial_factcheck import fact_check, valid_sources
from editorial_quality import similarity_report
from editorial_research import europe_pmc
from editorial_revision import run_case, save_case, save_text
from editorial_revision_checks import check_draft
from editorial_semantic import diagnose, revise, resize_article
from editorial_length import count_characters, length_finding
from editorial_layout import markdown_body, repair_sections
from editorial_writer import Pending, command_provider, private_path, topic_key, write_article


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path is not None else None


def validate_draft(article, channel):
    if (article.get("channel") != channel or article.get("status") != "draft"
            or article.get("publication_authorized") is True
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", article.get("slug", ""))
            or not isinstance(article.get("title"), str) or not article["title"].strip()):
        raise ValueError("Provide an identified draft for the selected active channel")
    article_digest(article)


def new_case(root, directory, existing, channel, topic, provider, *, documents=()):
    if existing is None:
        originals = sorted(CONTENT.glob("*.json"))
        peers = sorted(root.glob("*/case.json"))
        titles = [load(path)["title"] for path in originals]
        titles.extend(load(path)["versions"][0]["article"]["title"] for path in peers)
        response_path = directory / "writer-response.json"
        status_path = directory / "writer-status.json"

        def generate(task, payload):
            inputs = [getattr(provider, "identity", None), task, payload]
            key = sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            saved = load(response_path) if response_path.is_file() else {}
            status = load(status_path) if status_path.is_file() else {}
            if (inputs[0] is not None and saved.get("input_sha256") == key
                    and status.get("status") != "draft_rejected"):
                return saved["response"]
            reply = provider(task, payload)
            save_case(response_path, {"input_sha256": key, "response": reply})
            return reply

        article = write_article(channel, topic, generate, titles, documents=tuple(documents),
                                allow_length_repair=True)
    else:
        article = existing
    validate_draft(article, channel)
    case = {"schema": "zen-editorial-revision-case-v1", "decision": "checks_pending",
            "topic_sha256": sha256(topic_key(topic).encode()).hexdigest() if topic else None,
            "publication_authorized": False, "rss_delivery_authorized": False, "handoff": None,
            "versions": [{"stage": "original", "article": article, "text_sha256": article_digest(article)}]}
    if existing is None:
        case["writer_provider"] = getattr(provider, "identity", None)
        with (root / ".catalog-lock").open("a") as catalog:
            flock(catalog, LOCK_EX)
            peers = [load(path)["versions"][-1]["article"] for path in root.glob("*/case.json")]
            if (topic_key(article["title"]) in {topic_key(peer["title"]) for peer in peers}
                    or similarity_report(originals, candidate=article, drafts=peers)):
                raise ValueError("Generated draft resembles an article already in the editorial corpus")
            save_case(directory / "case.json", case)
    else:
        save_case(directory / "case.json", case)
    return case


def assemble_report(article, provider, research, documents, reviewed, checkpoint, quality,
                    *, saved=None):
    saved = saved or {}
    semantic = saved.get("semantic_review")
    if not semantic or semantic["status"] != "completed":
        semantic = diagnose(article, provider, reviewed)
    checkpoint("semantic_review", semantic)
    facts = saved.get("fact_check")
    if not facts or facts["status"] not in {"completed", "not_applicable"}:
        facts = fact_check(article, provider, research, documents, reviewed)
    checkpoint("fact_check", facts)
    if facts["status"] == "completed":
        disputed = [c for c in facts["claims"] if c["verdict"] == "disputed"]
        semantic["issues"] = [*semantic["issues"], *[
            {"id": f"factcheck:{claim['id']}", "category": "unsupported_claim",
             "paragraph": claim["paragraph"], "quote": claim["quote"], "reason": claim["reason"],
             "evidence": claim["evidence"]} for claim in disputed
        ]]
    report = quality(article, [source["url"] for source in facts["sources"]])
    return {**report, "semantic": semantic, "facts": facts, "llm": getattr(provider, "identity", None)}


def execute(case, directory, provider, research, documents, reviews, response, quality):
    path = directory / "case.json"

    def save(data):
        save_case(path, data)

    def check(article):
        version = case["versions"][-1]
        reviewed = reviews.get(version["text_sha256"])
        identity = getattr(provider, "identity", None)
        inputs = [identity, reviewed, documents,
                  getattr(research, "__name__", None), version["text_sha256"]]
        key = sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        reuse = identity is not None and version.get("stage_input_sha256") == key
        if not reuse:
            version["stages"] = {}
        version["stage_input_sha256"] = key

        def checkpoint(stage, report):
            version.setdefault("stages", {})[stage] = report
            save(case)

        return assemble_report(article, provider, research, documents, reviewed, checkpoint, quality,
                               saved=version["stages"])

    def semantic_editor(article, report):
        try:
            if {"short_article", "long_article"}.intersection(report["findings"]):
                if article["channel"] == "science" and not documents:
                    raise Pending("fact_check_pending", "Science expansion or compression requires retrieved sources")
                edited = resize_article(article, provider, tuple(documents or ()))
            else:
                edited = revise(article, report["semantic"]["issues"], provider,
                                reviews.get(article_digest(article)))
            if edited and article.get("layout_schema"):
                candidate, changes = edited
                edited = repair_sections(candidate, provider), changes
            report["semantic_edit_status"] = "proposed" if edited else "no_applicable_edit"
            return edited
        except Pending as exc:
            report["semantic_edit_status"] = exc.status
            report["semantic_edit_error"] = str(exc)
        except (TypeError, ValueError, KeyError) as exc:
            report["semantic_edit_status"] = "invalid_revision"
            report["semantic_edit_error"] = str(exc)
        return None

    run_case(case, check, save, response, semantic_editor=semantic_editor)
    version = case["versions"][-1]
    if case.get("recommended_text_sha256"):
        draft = next(v for v in case["versions"] if v["text_sha256"] == case["recommended_text_sha256"])
    elif version.get("report", {}).get("automated_checks_completed") is not True and len(case["versions"]) > 1:
        draft = case["versions"][-2]
    else:
        draft = version
    article = draft["article"]
    prose = markdown_body(article["body_html"])
    save_case(directory / "draft.json", article)
    save_text(directory / "draft.md", f'# {article["title"]}\n\n{prose}\n')
    save_case(directory / "reports.json", {
        "decision": case["decision"], "draft_sha256": draft["text_sha256"],
        "draft_characters_without_whitespace": count_characters(article["body_html"]),
        "writer_provider": case.get("writer_provider"),
        "last_checked_version": version["text_sha256"], "handoff": case["handoff"],
        "versions": [{k: v for k, v in row.items() if k != "article"} for row in case["versions"]],
    })
    save_case(directory / "sources.json", {
        "draft_sha256": draft["text_sha256"], "facts": draft.get("report", {}).get("facts"),
    })
    return case["decision"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True, choices=ACTIVE)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic")
    group.add_argument("--article", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Private workspace outside the repository")
    parser.add_argument("--provider", type=Path, help="Configured local LLM executable")
    researchers = parser.add_mutually_exclusive_group()
    researchers.add_argument("--research-provider", type=Path, help="Configured search and reading executable")
    researchers.add_argument("--europe-pmc", action="store_true", help="Retrieve public research abstracts")
    parser.add_argument("--research-query", help="Public source discovery query for the topic")
    parser.add_argument("--source-documents", type=Path, help="JSON with retrieved documents")
    parser.add_argument("--illustrations", type=Path, help="Manifest of 7–8 verified JPEG assets")
    parser.add_argument("--asset-root", type=Path, default=CONTENT.parents[1])
    parser.add_argument("--review", type=Path, help="Operator review for specific text hashes")
    parser.add_argument("--assistant-response", type=Path)
    args = parser.parse_args()
    try:
        provider = command_provider(args.provider)
        research = None
        if args.research_provider:
            research = command_provider(args.research_provider)
        elif args.europe_pmc:
            research = europe_pmc
        docs = load(args.source_documents)
        documents = docs["documents"] if docs is not None else None
        reviews = {}
        for row in load(args.review) or []:
            digest = row["text_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in reviews:
                raise ValueError("Use one review per valid text SHA-256")
            reviews[digest] = row
        response = load(args.assistant_response)
        root = private_path(args.output)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = load(args.article)
        topic_digest = None
        if existing is not None:
            validate_draft(existing, args.channel)
            slug = existing["slug"]
        else:
            if not topic_key(args.topic):
                raise ValueError("Topic cannot be blank")
            topic_digest = sha256(topic_key(args.topic).encode()).hexdigest()
            slug = f"{args.channel}-draft-{topic_digest[:16]}"
        directory = root / slug
        if directory.resolve().parent != root:
            raise ValueError("Case directory must be in the private workspace")
        directory.mkdir(exist_ok=True, mode=0o700)
        with (directory / ".lock").open("a") as lock, ExitStack() as stack:
            flock(lock, LOCK_EX)
            path = directory / "case.json"
            try:
                saved_docs = directory / "source-documents.json"
                if documents is None and saved_docs.is_file():
                    documents = load(saved_docs)["documents"]
                if documents is None and args.topic and args.channel == "science" and research and args.provider:
                    try:
                        documents = research("discover", {"topic": args.topic, "search_query": args.research_query,
                                                         "claims": []})["documents"]
                    except Pending:
                        documents = None
                if documents:
                    documents = list(valid_sources(documents).values())
                    save_case(saved_docs, {"documents": documents})
                if path.is_file():
                    case = load(path)
                else:
                    original_input = existing
                    if existing is not None:
                        existing = repair_sections(existing, provider)
                    case = new_case(root, directory, existing, args.channel, args.topic, provider,
                                    documents=documents or ())
                    if original_input is not None:
                        case["input_article"] = original_input
                        save_case(path, case)
            except Pending as exc:
                save_case(directory / "writer-status.json", {"status": exc.status, "reason": str(exc)})
                print(json.dumps({"status": exc.status, "directory": str(directory)}))
                raise SystemExit(2) from exc
            except (ValueError, TypeError, KeyError) as exc:
                save_case(directory / "writer-status.json", {"status": "draft_rejected", "reason": str(exc)})
                raise
            if args.topic:
                first = case["versions"][0]
                count = count_characters(first["article"]["body_html"])
                save_case(directory / "writer-status.json", {"status": "draft_created",
                          "text_sha256": first["text_sha256"], "characters": count,
                          "length_finding": length_finding(count)})
            if existing is not None and existing not in (case.get("input_article"), case["versions"][0]["article"]):
                raise ValueError("Resume using the original draft for this case")
            if existing is None and case.get("topic_sha256") != topic_digest:
                raise ValueError("Topic does not match the existing case")

            tool = None

            def grammar(text):
                nonlocal tool
                if tool is None:
                    import language_tool_python
                    tool = stack.enter_context(language_tool_python.LanguageTool("ru"))
                return tool.check(text)

            decision = execute(case, directory, provider, research, documents, reviews, response,
                               lambda draft, urls: check_draft(draft, grammar, source_urls=urls))
            assembled_path = directory / "assembled.json"
            assembled_path.unlink(missing_ok=True)
            if args.illustrations:
                from editorial_cover import cover_selection
                assembled = cover_selection(load(directory / "draft.json"), load(args.illustrations), args.asset_root)
                save_case(assembled_path, assembled)
            print(json.dumps({"status": decision, "directory": str(directory)}))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
