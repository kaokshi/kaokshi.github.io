#!/usr/bin/env python3
"""Prepare a private, resumable editorial dossier for an active Zen channel."""

import argparse
from contextlib import ExitStack
from fcntl import LOCK_EX, flock
from hashlib import sha256
import json
from pathlib import Path
import re

from editorial_agent_v1 import ACTIVE, MAX_AGENT, article_digest
from editorial_ai_check import CONTENT
from editorial_factcheck import fact_check, valid_sources
from editorial_quality import similarity_report
from editorial_research import europe_pmc, topic_sources
from editorial_revision import agent_attempts_used, run_case, save_case, save_text
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
        status_path = directory / "writer-status.json"

        def generate(task, payload):
            response_path = directory / {"write": "writer-response.json", "article_layout": "article-layout-response.json",
                                         "section_plan": "section-plan-response.json"}[task]
            inputs = [getattr(provider, "identity", None), task, payload]
            key = sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            saved = load(response_path) if response_path.is_file() else {}
            status = load(status_path) if status_path.is_file() else {}
            if (inputs[0] is not None and saved.get("input_sha256") == key
                    and status.get("status") != "draft_rejected"):
                return saved["response"]
            if task == "write":
                from editorial_writer import validate_prose_reply
                attempts_path = directory / "writer-attempts.json"
                attempts = load(attempts_path) if attempts_path.exists() else []
                while len(attempts) < 2:
                    attempt = {"number": len(attempts) + 1, "input_sha256": key, "status": "started"}
                    attempts.append(attempt)
                    save_case(attempts_path, attempts)
                    reply = None
                    try:
                        reply = provider(task, dict(payload, single_attempt=True))
                        save_case(directory / f"writer-raw-{len(attempts)}.json", reply)
                        validate_prose_reply(reply)
                    except (ValueError, Pending) as exc:
                        attempt.update(status="rejected", reason=str(exc))
                        from editorial_memory import remember_failure
                        remember_failure(root, directory.name, channel, "draft_rejected", str(exc),
                                         reply if isinstance(exc, ValueError) else None)
                        save_case(attempts_path, attempts)
                        continue
                    attempt["status"] = "accepted"
                    save_case(response_path, {"input_sha256": key, "response": reply})
                    save_case(attempts_path, attempts)
                    break
                else:
                    raise Pending("writer_contract_pending", "Writer attempts exhausted; see writer-attempts.json")
            else:
                reply = provider(task, payload)
            if task == "write":
                from editorial_memory import writer_experience_report
                report = writer_experience_report(root, channel, getattr(provider, "last_reminders", []), reply)
                save_case(directory / "writer-experience.json", report)
            save_case(response_path, {"input_sha256": key, "response": reply})
            return reply

        article = write_article(channel, topic, generate, titles, documents=tuple(documents),
                                allow_length_repair=True)
        settings = load(CONTENT.parents[1] / "config" / "site.json")
        from editorial_auto_release import officially_launched
        if (officially_launched(settings) and settings.get("auto_release_enabled") is True
                and channel in settings.get("auto_release_active_channels", [])):
            article.update(auto_release_candidate=True, release_hold=False)
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
            from editorial_memory import historical_drafts
            peers.extend(historical_drafts(root))
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
        control = saved.get("fact_control")
        if (article["channel"] == "science" and reviewed is None
                and (getattr(provider, "identity", None) or {}).get("kind") in {"llama.cpp", "ollama"}):
            from editorial_factcheck import reference_verdicts
            if not control or control["status"] != "completed":
                try:
                    control = reference_verdicts(provider)
                except (Pending, ValueError, KeyError, TypeError) as exc:
                    control = {"status": "fact_check_pending", "reason": str(exc)}
                checkpoint("fact_control", control)
        if control and control["status"] != "completed" and reviewed is None:
            facts = {"status": "fact_check_pending", "text_sha256": article_digest(article),
                     "reason": "Fact reviewer failed independent source controls", "sources": [], "claims": []}
        else:
            facts = fact_check(article, provider, research, documents, reviewed, previous=facts,
                               checkpoint=lambda partial: checkpoint("fact_check", partial))
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
        digest = article_digest(article)
        version = next(v for v in case["versions"] if v["text_sha256"] == digest)
        reviewed = reviews.get(version["text_sha256"])
        identity = getattr(provider, "identity", None)
        inputs = ["source-controls-v1", identity, reviewed, documents,
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

    latest = case["versions"][-1]
    review = reviews.get(latest["text_sha256"])
    if (response is None and case.get("decision") == "assistant_handoff_pending"
            and (case.get("handoff") or {}).get("transport_status") == "not_connected"
            and review and review.get("issues") and review.get("edits")
            and agent_attempts_used(case) < MAX_AGENT):
        # A newly supplied substantive review can use the remaining local attempt.
        review_key = sha256(json.dumps(review, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if case.get("resumed_review_sha256") != review_key:
            case.setdefault("handoff_history", []).append(case["handoff"])
            case.update(decision="checks_pending", handoff=None, resumed_review_sha256=review_key)
            save(case)
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
    group.add_argument("--auto-topic", action="store_true")
    group.add_argument("--article", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Private workspace outside the repository")
    parser.add_argument("--provider", type=Path, help="Configured local LLM executable")
    researchers = parser.add_mutually_exclusive_group()
    researchers.add_argument("--research-provider", type=Path, help="Configured search and reading executable")
    researchers.add_argument("--europe-pmc", action="store_true", help="Retrieve public research abstracts")
    parser.add_argument("--research-query", help="Public source discovery query for the topic")
    parser.add_argument("--source-documents", type=Path, help="JSON with retrieved documents")
    media = parser.add_mutually_exclusive_group()
    media.add_argument("--produce-media", action="store_true", help="Plan and source seven editorial images")
    parser.add_argument("--photo-query", help="Commons search terms for science")
    parser.add_argument("--generator-rights", help="Verified licensing basis for the local image model")
    media.add_argument("--media-plan", type=Path, help="Seven or eight Commons IDs or original scene prompts")
    parser.add_argument("--cover-review", type=Path, help="Optional hash-bound visual review of this exact set")
    parser.add_argument("--image-generator", default="http://127.0.0.1:7860")
    media.add_argument("--illustrations", type=Path, help="Manifest of 7–8 verified JPEG assets")
    parser.add_argument("--asset-root", type=Path, default=CONTENT.parents[1])
    parser.add_argument("--review", type=Path, help="Operator review for specific text hashes")
    parser.add_argument("--assistant-response", type=Path)
    args = parser.parse_args()
    root = private_path(args.output)
    from editorial_storage import workspace_lock
    with workspace_lock(root, exclusive=False):
        run(args, parser, root)


def run(args, parser, root):
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
        from editorial_memory import choose_topic, recalled_provider, image_conflicts, remember, remember_failure
        provider = recalled_provider(root, args.channel, provider)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if args.auto_topic:
            used = [load(p)["title"] for p in CONTENT.glob("*.json")]
            day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
            try:
                with (root / f".topic-{args.channel}.lock").open("a") as lock:
                    flock(lock, LOCK_EX)
                    try:
                        args.topic = choose_topic(root, args.channel, day, provider, used)
                    except ValueError:
                        if args.channel != "science" or not args.europe_pmc:
                            raise
                        # Use current source titles as briefs when model proposals are exhausted.
                        seeds = ("structural coloration", "animal migration", "glacier dynamics",
                                 "forest phenology", "coral fluorescence", "plant biomechanics")
                        seed = seeds[datetime.fromisoformat(day).toordinal() % len(seeds)]
                        records = topic_sources(seed, research)
                        if not records:
                            raise Pending("topic_selection_pending", "No source-backed topic candidates")
                        titles = [doc["title"] for doc in records]
                        args.topic = choose_topic(root, args.channel, day, lambda task, data: {"topics": titles}, used)
                        documents = [doc for doc in records if doc["title"] == args.topic]
                        save_case(root / f"topic-source-{day}-{args.channel}.json",
                                  {"topic": args.topic, "documents": documents})
                    source_path = root / f"topic-source-{day}-{args.channel}.json"
                    if documents is None and source_path.is_file():
                        saved_source = load(source_path)
                        if saved_source["topic"] == args.topic:
                            documents = saved_source["documents"]
            except (Pending, ValueError) as exc:
                save_case(root / f"topic-{day}-{args.channel}.json",
                          {"status": "topic_selection_pending", "reason": str(exc)})
                parser.exit(2, "topic_selection_pending\n")
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
                    query = args.research_query
                    if args.auto_topic and not query and not path.exists():
                        query = provider("research_query", {"topic": args.topic, "instruction":
                            "Give a short English database search query using the scientific subject of this topic."})["query"]
                    try:
                        documents = research("discover", {"topic": args.topic, "search_query": query,
                                                         "claims": []})["documents"]
                    except Pending:
                        if args.auto_topic and not path.exists():
                            raise
                        documents = None
                    if args.auto_topic and not path.exists() and not documents:
                        raise Pending("fact_check_pending", "No source text retrieved for the selected science topic")
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
                raw_path = directory / "writer-response.json"
                remember_failure(root, slug, args.channel, "draft_rejected", str(exc), load(raw_path) if raw_path.exists() else None)
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
            remember(root, case)
            from editorial_cover import UnsuitableIllustrations, cover_selection
            from editorial_local_llm import ProviderFailure
            assembled_path = directory / "assembled.json"
            assembled_path.unlink(missing_ok=True)
            manifest_path = directory / "illustrations.json"
            selection_path = directory / "cover-selection.json"
            if args.produce_media and decision in {"checks_pending", "fact_check_pending",
                                                  "semantic_review_pending", "assistant_handoff_pending"}:
                save_case(selection_path, {"status": "waiting_for_editorial_checks", "decision": decision})
                print(json.dumps({"status": decision, "directory": str(directory)}))
                raise SystemExit(2)
            if args.media_plan or args.produce_media:
                from editorial_media import plan_images, produce, replace_duplicates
                production_root = private_path(args.asset_root)
                save_case(selection_path, {"status": "producing_illustrations"})
                try:
                    draft = load(directory / "draft.json")
                    plan_path = directory / "media-plan.json"
                    context_path = directory / "media-context.json"
                    context = {"article": article_digest(draft), "title": draft["title"],
                               "query": args.photo_query, "rights": args.generator_rights,
                               "provider": getattr(provider, "identity", None)}
                    if args.media_plan:
                        plan = load(args.media_plan)
                    elif context_path.exists() and plan_path.exists() and load(context_path) == context:
                        plan = load(plan_path)
                    else:
                        plan = plan_images(draft, provider, query=args.photo_query, rights_basis=args.generator_rights)
                    save_case(plan_path, plan)
                    save_case(context_path, context if not args.media_plan else {"external": True})
                    images = produce(args.channel, plan, production_root, args.image_generator,
                                     checkpoint=directory / "media-progress.json")
                    images = replace_duplicates(load(directory / "draft.json"), plan, images, production_root,
                                                root, args.image_generator, provider, query=args.photo_query)
                except (OSError, ValueError, KeyError, Pending) as exc:
                    remember_failure(root, slug, args.channel, "media_production_pending", str(exc))
                    save_case(selection_path, {"status": "media_production_pending", "reason": str(exc)})
                    parser.exit(2, f"media_production_pending: {exc}\n")
                save_case(manifest_path, images)
            if args.illustrations:
                save_case(manifest_path, load(args.illustrations))
            save_case(selection_path, {"status": "awaiting_illustrations"})
            if manifest_path.exists():
                try:
                    draft = load(directory / "draft.json")
                    images = load(manifest_path)
                    budget_path = directory / "visual-replacements.json"
                    basis = sha256(json.dumps([draft, images], sort_keys=True).encode()).hexdigest()
                    budget = load(budget_path) if budget_path.exists() else {}
                    if budget.get("basis") != basis:
                        budget = {"basis": basis, "attempts": 0, "images": images}
                    images = budget["images"]
                    while True:
                        try:
                            assembled = cover_selection(draft, images, args.asset_root,
                                review=load(args.cover_review), checkpoint=lambda result: save_case(selection_path, result))
                            break
                        except UnsuitableIllustrations as exc:
                            if not (args.media_plan or args.produce_media) or args.cover_review or budget["attempts"] >= 2:
                                raise
                            from editorial_media import replace_unsuitable
                            budget["attempts"] += 1
                            budget["rejected"] = exc.identifiers
                            save_case(budget_path, budget)
                            images = replace_unsuitable(draft, plan, images, exc.identifiers, production_root,
                                            root, args.image_generator, provider, args.photo_query)
                            budget["images"] = images
                            save_case(budget_path, budget)
                except (OSError, KeyError, ValueError, TypeError, ProviderFailure, Pending) as exc:
                    remember_failure(root, slug, args.channel, "cover_selection_pending", str(exc))
                    save_case(selection_path, {**load(selection_path), "status": "cover_selection_pending",
                                               "reason": str(exc)})
                    parser.exit(2, f"cover_selection_pending: {exc}\n")
                from editorial_preview import export_preview, verify_preview
                with (root / ".media-lock").open("a") as media_lock:
                    flock(media_lock, LOCK_EX)
                    peers = [load(p) for p in root.glob("*/assembled.json") if p != assembled_path]
                    def final_quality(version, urls):
                        return check_draft(version, grammar, source_urls=urls,
                                           asset_root=args.asset_root, peers=peers)

                    report = assemble_report(
                        assembled, provider, research, documents, reviews.get(article_digest(assembled)),
                        lambda stage, result: save_case(directory / f"assembled-{stage}.json", result),
                        final_quality)
                    save_case(directory / "assembled-quality.json", report)
                    semantic, facts = report["semantic"], report["facts"]
                    digest = article_digest(assembled)
                    facts_ok = (facts.get("status") == "not_applicable" and args.channel == "legends") or (
                        facts.get("status") == "completed" and facts.get("text_sha256") == digest
                        and bool(facts.get("claims")) and all(c["verdict"] == "confirmed" for c in facts["claims"]))
                    if (not facts_ok or semantic.get("status") != "completed" or semantic.get("issues")
                            or semantic.get("text_sha256") != digest):
                        print(json.dumps({"status": "assembled_review_pending", "directory": str(directory)}))
                        raise SystemExit(2)
                    audit = report["image_corpus_audit"]
                    prior_matches = image_conflicts(root, assembled, args.asset_root)
                    save_case(directory / "historical-image-conflicts.json", prior_matches)
                    if (prior_matches or report["image_similarity"]["duplicates"] or audit.get("unreadable")
                            or audit["near_duplicate_status"] != "completed"):
                        parser.exit(2, "image_corpus_review_pending\n")
                    export_preview(assembled, args.asset_root, directory)
                    save_case(directory / "format-report.json", verify_preview(assembled, args.asset_root, directory))
                    remember(root, case, assembled, args.asset_root)
                    save_case(assembled_path, assembled)
            print(json.dumps({"status": decision, "directory": str(directory)}))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
