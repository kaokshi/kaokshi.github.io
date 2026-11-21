"""Run a paired local-author control. Private outputs never enter the live article corpus."""
import argparse
from contextlib import closing
import json
from pathlib import Path

from editorial_agent_v1 import article_digest
from editorial_layout import FIELD_LINE, parse_sections
from editorial_length import count_characters
from editorial_local_llm import ProviderFailure, complete, read_json_response
from editorial_memory import connect, recalled_provider
from editorial_revision import save_case
from editorial_writer import private_path, topic_key, write_article


def trial(root, directory, channel, topic, documents, seed):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    scores = {}
    for arm in ("baseline", "memory"):
        score = {}
        def send(endpoint, path, payload, timeout):
            if path == "/api/chat":
                payload["options"]["seed"] = seed
            else:
                payload["seed"] = seed
            return read_json_response(endpoint, path, payload, timeout)
        def model(task, payload):
            if task == "write":
                score["reminders"] = [r["observed"] for r in payload.get("genre_experience", [])]
            reply = complete({"task": task, **payload, "single_attempt": True}, send=send)
            save_case(directory / f"{arm}-{task}.json", reply)
            if task == "write":
                lines = [line for p in reply["paragraphs"] for line in p.splitlines()]
                score["field_lines"] = sum(bool(FIELD_LINE.match(line)) for line in lines)
                blocks = [topic_key(p) for p in reply["paragraphs"]]
                score["duplicate_paragraphs"] = len(blocks) - len(set(blocks))
            return reply
        provider = recalled_provider(root, channel, model) if arm == "memory" else model
        try:
            article = write_article(channel, topic, provider, documents=documents)
            score.update(text_sha256=article_digest(article), length=count_characters(article["body_html"]),
                         sections=len(parse_sections(article["body_html"])), status="draft_valid")
            save_case(directory / f"{arm}-article.json", article)
        except (ProviderFailure, ValueError, KeyError, OSError, RuntimeError) as exc:
            score.update(status="blocked", reason=str(exc))
        scores[arm] = score
        result = {"topic": topic, "channel": channel, "seed": seed, "arms": scores, "activated": False}
        save_case(directory / "comparison.json", result)
    with closing(connect(root)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS writer_trials(id TEXT PRIMARY KEY, channel TEXT, report TEXT)")
        from hashlib import sha256
        data = json.dumps(result, ensure_ascii=False, sort_keys=True)
        db.execute("INSERT OR IGNORE INTO writer_trials VALUES (?,?,?)",
                   (sha256(data.encode()).hexdigest(), channel, data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", choices=("legends", "science"), required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--seed", type=int, default=47)
    args = parser.parse_args()
    docs = json.loads(args.documents.read_text())["documents"] if args.documents else []
    trial(private_path(args.memory), private_path(args.output), args.channel, args.topic, docs, args.seed)
