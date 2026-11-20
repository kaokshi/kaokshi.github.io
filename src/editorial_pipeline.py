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
from editorial_factcheck import fact_check
from editorial_quality import similarity_report
from editorial_research import europe_pmc
from editorial_revision import run_case, save_case
from editorial_revision_checks import check_draft
from editorial_semantic import diagnose, revise
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


def new_case(root, directory, existing, channel, topic, provider):
    if existing is None:
        originals = sorted(CONTENT.glob(f"{channel}-*.json"))
        other_cases = sorted(root.glob(f"{channel}-*/case.json"))
        titles = [load(path)["title"] for path in originals]
        titles.extend(load(path)["versions"][0]["article"]["title"] for path in other_cases)
        article = write_article(channel, topic, provider, titles)
        if similarity_report(originals, candidate=article):
            raise ValueError("Generated draft resembles a repository article")
        # Cases retain the current draft as a regular JSON file for reuse by the corpus checker.
        others = [path.parent / "draft.json" for path in other_cases
                  if (path.parent / "draft.json").is_file()]
        if similarity_report(others, candidate=article):
            raise ValueError("Generated draft resembles a draft already in this workspace")
    else:
        article = existing
    validate_draft(article, channel)
    case = {"schema": "zen-editorial-revision-case-v1", "decision": "checks_pending",
            "topic_sha256": sha256(topic_key(topic).encode()).hexdigest() if topic else None,
            "publication_authorized": False, "rss_delivery_authorized": False, "handoff": None,
            "versions": [{"stage": "original", "article": article, "text_sha256": article_digest(article)}]}
    save_case(directory / "case.json", case)
    return case


def assemble_report(article, provider, research, documents, reviewed, checkpoint, quality):
    semantic = diagnose(article, provider, reviewed)
    checkpoint("semantic_review", semantic)
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
    return {**report, "semantic": semantic, "facts": facts}


def execute(case, directory, provider, research, documents, reviews, response, quality):
    path = directory / "case.json"

    def save(data):
        save_case(path, data)

    def check(article):
        version = case["versions"][-1]
        reviewed = reviews.get(version["text_sha256"])

        def checkpoint(stage, report):
            version.setdefault("stages", {})[stage] = report
            save(case)

        return assemble_report(article, provider, research, documents, reviewed, checkpoint, quality)

    def semantic_editor(article, report):
        try:
            edited = revise(article, report["semantic"]["issues"], provider,
                            reviews.get(article_digest(article)))
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
    save_case(directory / "draft.json", draft["article"])
    save_case(directory / "reports.json", {
        "decision": case["decision"], "draft_sha256": draft["text_sha256"],
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
    parser.add_argument("--source-documents", type=Path, help="JSON with retrieved documents")
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
                with (root / ".catalog-lock").open("a") as catalog:
                    flock(catalog, LOCK_EX)
                    if path.is_file():
                        case = load(path)
                    else:
                        case = new_case(root, directory, existing, args.channel, args.topic, provider)
            except Pending as exc:
                save_case(directory / "writer-status.json", {"status": exc.status, "reason": str(exc)})
                print(json.dumps({"status": exc.status, "directory": str(directory)}))
                raise SystemExit(2) from exc
            except (ValueError, TypeError, KeyError) as exc:
                save_case(directory / "writer-status.json", {"status": "draft_rejected", "reason": str(exc)})
                raise
            if args.topic:
                save_case(directory / "writer-status.json", {"status": "draft_created",
                          "text_sha256": case["versions"][0]["text_sha256"]})
            if existing is not None and existing != case["versions"][0]["article"]:
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
            print(json.dumps({"status": decision, "directory": str(directory)}))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
