#!/usr/bin/env python3
"""Generate private Zen drafts through an explicitly configured model executable."""

import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import subprocess
import unicodedata

from editorial_ai_check import ROOT
from editorial_agent_v1 import ACTIVE
from editorial_quality import MIN_CHARS
from editorial_revision import save_case

GUIDES = {
    "legends": ("Легенды тёмных улиц", "Создай оригинальный вымышленный сюжет. "
                "Проверь хронологию, мотивацию персонажей и причинно-следственные связи. "
                "Явно обозначь вымысел."),
    "science": ("За гранью очевидного", "Объясни явление понятным языком. "
                "Отметь границы доказанного, избегай догадок и вымышленных ссылок. "
                "Точные факты должны быть проверяемыми по первоисточникам."),
}


class Pending(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


def command_provider(executable):
    def invoke(task, payload):
        if executable is None:
            raise Pending("provider_not_configured", "LLM executable was not configured")
        request = json.dumps({"task": task, **payload}, ensure_ascii=False)
        try:
            proc = subprocess.run([str(executable.expanduser().resolve())], input=request, capture_output=True,
                                  text=True, encoding="utf-8", timeout=240, check=False)
            if proc.returncode:
                raise Pending("provider_error", f"Provider exit code {proc.returncode}")
            response = json.loads(proc.stdout)
            if not isinstance(response, dict):
                raise ValueError("Expected an object")
            return response
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise Pending("provider_error", type(exc).__name__) from exc
    return invoke


def topic_key(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def private_path(path):
    path = path.resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise ValueError("Drafts and reports must be stored outside the repository")
    return path


def write_article(channel, topic, provider, existing_titles=()):
    if channel not in ACTIVE or not topic_key(topic):
        raise ValueError("Use a nonempty topic for legends or science")
    all_titles = list(existing_titles)
    if topic_key(topic) in {topic_key(title) for title in all_titles}:
        raise ValueError("Topic already exists in the editorial corpus")
    channel_name, genre_prompt = GUIDES[channel]
    reply = provider("write", {
        "channel": channel, "topic": topic, "existing_titles": all_titles,
        "instruction": f"{genre_prompt} Дай заголовок, зацепку, развитие и естественный финал. "
                       f"Напиши 3500–5000 знаков без пробелов на русском. Используй "
                       f"конкретные формулировки. Заверши оригинальным призывом подписаться "
                       f"на канал «{channel_name}». Ответ JSON с title, paragraphs и cta.",
    })
    title, blocks, cta = reply.get("title"), reply.get("paragraphs"), reply.get("cta")
    if (not isinstance(title, str) or not title.strip() or not isinstance(blocks, list)
            or len(blocks) < 3 or not all(isinstance(p, str) and p.strip() for p in blocks)
            or not isinstance(cta, str) or channel_name.casefold() not in cta.casefold()
            or not re.search(r"подпи[сш]", cta, re.I)):
        raise ValueError("Writer output needs a title, article paragraphs and a channel-specific CTA")
    if topic_key(title) in {topic_key(t) for t in all_titles}:
        raise ValueError("Generated title duplicates another article")
    blocks = [p.strip() for p in blocks] + [cta.strip()]
    if len(set(map(topic_key, blocks))) != len(blocks):
        raise ValueError("Writer output has duplicate paragraphs")
    prose = " ".join(blocks)
    if len(re.sub(r"\s", "", prose)) < MIN_CHARS or len(re.findall(r"[а-яё]", prose, re.I)) < MIN_CHARS // 2:
        raise ValueError("Writer output needs at least 3000 non-whitespace characters and Russian prose")
    slug = channel + "-draft-" + sha256(topic_key(topic).encode()).hexdigest()[:16]
    return {"slug": slug, "channel": channel, "title": title.strip(), "status": "draft",
            "body_html": "\n".join(f"<p>{escape(p, quote=False)}</p>" for p in blocks),
            "sources": [], "image_files": [], "editorial_reviewed": False,
            "publication_authorized": False, "release_hold": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True, choices=ACTIVE)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--provider", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        path = private_path(args.output)
        if path.exists():
            raise ValueError("Output already exists")
        from editorial_ai_check import CONTENT
        from editorial_quality import similarity_report
        corpus = sorted(CONTENT.glob(f"{args.channel}-*.json"))
        titles = [json.loads(p.read_text(encoding="utf-8"))["title"] for p in corpus]
        article = write_article(args.channel, args.topic, command_provider(args.provider), titles)
        if similarity_report(corpus, candidate=article):
            raise ValueError("Draft resembles an existing article")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        save_case(path, article)
        print(json.dumps({"status": "draft_created", "file": str(path)}))
    except Pending as exc:
        print(json.dumps({"status": exc.status, "reason": str(exc)}))
        raise SystemExit(2) from exc
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
