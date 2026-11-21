#!/usr/bin/env python3
"""Generate private Zen drafts through an explicitly configured model executable."""

import argparse
from hashlib import sha256
from editorial_layout import SECTION_SCHEMA, arrange, assert_reader_text
import json
import os
from pathlib import Path
import re
import subprocess
import unicodedata

from editorial_ai_check import ROOT
from editorial_agent_v1 import ACTIVE
from editorial_length import count_characters, length_finding, MIN_CHARS, MAX_CHARS
from editorial_local_llm import request_timeout
from editorial_revision import save_case

GUIDES = {
    "legends": ("Легенды тёмных улиц", "Создай оригинальный вымышленный сюжет. "
                "Покажи мотивацию и хронологию через действия. "
                "Веди художественное повествование до развязки. Не вставляй научных справок, "
                "ссылок на исследования и комментариев о том, как устроен сюжет."),
    "science": ("За гранью очевидного", "Объясни явление понятным языком. "
                "Используй прочитанные документы, точные факты сверяй с первоисточниками. "
                "Отметь границы доказанного, избегай догадок, вымышленных ссылок и цитат."),
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
            timeout = 2 * request_timeout() + 20 if executable.name == "editorial_local_llm.py" else 445
            proc = subprocess.run([str(executable.expanduser().resolve())], input=request, capture_output=True,
                                  text=True, encoding="utf-8", timeout=timeout, check=False)
            if proc.returncode:
                reason = proc.stderr[:240].strip() if executable.name == "editorial_local_llm.py" else ""
                raise Pending("provider_error", reason or f"Provider exit code {proc.returncode}")
            response = json.loads(proc.stdout)
            if not isinstance(response, dict):
                raise ValueError("Expected an object")
            return response
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise Pending("provider_error", type(exc).__name__) from exc
    if executable is None:
        invoke.identity = {"kind": "not_configured"}
    elif executable.name == "editorial_local_llm.py":
        invoke.identity = {"kind": os.environ.get("ZEN_LLM_BACKEND", "llama.cpp"),
                           "model": os.environ.get("ZEN_LLM_MODEL"),
                           "revision": os.environ.get("ZEN_LLM_MODEL_REVISION")}
    else:
        invoke.identity = {"kind": "local_executable", "name": executable.name}
    return invoke


def topic_key(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def private_path(path):
    path = path.resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise ValueError("Drafts and reports must be stored outside the repository")
    return path


def recover_numbered_prose(blocks):
    """Recognize Qwen's section-labelled multiline envelope; preserve every prose line."""
    if not isinstance(blocks, list) or not all(isinstance(p, str) for p in blocks):
        return blocks
    rows = [p for p in blocks if p.strip().casefold() not in {"cta", "cta:"}]
    if len(rows) != 1 or not re.match(r"^\s*start\s*:?\s*1\s*\nheading:", rows[0], re.I):
        return blocks
    pieces = re.split(r"(?im)^start\s*:?\s*(\d+)\s*\nheading:[^\n]*\n", rows[0].strip())
    if pieces[0] or len(pieces) < 7 or [int(n) for n in pieces[1::2]] != list(range(1,len(pieces)//2+1)):
        return blocks
    if any(not text.strip() for text in pieces[2::2]):
        return blocks
    return [line.strip() for text in pieces[2::2] for line in text.splitlines() if line.strip()]


def validate_prose_reply(reply):
    if (not isinstance(reply, dict) or set(reply) - {"title", "paragraphs", "cta", "sections"}
            or not isinstance(reply.get("paragraphs"), list) or len(reply["paragraphs"]) < 3):
        raise ValueError("Invalid prose response structure")
    for text in [reply.get("title"), *reply["paragraphs"], *([reply["cta"]] if "cta" in reply else [])]:
        assert_reader_text(text)
        if len(text.strip().splitlines()) != 1:
            raise ValueError("Each prose field must be one reader-facing line")


def write_article(channel, topic, provider, existing_titles=(), *, documents=(), allow_length_repair=False):
    if channel not in ACTIVE or not topic_key(topic):
        raise ValueError("Use a nonempty topic for legends or science")
    all_titles = list(existing_titles)
    if topic_key(topic) in {topic_key(title) for title in all_titles}:
        raise ValueError("Topic already exists in the editorial corpus")
    channel_name, genre_prompt = GUIDES[channel]
    topic_words = set(topic_key(topic).split())
    related = sorted(all_titles, key=lambda title: -len(topic_words & set(topic_key(title).split())))[:5]
    reply = provider("write", {
        "channel": channel, "topic": topic, "existing_titles": related,
        "documents": [{**doc, "text": doc["text"][:4500]} for doc in documents[:3]],
        "instruction": f"{genre_prompt} Дай заголовок, зацепку, развитие и естественный финал. "
                       f"Обязательный объём {MIN_CHARS}–{MAX_CHARS} символов без пробелов. "
                       f"Целься в 4000–4500 символов без пробелов, раскрывай тему сценами или "
                       f"конкретными примерами. "
                       f"Сделай 6–8 достаточно содержательных абзацев, сохраняй естественный ритм. "
                       f"Подбери самостоятельный короткий заголовок. "
                       f"Не дублируй абзацы. Каждый элемент paragraphs — один готовый к чтению абзац без заголовка, переноса строки и разметки.",
    })
    title, blocks, cta = reply.get("title"), reply.get("paragraphs"), reply.get("cta")
    validate_prose_reply(reply)
    plan = reply.get("sections")
    if "cta" not in reply:
        layout = provider("article_layout", {"title": title, "paragraphs": blocks,
            "instruction": f"Сгруппируй абзацы по смыслу. Верни sections с номерами start от 1 и заголовками heading. "
                           f"Начни с 1, делай разделы по несколько абзацев. Дай отдельный короткий, связанный "
                           f"с материалом призыв подписаться на канал «{channel_name}» в поле cta."})
        cta, plan = layout["cta"], layout["sections"]
        assert_reader_text(cta)
    if (not isinstance(title, str) or not title.strip() or not isinstance(blocks, list)
            or len(blocks) < 3 or not all(isinstance(p, str) and p.strip() for p in blocks)
            or not isinstance(cta, str) or channel_name.casefold() not in cta.casefold()
            or not re.search(r"подпи[сш]", cta, re.I)):
        raise ValueError("Writer output needs a title, article paragraphs and a channel-specific CTA")
    if topic_key(title) in {topic_key(t) for t in all_titles}:
        raise ValueError("Generated title duplicates another article")
    cta_key = topic_key(cta)
    blocks = [p.strip() for p in blocks if topic_key(p) != cta_key] + [cta.strip()]
    if len(blocks) < 4:
        raise ValueError("Writer output needs at least three distinct prose paragraphs before the CTA")
    if len(set(map(topic_key, blocks))) != len(blocks):
        raise ValueError("Writer output has duplicate paragraphs")
    prose = " ".join(blocks)
    body = arrange(blocks, plan, provider, title)
    chars = count_characters(body)
    if len(re.findall(r"[а-яё]", prose, re.I)) < chars // 2:
        raise ValueError("Writer output must be predominantly Russian prose")
    if not allow_length_repair and length_finding(chars):
        raise ValueError(f"Draft has {chars} characters outside the range {MIN_CHARS}–{MAX_CHARS}")
    slug = channel + "-draft-" + sha256(topic_key(topic).encode()).hexdigest()[:16]
    return {"slug": slug, "channel": channel, "title": title.strip(), "status": "draft",
            "body_html": body, "layout_schema": SECTION_SCHEMA,
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
        corpus = sorted(CONTENT.glob("*.json"))
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
