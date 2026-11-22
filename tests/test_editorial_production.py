"""Exercise the production entry point with deterministic provider boundaries."""
from datetime import datetime, timedelta, timezone
import io
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from docx import Document
from PIL import Image

from src import (editorial_brain as brain, editorial_cinema, editorial_delivery,
                 editorial_images, editorial_providers, editorial_review)
from src.editorial_learning import CATEGORIES, evaluate_cases
from src.editorial_memory import Memory, digest, read_json, write
from src.editorial_providers import Blocked, ProviderError
from src.editorial_review import CTA, fact_evidence, parse_article, structural_checks


def draft(channel):
    sections = []
    for number in range(1, 8):
        paragraphs = [
            f"Раздел {channel} {number}. " + " ".join(f"подробность{number}{n}" for n in range(60))
        ]
        if channel == "science":
            # Numeric claims are tested independently. Use stable letters for the prose.
            paragraphs = [p.translate(str.maketrans("0123456789", "абвгдежзик")) for p in paragraphs]
        if number == 7:
            paragraphs.append("Если вам близка эта тема, подпишитесь на канал.")
        sections.extend([f"**Смысловая часть {chr(1040 + number)}**", *paragraphs])
    value = "\n\n".join(sections)
    # Use about 4000 visible characters with distinct paragraphs.
    return value.replace("подробность", "деталь")


def verdict(channel):
    if channel == "legends":
        return {"issues": [], "claims": []}
    claim = "Если вам близка эта тема, подпишитесь на канал."
    return {"issues": [], "claims": [{"claim": claim, "source": "S1",
                                      "quote": "Достаточно длинная подтверждающая цитата",
                                      "verdict": "supported"}]}


class PrivateStub:
    def checkpoint(self):
        pass

    def notify(self, day, status):
        return "https://github.com/example/private/issues/1"

    def review_url(self, day):
        return "https://github.com/example/private/tree/main/" + day


class ProductionTests(unittest.TestCase):
    def test_two_channels_roundtrip_and_idempotency(self):
        calls = []
        def model(provider, prompt, tokens):
            channel = "science" if any(w in prompt for w in ("научпопа", "Тема: Экосистема", "Канал: science",
                                                              "Скомбинируй оба типа")) else "legends"
            calls.append(provider)
            if "Предложи 5" in prompt:
                title = "Экосистема горячих источников в Йеллоустоне" if channel == "science" else "Зимой из фонтана звучал чужой голос"
                text = json.dumps({"topics": [{"title": title, "angle": "Конкретный замысел",
                    "sources": ["https://nps.gov/a", "https://usgs.gov/b"]}]})
            elif "Верни JSON" in prompt and "subject" in prompt:
                text = json.dumps({"subject": "water and environmental detail",
                    "images": [{"section": i, "kind": "photo" if channel == "science" and i % 2 else "illustration",
                                "subject": f"distinct subject {i}", "brief": f"Detailed scene description {i}",
                                "query": "hot spring photograph"} for i in range(1, 8)]})
            elif "Проверь конкретные недостатки" in prompt:
                text = json.dumps(verdict(channel))
            else:
                text = draft(channel)
            return {"text": text, "model": provider, "usage": {"total_tokens": 1}}

        def inspections(raw, *_):
            article = parse_article(raw)
            return {"article": article, "text_sha256": digest(brain.canonical(article)), "issues": [],
                    **{key: {"status": "completed"} for key in ("model_a", "grammar", "editorial")},
                    "facts": {"status": "completed", "evidence": []}}

        def asset(scene, subject, path, *_):
            content = io.BytesIO()
            shade = scene["section"] * 28 + (7 if scene["kind"] == "photo" else 0)
            Image.new("RGB", (580, 490), (shade, 10, 220)).save(content, "JPEG")
            raw = content.getvalue()
            write(path, raw)
            return {"kind": scene["kind"], "section": scene["section"], "file": path.name,
                    "scene": scene["subject"], "cover_score": 1.0, "sha256": digest(raw),
                    "rights_status": "rights_review_pending",
                    "source_page": "https://commons.wikimedia.org/wiki/File:Example"}

        with TemporaryDirectory() as directory, patch.object(brain, "text_call", model), \
                patch.object(brain, "qa", inspections), patch.object(brain, "photo", asset), \
                patch.object(brain, "generated", asset), patch.object(brain, "sources", lambda urls, domains:
                    [{"status": "accessible", "url": url, "text": "Доказательство"} for url in urls]):
            root = Path(directory)
            result = brain.execute("daily", root, PrivateStub(), validation_window=120)
            self.assertEqual(result["articles"], {"legends-01": "rights_review_pending",
                                                  "science-01": "rights_review_pending"})
            state = read_json(root / result["day"] / "state.json")
            self.assertTrue(any(task["operation"] == "full-edit" and task["executor"] == "openrouter"
                                for task in state["tasks"].values()))
            for task in state["tasks"].values():
                self.assertEqual([x["status"] for x in task["events"]],
                                 ["created", "assigned", "running", "validating", "completed"])
            packet = root / result["day"] / "review"
            science = packet / "science-01"
            self.assertEqual(len(Document(science / "Статья.docx").inline_shapes), 7)
            self.assertEqual(digest((science / "Текст для SHA-256.txt").read_bytes()),
                             state["articles"]["science-01"]["text_sha256"])
            report = read_json(science / "Статус и проверки.json")
            self.assertEqual({row["kind"] for row in report["images"]}, {"photo", "illustration"})
            self.assertIn("document_roundtrip", report)
            self.assertEqual(brain.execute("review", root, PrivateStub())["status"],
                             "owner_notification_created")
            from cryptography.hazmat.primitives.asymmetric import x25519
            from cryptography.hazmat.primitives.serialization import (
                Encoding, PrivateFormat, NoEncryption, PublicFormat,
            )
            key = x25519.X25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            key_path = root / "owner.pem"
            write(key_path, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            editor = root / "editorial"
            protected = root / "sealed"
            stage = root / result["day"]
            editor.mkdir()
            shutil.copytree(stage, editor / result["day"])
            with patch.dict("os.environ", {"EDITORIAL_REVIEW_PUBLIC_KEY": public,
                                           "EDITORIAL_STATE_KEY_HEX": "ab" * 32}):
                actual = editorial_delivery.save_outputs(editor, result["day"], protected)
                self.assertEqual(actual["delivery"], "delivery_integration_pending")
                self.assertEqual({x.name for x in protected.iterdir()}, {"checkpoint.enc", "owner-review.enc"})
                state_wire = (protected / "checkpoint.enc").read_bytes()
                self.assertEqual(editorial_delivery.unseal_state(state_wire)[:2], b"PK")
                with self.assertRaisesRegex(Blocked, "authentication_failed"):
                    editorial_delivery.unseal_state(state_wire[:-1] + bytes([state_wire[-1] ^ 1]))
            delivered = editorial_delivery.retrieve(root / "authorized", key_path, day=result["day"],
                                                     artifact=protected / "owner-review.enc")
            self.assertEqual(delivered["status"], "verified_files_available")
            self.assertTrue((root / "authorized" / "science-01" / "Статья.docx").is_file())
            self.assertTrue((root / "authorized" / "science-01" / "Статья.html").is_file())
            self.assertEqual(len(list((root / "authorized" / "science-01" / "images").glob("*.jpg"))), 7)

            previous = len(calls)
            brain.execute("daily", root, PrivateStub(), validation_window=120)
            self.assertEqual(len(calls), previous)

    def test_existing_truman_show_evidence_and_rights_fail_closed(self):
        repo = Path(brain.ROOT)
        existing = read_json(repo / "content/articles/cinema-01-truman-show-real-photos.json")
        ref = read_json(repo / "config/movie_reference_index.json")[existing["slug"]]
        self.assertEqual(ref["imdb_id"], "tt0120382")
        official = "https://www.paramount.com/movies/the-truman-show"
        plan = {"film_title": "Шоу Трумана", "year": 1998, "format": "retelling",
                "spoilers": True, "sources": [official]}
        def film_resolver(action, payload):
            self.assertEqual(payload["topic"], "«Шоу Трумана» (1998)")
            return {"film": {"title": "Шоу Трумана", "original_title": ref["film_title"],
                "qid": "Q214801", "imdb_id": ref["imdb_id"], "years": [ref["original_year"]],
                "cast": ["Jim Carrey"], "directors": [ref["director"]]},
                "documents": [{"url": "https://www.wikidata.org/wiki/Q214801",
                    "text": "The Truman Show. Peter Weir. 1998."}]}
        details = editorial_cinema.evidence(plan, {}, resolver=film_resolver, fetch=lambda *args: [
            {"status": "accessible", "url": official, "text": "The Truman Show. Jim Carrey stars."}])
        self.assertEqual(details["format"], "review")
        self.assertFalse(details["spoilers"])
        self.assertFalse(details["detailed_plot_evidence"])
        self.assertEqual(details["film"]["imdb_id"], ref["imdb_id"])
        with self.assertRaisesRegex(Blocked, "film_independent_source_unavailable"):
            editorial_cinema.evidence(plan, {}, resolver=film_resolver, fetch=lambda *args: [])
        class Index:
            class memory:
                time_left = staticmethod(lambda: 1000)
        with self.assertRaisesRegex(Blocked, "rights_review_pending"):
            editorial_cinema.stills({"subject": "Truman in Seahaven"}, "Truman Show", None,
                                    Index(), details["film"], {})
        self.assertEqual(brain.settings()["articles_per_channel"]["cinema"], 0)

    def test_unsupported_claim_cannot_be_covered_by_an_unrelated_fragment(self):
        text = "Наблюдение началось в 1998 году. Объект находился рядом."
        actual, issue = fact_evidence(text, {"claims": [
            {"claim": "Объект находился рядом", "source": "S1", "quote": "Достоверная цитата о положении объекта",
             "verdict": "supported"}]},
            [{"url": "https://example.org", "status": "accessible",
              "text": "Достоверная цитата о положении объекта."}])
        self.assertEqual(len(actual), 1)
        self.assertTrue(any(line.startswith("unsupported_numeric_claim") for line in issue))

    def test_revision_cap_and_rejected_versions(self):
        def report(raw, **details):
            article = parse_article(raw)
            review = {"text_sha256": digest(brain.canonical(article)),
                      "issues": ["logic: missing explanation"], "chars_without_spaces": 4000,
                      **{k: {"status": "completed"} for k in ("editorial", "grammar", "facts", "model_a")}}
            return {"raw": raw, "text_sha256": review["text_sha256"], "report": review, "article": article}

        with TemporaryDirectory() as directory:
            config = brain.settings()
            memory = Memory(Path(directory), "2026-11-22", datetime.now(timezone.utc) + timedelta(hours=2), config)
            article = brain.Article(memory, "legends", 1, ({}, [], set()), None)
            article.current = report("**Начало**\n\nИсходная версия.")
            before = article.current["text_sha256"]
            candidates = (f"**Начало**\n\nКандидат {i}." for i in range(5))
            with patch.object(article, "model", side_effect=lambda *a, **k: next(candidates)) as edit, \
                    patch.object(article, "inspect", side_effect=lambda candidate, reference: report(candidate["raw"])):
                article.revise("Сюжет")
            self.assertEqual(edit.call_count, 5)
            self.assertEqual(article.assistant, "assistant_handoff_pending")
            self.assertEqual(article.current["text_sha256"], before)
            self.assertTrue(all(row["status"] == "rejected" and row["after"] != before for row in article.attempts))

    def test_actual_check_runner_reports_incomplete_on_unavailable_tools(self):
        value = draft("legends")
        checker = {"status": "error", "reason": "child process not found"}
        with patch.object(editorial_review, "measure_text", return_value={"modernbert": checker}), \
                patch.object(editorial_review, "grammar",
                             return_value={"status": "unavailable", "reason": "LanguageTool"}):
            report = editorial_review.qa(value, "legends", [], [], lambda *a: {"issues": []},
                                         lambda: 3600)
        self.assertEqual(report["detectors"]["modernbert"]["status"], "unavailable")
        self.assertEqual(report["model_a"]["status"], "incomplete")
        self.assertEqual(report["grammar"]["status"], "unavailable")
        self.assertEqual(report["text_sha256"], digest(brain.canonical(parse_article(value))))

    def test_free_router_rejects_metered_catalog_and_empty_response(self):
        router = editorial_providers
        router.require_free_router.cache_clear()
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "fixture-token"}), \
                patch.object(router, "request", return_value=json.dumps({"data": [{
                    "id": router.NEMOTRON, "pricing": {"prompt": "0", "completion": "0.001"}}]}).encode()):
            with self.assertRaisesRegex(ProviderError, "zero_price_not_confirmed"):
                router.require_free_router()
        router.require_free_router.cache_clear()
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "fixture-token"}), \
                patch.object(router, "require_free_router"), \
                patch.object(router, "request", return_value=b'{"choices":[]}'):
            with self.assertRaisesRegex(ProviderError, "empty_visible_content"):
                router.text_call("openrouter", "Контроль")

    def test_cover_selection_uses_scores_and_retains_illustrated_sections(self):
        pictures = [{"file": "first.jpg", "section": 1, "cover_score": .1, "sha256": "a"},
                    {"file": "only-for-this-section.jpg", "section": 2, "cover_score": .9, "sha256": "b"},
                    {"file": "cover.jpg", "section": 3, "cover_score": .8, "sha256": "c"},
                    {"file": "other.jpg", "section": 3, "cover_score": .3, "sha256": "d"}]
        self.assertEqual(editorial_images.select_cover(pictures), "cover.jpg")

    def test_corrupt_image_cannot_be_accepted(self):
        with TemporaryDirectory() as directory:
            memory = Memory(Path(directory), "2026-11-22", datetime.now(timezone.utc), brain.settings())
            index = editorial_images.ImageIndex(memory, [])
            with self.assertRaises(OSError):
                index.save(b"not an image", {}, {"subject": "Figure"}, "Theme", Path(directory) / "a.jpg")

    def test_cta_and_structure(self):
        self.assertTrue(all(CTA.search(x) for x in ("Подписывайтесь!", "Подпишитесь на канал.",
                                                    "Вы можете подписаться.", "Следите за каналом.")))
        self.assertFalse(CTA.search("Тут есть канал и подписи."))
        report = structural_checks(parse_article("Вступление\n\n**Сцена**\n\nТекст"), [])
        self.assertIn("missing_cta", report["issues"])
        self.assertIn("section_structure", report["issues"])

    def test_429_stops_only_affected_provider_and_persists(self):
        with TemporaryDirectory() as directory:
            cutoff = datetime.now(timezone.utc) + timedelta(hours=2)
            path = Path(directory)
            memory = Memory(path, "2026-11-22", cutoff, brain.settings())
            with self.assertRaisesRegex(Blocked, "http_429"):
                memory.task("legends-01", "image", "klein", {"n": 1}, lambda task:
                    memory.call(task, "legends-01", "klein", lambda: fail("http_429_quota_or_rate_limit")))
            restarted = Memory(path, "2026-11-22", cutoff, brain.settings(), retry=True)
            with self.assertRaisesRegex(Blocked, "provider_stopped"):
                restarted.call("t2", "legends-01", "klein", lambda: self.fail("repeated an exhausted route"))
            self.assertEqual(restarted.call("t3", "science-01", "groq", lambda: {"text": "ok"})["text"], "ok")
            self.assertEqual(len(restarted.data["calls"]), 2)

    def test_deadline_and_unconnected_review(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            memory = Memory(path, "2026-11-22", datetime.now(timezone.utc) - timedelta(seconds=30), brain.settings())
            with self.assertRaisesRegex(Blocked, "production_deadline"):
                memory.task("science-01", "draft", "groq", {},
                            lambda _: self.fail("late provider call"))
            result = brain.review(memory)
            self.assertEqual(result["status"], "delivery_integration_pending")
            self.assertFalse(brain.status(memory)["publication_authorized"])

    def test_verified_learning_detects_regression_and_overlap(self):
        def case(i, partition, regression=False):
            return {"channel": "legends", "issue": "missing_cta", "article_id": f"article-{i}",
                    "owner_verified": True, "provenance": "verified owner review", "partition": partition,
                    "candidate_instruction": CATEGORIES["missing_cta"], "baseline_resolved": False,
                    "candidate_resolved": True, "regression": regression,
                    **{k: digest(f"{i}.{k}") for k in ("text_sha256", "source_sha256",
                                                       "baseline_sha256", "candidate_sha256")}}
        development = [case(i, "development") for i in range(20)]
        holdout = [case(i, "holdout") for i in range(20, 25)]
        report = evaluate_cases(development + holdout + [case(2, "holdout")], 20)
        self.assertEqual(report["proposals"][0]["status"], "candidate_improved")
        self.assertEqual(report["proposals"][0]["held_out_cases"], 5)
        holdout[0]["regression"] = True
        self.assertEqual(evaluate_cases(development + holdout, 20)["proposals"][0]["status"],
                         "rejected_regression")


def fail(code):
    raise ProviderError(code, stop=True)


if __name__ == "__main__":
    unittest.main()
