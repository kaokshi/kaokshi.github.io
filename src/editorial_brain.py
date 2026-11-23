"""Production entry point for preparation and private review of Zen drafts."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from time import sleep
from zoneinfo import ZoneInfo
import fcntl

from . import editorial_cinema as cinema
from .editorial_alt_models import gemini_pilot, groq_gemini_klein_pilot, groq_ovh_klein_pilot, ovh_finalize, ovh_recover, ovh_trial, without_openrouter
from .editorial_ai_check import REQUIRED_CHECKS, extract_text
from .editorial_images import ImageIndex, generated, photo, similarity, validate_plan
from .editorial_layout import layout_issues
from .editorial_groq_budget import forecast
from .editorial_learning import learn, propose_pull_requests
from .editorial_memory import Memory, PrivateGit, StorageError, digest, now, read_json, write, write_json
from .editorial_no_repeat import topic_duplicate_reason, words
from .editorial_package import package, zip_packet
from .editorial_subscription import CHANNELS as SUBSCRIPTION_CHANNELS
from .editorial_providers import Blocked, MODELS, ProviderError, gemini_limit, gemini_model, gemini_text, klein, text_call
from .editorial_review import RULES, canonical, improves, parse_article, qa, source_context, sources, structural_checks

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
CONTENT = ROOT / "content" / "articles"
PROMPT_VERSION = "production-2026-11-23-v2"
CHANNELS = ("legends", "science", "cinema")


def json_reply(response):
    text = response["text"].strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.partition("\n")[2].removesuffix("```").strip()
    try:
        value = json.loads(text)
    except ValueError:
        raise Blocked("model_json_invalid") from None
    if not isinstance(value, dict):
        raise Blocked("model_json_not_object")
    return value


def archive(root, exclude_day=None):
    titles = {c: [] for c in CHANNELS}
    prose = []
    paths = set()
    for path in sorted(CONTENT.glob("*.json")):
        row = read_json(path)
        title = row.get("title", "")
        if row.get("channel") in titles:
            titles[row["channel"]].append(title)
        prose.append((title, words(extract_text(row.get("body_html", "")))))
        for name in row.get("image_files", []):
            asset = ROOT / name
            if asset.is_file() and asset.resolve().is_relative_to(ROOT / "content"):
                paths.add(asset)
    for path in sorted(root.glob("????-??-??/state.json")):
        if path.parent.name == exclude_day:
            continue
        row = read_json(path)
        for article_id, entry in row["articles"].items():
            if title := entry.get("title"):
                titles[article_id.split("-")[0]].append(title)
                if entry.get("text_sha256"):
                    source = path.parent / article_id / "versions" / (entry["text_sha256"] + ".txt")
                    if source.exists():
                        prose.append((title, words(source.read_text(encoding="utf-8"))))
    return titles, prose, paths


class Article:
    def __init__(self, memory, channel, index, prior, image_index):
        self.memory = memory
        self.channel = channel
        self.id = f"{channel}-{index:02d}"
        self.folder = memory.folder / self.id
        self.prior = prior
        self.image_index = image_index
        self.current = None
        self.title, self.records, self.pictures, self.blockers = "", [], [], []
        self.film = None
        self.cinema_registry = read_json(CONFIG / "editorial_cinema.json", {})
        self.attempts = []
        self.assistant = "not_required"
        self.config = memory.config
        self.escalation = read_json(CONFIG / "ai_revision_policy.json")["editorial_escalation"]

    def task(self, stage, executor, inputs, fn, **kwargs):
        return self.memory.task(self.id, stage, executor, inputs, fn, **kwargs)

    def model(self, stage, provider, prompt, *, validate=None, tokens=None, metadata=None, repair=False, tried=()):
        from .editorial_alt_models import enabled as alternate_enabled, require_isolated
        if alternate_enabled():
            require_isolated()
            if ovh_trial() or groq_ovh_klein_pilot():
                raise Blocked("ovh_removed_from_project")
            if provider in {"ovh", "ovh-gptoss"}:
                raise Blocked("ovh_removed_from_project")
            if groq_gemini_klein_pilot():
                if provider not in {"groq", "gemini"}:
                    raise Blocked("pilot_text_provider_not_allowed")
                if provider == "groq" and stage.startswith(("editorial-audit", "full-edit", "repair-")):
                    return self.model(stage, "gemini", prompt, validate=validate,
                                      tokens=tokens, metadata=metadata, repair=repair)
            elif provider == "groq":
                # Legacy Kilo-only isolated tests remain separate from this pilot.
                route = "openrouter" if stage.startswith(("editorial-audit", "repair-", "full-edit")) else "kilo"
                return self.model(stage, route, prompt, validate=validate,
                                  tokens=tokens, metadata=metadata, repair=repair)
        if provider == "groq" and os.environ.get("EDITORIAL_GROQ_FALLBACK_ONLY") == "true":
            # Authenticated, zero-price OpenRouter catalog was checked by the workflow.
            # Do not contact Groq at all when its daily capacity is unavailable.
            return self.model(stage, "openrouter", prompt, validate=validate,
                              tokens=max(7800, tokens or 0),
                              metadata={**(metadata or {}), "fallback_from": "groq",
                                        "fallback_reason": "groq_preflight_capacity_unavailable"},
                              repair=repair)
        isolated_edit = (alternate_enabled() and provider == "openrouter"
                         and stage.startswith(("full-edit", "repair-")))
        if tokens is None:
            tokens = 3700 if provider == "groq" else 7800
        if without_openrouter() and not ovh_trial() and provider in {"ovh", "ovh-gptoss", "kilo-review"}:
            tokens = min(tokens, 7800)

        def send(task, ceiling):
            if provider == "gemini":
                callback = lambda: gemini_text(prompt, min(ceiling, 8192),
                                               response_json=validate is not None,
                                               fallback_model=stage.endswith("-retry"))
            else:
                callback = lambda: text_call(provider, prompt, ceiling)
            if provider == "groq":
                return self.memory.call_groq(task, self.id, stage, prompt, ceiling, callback,
                                             repair=repair or stage.startswith("repair-"))
            if isolated_edit:
                return self.memory.call(task, self.id, provider, callback, editorial_edit=True)
            return self.memory.call(task, self.id, provider, callback)

        def execute(task):
            seen = self.memory.response(task)
            if seen is not None:
                return seen
            try:
                return send(task, tokens)
            except ProviderError as exc:
                # Never shrink a required article merely to fit a provider window.
                # Gemini retries must be separate persisted, quota-counted tasks.
                # A silent resend inside one reservation understates real API use.
                if isolated_edit or provider == "gemini" or exc.retry_after is None:
                    raise
                if ovh_trial() and not ((ovh_recover() or groq_ovh_klein_pilot())
                                        and exc.telemetry.get("http_status") == 429):
                    raise
                delay = max(70, exc.retry_after) if provider == "ovh" and (
                    ovh_recover() or groq_ovh_klein_pilot()) else exc.retry_after
                if self.memory.time_left() < delay + 165:
                    raise
                if alternate_enabled():
                    sleep(delay)
                return send(task, tokens)

        inputs = {"model": ("gemini-3.6-flash" if provider == "gemini" and
                            stage.endswith("-retry") and gemini_model() == "gemini-3.5-flash"
                            else gemini_model()) if provider == "gemini" else MODELS[provider],
                  "prompt_version": PROMPT_VERSION,
                  "prompt_sha256": digest(prompt), **(metadata or {})}
        if isinstance(self.memory, Memory) and self.memory.retry:
            with self.memory.lock:
                for row in self.memory.data["tasks"].values():
                    if (row["article"] != self.id or row["operation"] != stage
                            or row["executor"] != provider or row["status"] != "completed"):
                        continue
                    previous = row["inputs"]
                    expected = {**inputs}
                    if provider == "kilo" and previous.get("model") == "kilo-auto/free":
                        expected["model"] = "kilo-auto/free"
                    if stage.endswith("-writer-fallback") and "fallback_reason" in previous:
                        expected["fallback_reason"] = previous["fallback_reason"]
                    if previous == expected:
                        inputs = previous
                        break
        try:
            result = self.task(stage, provider, inputs, execute, minimum_seconds=130,
                               validator=(lambda response: validate(json_reply(response))) if validate else None)
        except Blocked as exc:
            if ovh_trial() or groq_gemini_klein_pilot():
                raise
            # Route a quota-stopped Groq stage to a separately recorded, verified-free
            # OpenRouter task. Do not hide authentication failures, malformed answers,
            # incomplete previous requests or any editorial validation errors.
            reason = str(exc)
            quota_blockers = {
                "call_budget_exhausted",
                "groq_quota_wait_exceeds_deadline",
                "groq_daily_quota_reset_pending",
                "groq_editorial_budget_exhausted",
                "groq_daily_budget_exhausted",
                "groq_editorial_reserve_protected",
                "http_429_groq_daily_quota",
                "provider_stopped_http_429_groq_daily_quota",
            }
            if alternate_enabled() and provider == "kilo":
                # No fallback may contact OVH after the owner removed it.
                # Preserve blocked stage without pretending that an editor
                # can replace unavailable independent authoring.
                raise
            if (provider != "groq" or stage.startswith("editorial-audit")
                    or reason not in quota_blockers):
                raise
            return self.model(stage, "openrouter", prompt, validate=validate,
                              tokens=max(7800, tokens),
                              metadata={**(metadata or {}), "fallback_from": "groq",
                                        "fallback_reason": reason}, repair=repair)
        return json_reply(result) if validate else result["text"]

    def source_seed(self, history):
        registry = read_json(CONFIG / "editorial_science_sources.json", [])
        if not registry:
            return None
        rotation = (date.fromisoformat(self.memory.day).toordinal() + int(self.id[-2:]) - 2) % len(registry)
        for row in registry[rotation:] + registry[:rotation]:
            if topic_duplicate_reason(self.channel, row["title"], history):
                continue
            with self.memory.lock:
                titles = [entry.get("title", "") for key, entry in self.memory.data["articles"].items()
                          if key != self.id and key.startswith("science-")]
            if topic_duplicate_reason(self.channel, row["title"], titles):
                continue
            urls = row["sources"]
            records = self.task("sources", "primary-sources", {"urls": urls}, lambda _: sources(
                urls, self.config["science_source_domains"]), minimum_seconds=85)
            if len([r for r in records if r["status"] == "accessible"]) >= 2:
                result = {**row, "records": records}
                self.memory.article(self.id, title=row["title"], topic_plan=result, channel=self.channel)
                return result
        return None

    def plan(self):
        history = self.prior[0][self.channel]
        existing = self.memory.data["articles"].get(self.id, {})
        if existing.get("title"):
            return existing["topic_plan"]
        if (self.channel == "cinema" and groq_gemini_klein_pilot()
                and os.environ.get("EDITORIAL_CINEMA_ONLY_SEED") == "true"):
            # A validated factual film selection replaces wasteful trial-and-error
            # theme calls. All actual article prose is still produced only by Groq.
            selected = {
                "title": "«Пленницы» (2013): обзор без спойлеров",
                "angle": "Как тревожная семейная история раскрывает конфликт "
                         "между отчаянием, законом и моральным выбором",
                "film_title": "Пленницы", "year": 2013, "format": "review",
                "spoilers": False,
                "sources": [
                    "https://www.rottentomatoes.com/m/prisoners_2013",
                    "https://www.rogerebert.com/reviews/prisoners-2013",
                    "https://www.theguardian.com/film/2013/sep/26/prisoners-review",
                ],
            }
            if topic_duplicate_reason(self.channel, selected["title"], history):
                raise Blocked("unique_topic_or_verifiable_sources_missing")
            checked = self.task(
                "film-evidence", "wikidata-and-film-sources",
                {"plan": selected, "research_version": "independent-reviews-20261123"},
                lambda _: cinema.evidence(selected, self.cinema_registry),
                minimum_seconds=125)
            topic = {**selected, **checked}
            self.memory.article(self.id, title=selected["title"],
                                topic_plan=topic, channel=self.channel)
            return topic
        if self.channel == "science":
            seed = self.source_seed(history)
            if seed:
                return seed
            if ovh_trial():
                raise Blocked("ovh_trial_no_verified_science_seed")
        subject = ("оригинальной городской мистики, с уникальным сюжетом и развязкой" if self.channel == "legends"
                   else "научпопа о наблюдаемом природном явлении, приборе или научном объекте")
        if self.channel == "cinema":
            subject = "канала «Кадр за Кадром» с уникальным фильмом"
        prompt = (f"Предложи 5 разных тем для {subject}. Для каждой дай title, angle (конкретный "
                  "замысел), sources (для научпопа 2–4 точные HTTPS-ссылки на первичные материалы "
                  "научных организаций, для мистики пустой массив). Ответь JSON {\"topics\":[...]}. "
                  f"Избегай похожих тем и сюжетов из архива: {json.dumps(history[-150:], ensure_ascii=False)}. "
                  f"Научные домены: {', '.join(self.config['science_source_domains'])}.")
        if self.channel == "cinema":
            signals = self.task("film-interests", "wikimedia", {}, lambda _: cinema.interest_signals())
            releases = self.task("film-releases", "wikidata", {}, lambda _: cinema.recent_releases())
            prompt += (" Для каждой темы верни дополнительно film_title, year, qid (если известен), "
                       "format (retelling, review, critique или plot_analysis), spoilers (boolean). "
                       "Дай ссылки на студийные или независимые источники о конкретном фильме. "
                       "Не угадывай QID: если не уверен в нём, оставь поле пустым; "
                       "независимый каталог проверит фильм сам. "
                       f"Публичные сигналы интереса: {json.dumps(signals, ensure_ascii=False)}. "
                       f"Недавние релизы: {json.dumps(releases, ensure_ascii=False)}.")
        from .editorial_alt_models import enabled as alternative_test
        topic_tokens = 1800 if groq_gemini_klein_pilot() else 6000 if alternative_test() else 3200
        for shift in range(2):
            if shift and not alternative_test() and os.environ.get("EDITORIAL_GROQ_FALLBACK_ONLY") != "true":
                with self.memory.lock:
                    projection = groq_plan(self.memory)
                if (projection["groq_usage"]["accounted_tokens"] >= self.config["groq_budget"]["target_daily_tokens"][1]
                        or not all(projection[key] for key in ("fits_editorial_budget",
                            "fits_observed_organization_headroom", "deadline_estimate_fits"))):
                    raise Blocked("groq_optional_topic_retry_deferred")
            # When Groq was preflight-blocked and the zero-price OpenRouter
            # route was separately verified, only the latter's real request
            # envelope and the common deadline may gate this retry.
            def is_valid(plan):
                if not isinstance(plan.get("topics"), list):
                    raise Blocked("topic_list_missing")
            topic_prompt = prompt + (f" В этот раз выбери иные предметы для набора {shift}." if shift else "")
            try:
                saved_topic = None
                if groq_gemini_klein_pilot() and self.memory.retry:
                    # Reuse the original model-made candidate list even if
                    # non-semantic prompt formatting changed between patches.
                    # No new model request is needed to retry independent
                    # verification of the same film candidates.
                    with self.memory.lock:
                        saved_topic = next((
                            task["result"]
                            for task in self.memory.data["tasks"].values()
                            if task["article"] == self.id
                            and task["operation"] == f"topics-{shift + 1}"
                            and task["status"] == "completed"
                            and isinstance(task.get("result"), dict)
                            and isinstance(task["result"].get("text"), str)
                        ), None)
                result = (json_reply(saved_topic) if saved_topic is not None
                          else self.model(f"topics-{shift + 1}", "groq", topic_prompt,
                                          validate=is_valid, tokens=topic_tokens))
                is_valid(result)
            except Blocked as exc:
                # Retry malformed JSON as a new, metered model task. Never
                # insert any assistant-authored topic or other creative data.
                if str(exc) not in {"model_json_invalid", "model_json_not_object",
                                    "topic_list_missing"}:
                    raise
                strict_prompt = (topic_prompt + " Ответь строго одним JSON-объектом "
                                 "с массивом topics; без Markdown, пояснений или "
                                 "текста до/после JSON. Не придумывай идентификаторы "
                                 "каталога фильмов, если не уверен.")
                result = self.model(f"topics-json-retry-{shift + 1}", "groq",
                                    strict_prompt, validate=is_valid, tokens=topic_tokens)
            for row in result["topics"][:8]:
                if not isinstance(row, dict):
                    continue
                title, angle = row.get("title"), row.get("angle")
                if (not isinstance(title, str) or not 8 <= len(title) <= 180
                        or not isinstance(angle, str) or not angle.strip()):
                    continue
                if topic_duplicate_reason(self.channel, title, history):
                    continue
                if self.channel == "science":
                    urls = row.get("sources")
                    if not isinstance(urls, list) or any(not isinstance(x, str) for x in urls):
                        continue
                    records = self.task("sources", "primary-sources", {"urls": urls[:5]},
                                        lambda _: sources(urls, self.config["science_source_domains"]),
                                        minimum_seconds=85)
                    if len([r for r in records if r["status"] == "accessible"]) < 2:
                        continue
                    row = {**row, "records": records}
                if self.channel == "cinema":
                    try:
                        film = self.task("film-evidence", "wikidata-and-film-sources",
                            {"plan": row, "research_version": "wikimedia-ua-20261123"},
                            lambda _: cinema.evidence(row, self.cinema_registry), minimum_seconds=125)
                    except Blocked:
                        continue
                    identity = film["film"]
                    if any(topic_duplicate_reason("cinema", name, history)
                           for name in (identity["title"], identity["original_title"])):
                        continue
                    row = {**row, **film}
                with self.memory.lock:
                    current = [x["title"] for key, x in self.memory.data["articles"].items()
                               if x.get("title") and key != self.id and key.startswith(self.channel + "-")]
                    if topic_duplicate_reason(self.channel, title, current):
                        continue
                    if self.channel == "cinema" and any(
                        (entry.get("topic_plan", {}).get("film") or {}).get("qid") == row["film"]["qid"]
                        for entry in self.memory.data["articles"].values()
                    ):
                        continue
                    self.memory.article(self.id, title=title, topic_plan=row, channel=self.channel)
                return row
        raise Blocked("unique_topic_or_verifiable_sources_missing")

    def remember_version(self, raw, report=None):
        structured = parse_article(raw)
        text = canonical(structured)
        sha = digest(text)
        versions = self.folder / "versions"
        original = versions / (sha + ".md")
        if not original.exists():
            write(original, raw.encode("utf-8"))
            write(versions / (sha + ".txt"), text.encode("utf-8"))
        if report:
            write_json(versions / (sha + ".json"), report)
        self.memory.save()
        return {"raw": raw, "article": structured, "text_sha256": sha, "report": report}

    def inspect(self, candidate, reference=None, *, repair=False, submitted_audit=None):
        raw, sha = candidate["raw"], candidate["text_sha256"]
        pilot_resume = groq_ovh_klein_pilot() and self.memory.retry
        facts = source_context(self.records, chars_per_source=6000 if ovh_finalize() or pilot_resume else 2000)
        def audit(text, language_issues):
            if submitted_audit is not None:
                return submitted_audit
            prompt = ("Проверь конкретные недостатки текста. Оцени связность, оригинальность, "
                      "смысловые повторы, естественность, достаточность раскрытия и сохранность важных "
                      "смысловых элементов. Учитывай замечания LanguageTool, оставляй только реальные ошибки. "
                      "Верни JSON {\"issues\":[\"category: конкретное замечание и точное место\"],\"claims\":[]}. "
                      "Категории: language, logic, coverage, loss_of_meaning, fiction_claim, redundancy. "
                      "Если проблем нет, верни пустой список issues. Для научпопа перечисли все проверяемые "
                      "фактические утверждения, в том числе числа и причинность. Для каждого верни claim "
                      "(дословный фрагмент статьи), source (S1, S2 и т. д.), quote (дословная подтверждающая "
                      "цитата источника), verdict (supported или unsupported). Доказательство должно "
                      "подтверждать утверждение по смыслу. У неподтверждённых claims укажи unsupported."
                      f"\nКанал: {self.channel}\nСТАТЬЯ:\n{text}\nИСТОЧНИКИ:\n{facts}"
                      f"\nЯЗЫКОВЫЕ СИГНАЛЫ:\n{json.dumps(language_issues[:25], ensure_ascii=False)}")
            if self.film:
                prompt += ("\nФИЛЬМ: " + json.dumps(self.film, ensure_ascii=False)
                           + "\nУбедись, что режим спойлеров соблюдён. Проверяй сцены, хронологию, "
                           "точность имён, режиссёра, роли, финал и год только по источникам. "
                           "Отдельно помечай оценочные высказывания.")
            if reason := candidate["article"].get("length_exception_reason"):
                prompt += ("\nОБОСНОВАНИЕ ОБЪЁМА: " + reason
                           + "\nЕсли статья свыше 6000 знаков, проверь конкретность и необходимость обоснования.")
            if reference:
                prompt += "\nПРЕДЫДУЩАЯ ВЕРСИЯ ДЛЯ СРАВНЕНИЯ:\n" + canonical(reference["article"])
            if ovh_finalize():
                prompt += ("\nОтветь только JSON. Для claims копируй непрерывный отрывок из "
                           "СТАТЬИ и непрерывную цитату на языке источника из блока ИСТОЧНИКИ. "
                           "Цитата должна подтверждать числа в той же записи, что и в статье. "
                           "Не добавляй план иллюстраций, не переписывай статью.")
                return self.model("editorial-audit", "ovh", prompt, validate=lambda x: None,
                                  metadata={"text_sha256": sha}, tokens=11000)
            if ovh_trial():
                count = len(candidate["article"]["sections"])
                prompt += (f'\nВ тот же JSON включи image_plan с subject и массивом images. '
                           f'Укажи ровно {count + 1} различных изображений: два для первого раздела '
                           'и по одному для остальных. Каждая запись: section (номер раздела), '
                           'kind (строго photo или illustration), subject, brief, query (английские строки). '
                           'Включи оба вида. Для Wikimedia Commons выбирай широко документированные '
                           'объекты и запросы по содержанию раздела. Не придумывай URL и лицензии.')
                if groq_ovh_klein_pilot():
                    sections = [1, 1, *range(2, count + 1)]
                    prompt += (f" Поле image_plan.images обязано иметь ровно {count + 1} элементов. "
                               f"Номера section в порядке ответа: {sections}. Две записи illustration "
                               "для Klein, остальные — photo с английским запросом для Commons. "
                               "Не включай варианты и пояснения. Цитаты из источников дословные, "
                               "на языке источника, не короче 18 знаков.")
                provider = "gemini" if gemini_pilot() else "ovh"
                result = self.model("editorial-audit", provider, prompt, validate=lambda x: None,
                                    metadata={"text_sha256": sha}, tokens=11000 if groq_ovh_klein_pilot() else 7800)
                self.memory.article(self.id, trial_image_plan=result.get("image_plan"))
                return result
            last_error = None
            from .editorial_alt_models import enabled as alternative_test
            providers = (("gemini",) if groq_gemini_klein_pilot() else
                         ("ovh-gptoss", "kilo-review") if without_openrouter() else
                         ("openrouter",) if alternative_test() else ("groq", "openrouter"))
            for provider in providers:
                try:
                    return self.model("editorial-audit", provider, prompt, validate=lambda x: None,
                                      metadata={"text_sha256": sha}, tokens=(5000 if groq_gemini_klein_pilot() else
                                                                   2900 if provider == "groq" else 8300),
                                      repair=repair)
                except Blocked as exc:
                    last_error = exc
                    if provider == "openrouter" and str(exc) == "empty_visible_content":
                        return self.model("editorial-audit-retry", provider, prompt, validate=lambda x: None,
                                          metadata={"text_sha256": sha}, tokens=9000)
            raise last_error
        report = self.task("qa", "local-qa-and-editorial-review",
                           {"text_sha256": sha, "reference": reference and reference["text_sha256"],
                            "length_exception_reason": candidate["article"].get("length_exception_reason"),
                            **({"reviewer": gemini_model(),
                                  **({"audit_sha256": digest(json.dumps(submitted_audit, sort_keys=True))}
                                     if submitted_audit is not None else {})} if gemini_pilot() else
                               {"reviewer": "ovh-qwen-pilot-resume"} if pilot_resume else
                               {"reviewer": "ovh-qwen-correction", "audit_sha256": digest(
                                    json.dumps(submitted_audit, ensure_ascii=False, sort_keys=True))} if ovh_recover() else
                               {"reviewer": "ovh-qwen-facts-only"} if ovh_finalize() else
                               {"reviewer": "ovh-qwen-combined" if submitted_audit is not None else
                                "ovh-qwen-with-image-plan"} if ovh_trial() else
                               {"reviewer": "ovhcloud"} if without_openrouter() else {})},
                           lambda _: qa(raw, self.channel, self.prior[1], self.records,
                                        audit, self.memory.time_left), minimum_seconds=20 * 60,
                           reusable=lambda row: all(row[key]["status"] == "completed"
                               for key in ("model_a", "grammar", "editorial")))
        if report["text_sha256"] != sha:
            raise Blocked("qa_sha_mismatch")
        return self.remember_version(raw, report)

    def prose(self, topic):
        if ovh_finalize() or (groq_ovh_klein_pilot() and self.memory.retry):
            entry = self.memory.data["articles"][self.id]
            previous_sha = entry.get("text_sha256")
            if not isinstance(previous_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_sha):
                raise Blocked("ovh_finalize_saved_text_missing")
            path = self.folder / "versions" / (previous_sha + ".md")
            if not path.is_file():
                raise Blocked("ovh_finalize_saved_text_missing")
            raw = path.read_text(encoding="utf-8")
            if digest(canonical(parse_article(raw))) != previous_sha:
                raise Blocked("qa_sha_mismatch")
            self.current = self.remember_version(raw)
            if gemini_pilot():
                original = self.current
                prompt = (f"{RULES}\nОтредактируй черновик, сохрани тему и ровно шесть "
                          "разделов с жирными заголовками. Сохрани 3000–6000 знаков без пробелов, "
                          "вступление и точное название канала в последнем призыве. "
                          "Удали плохо подтверждённые цифры и факты. Все факты должны следовать "
                          "из приведённых источников. Верни только полный текст статьи. "
                          f"\nКАНАЛ: {SUBSCRIPTION_CHANNELS[self.channel]}"
                          f"\nИСТОЧНИКИ:\n{source_context(self.records, chars_per_source=5000)}"
                          f"\nЧЕРНОВИК:\n{raw}")
                if os.environ.get("EDITORIAL_GEMINI_COMBINED_FALLBACK") == "true":
                    prompt = prompt.replace("Верни только полный текст статьи.", (
                        'Верни JSON с полями article, issues, claims, image_plan. article — полный '
                        'отредактированный текст. issues — список оставшихся замечаний или []. '
                        'claims — все проверяемые утверждения именно из article, включая числа. '
                        'Каждая запись содержит claim (дословный отрывок текста), source (S1, S2 '
                        'или S3), quote (точная непрерывная цитата источника на языке источника '
                        'длиной от 18 знаков), verdict (supported или unsupported). Не выдумывай '
                        'ссылок или цитат. image_plan содержит subject и images из ровно семи '
                        'записей. section в порядке [1, 1, 2, 3, 4, 5, 6]. Две записи с kind '
                        'illustration, пять с kind photo. Каждая запись содержит section, kind, '
                        'а также английские subject, brief, query для Klein или Wikimedia Commons.'))
                    answer = self.model("full-edit", "gemini", prompt, validate=lambda x: None,
                                        metadata={"source_sha256": previous_sha}, tokens=8192)
                    edited = answer.get("article")
                    if not isinstance(edited, str) or not edited.strip():
                        raise Blocked("empty_visible_content")
                    image_plan = answer.get("image_plan")
                else:
                    edited = self.model("full-edit", "gemini", prompt,
                                        metadata={"source_sha256": previous_sha}, tokens=4800)
                    answer = image_plan = None
                self.current = self.remember_version(edited)
                self.memory.article(self.id, independent_editor_provider="gemini", trial_image_plan=image_plan)
                self.current = self.inspect(self.current, original, submitted_audit=answer)
                return self.current
            if ovh_recover():
                previous = read_json(self.folder / "versions" / (previous_sha + ".json"), {})
                previous_issues = previous.get("issues", [])
                fact_status = previous.get("facts", {}).get("status")
                print(json.dumps({"ovh_recovery_previous_qa": {
                    "facts": fact_status, "editorial": previous.get("editorial", {}).get("status"),
                    "verified_claims": len(previous.get("facts", {}).get("evidence", [])),
                    "issues": len(previous_issues)}}), flush=True)
                if (previous and not previous_issues and fact_status == "completed"
                        and all(previous.get(key, {}).get("status") == "completed"
                                for key in ("model_a", "grammar", "editorial"))):
                    self.current = self.remember_version(raw, previous)
                    return self.current
                prompt = (
                    f"{RULES}\nДоработай научпоп, исправляя только перечисленные замечания. "
                    "Удали утверждения, которые нельзя подтвердить приведёнными источниками, или замени "
                    "их сведениями, которые источник подтверждает напрямую. Если числу нет точного "
                    "подтверждения в той же записи (единицы, разделители, порядок цифр), убери число. "
                    "Сохрани тему, интро и ровно шесть разделов с жирными заголовками. Сохрани 3000–6000 "
                    "знаков без пробелов и точное название канала в заключительном призыве. "
                    "Верни только один JSON с полями article, issues и claims. article — полный исправленный "
                    "текст; issues — оставшиеся ошибки или пустой массив. claims — проверяемые фразы "
                    "именно из исправленного текста. У каждого claim (дословная фраза), source (S1, S2 "
                    "или S3 согласно источникам ниже), quote (дословная непрерывная цитата на языке "
                    "источника длиной не менее 18 знаков), verdict (supported или unsupported). "
                    "Проверь в claims все числовые утверждения. Без плана иллюстраций. "
                    "Никогда не придумывай цитаты, данные и источники. "
                    f"\nЗАМЕЧАНИЯ:\n{json.dumps(previous_issues[:35], ensure_ascii=False)}"
                    f"\nИСТОЧНИКИ:\n{source_context(self.records, chars_per_source=8500)}"
                    f"\nСОХРАНЁННЫЙ ТЕКСТ:\n{raw}")
                result = self.model("repair-saved-facts", "ovh", prompt, validate=lambda x: None,
                                    metadata={"text_sha256": previous_sha}, tokens=14000)
                corrected = result.get("article")
                if not isinstance(corrected, str) or not corrected.strip():
                    raise Blocked("empty_visible_content")
                self.current = self.remember_version(corrected)
                if self.current["text_sha256"] != previous_sha:
                    self.memory.article(self.id, trial_image_plan=None)
                self.current = self.inspect(self.current, submitted_audit=result)
            else:
                self.current = self.inspect(self.current)
            return self.current
        facts = source_context(self.records)
        angle = topic["angle"]
        style = ("Пиши художественный вымысел, с конкретным героем, причиной и завершённой развязкой."
                 if self.channel == "legends" else
                 "Объясни наблюдаемое явление и механизм. Используй только факты из источников. "
                 "Не выдумывай числа, исследования, цитаты или наблюдения.")
        if self.film:
            style = ("Канал «Кадр за Кадром». Отделяй авторскую оценку от доказуемых фактов. "
                     "Не выдумывай сцены, реплики и последовательность событий. "
                     f"Карточка, формат и режим спойлеров: {json.dumps(self.film, ensure_ascii=False)}.")
        channel_name = SUBSCRIPTION_CHANNELS[self.channel]
        brief = (f"{RULES}\n{style}\n"
                 f"В последнем абзаце естественно пригласи подписаться на канал "
                 f"«{channel_name}». Укажи название канала точно в таком написании, "
                 "не вставляй URL или Markdown-ссылку: кликабельную ссылку "
                 "редакция добавит сама при экспорте.\n"
                 f"Тема: {self.title}\nЗамысел: {angle}\nИСТОЧНИКИ:\n{facts}")
        if groq_gemini_klein_pilot():
            # Economical pilot: one Groq draft, one Gemini full edit and one
            # independent Gemini QA. Save the original before editing to ensure
            # a failed API request never destroys the draft or spends it again.
            original_raw = self.model("draft", "groq", brief, tokens=3000,
                                      metadata={"topic": self.title})
            original = self.remember_version(original_raw)
            self.current = original
            self.memory.article(self.id, draft_provider="groq",
                                independent_editor_provider="gemini")
            edit_prompt = (
                brief + "\nТы редактор. Исправь композицию, логику и язык."
                " Сохрани все достоверные факты, сюжет и сильное вступление."
                " Оставь 3000–6000 знаков без пробелов, шесть или семь разделов"
                " с жирными заголовками и призыв подписаться в конце."
                " Не выдумывай сцены фильма и цитаты. Верни только полный текст."
                "\nЧЕРНОВИК:\n" + original_raw)
            try:
                edited = self.model("full-edit", "gemini", edit_prompt,
                                    tokens=6200,
                                    metadata={"source_sha256": original["text_sha256"]})
            except Blocked as exc:
                # A 503 or timeout is a transient Google outage, not exhaustion
                # of the account's daily calls. Retry once as a NEW saved task
                # with a separately charged request to the direct 3.6 endpoint.
                # Never repeat auth/quota/invalid-output failures automatically.
                retryable = {"http_503_transient", "http_502_transient",
                             "http_504_transient", "connection_timeout", "connection_failed"}
                if str(exc) not in retryable:
                    raise
                with self.memory.lock:
                    used = sum(row["article"] == self.id and
                               row["provider"] == "gemini" for row in self.memory.data["calls"])
                if used >= self.config["per_article_calls"]["gemini"]:
                    raise
                edited = self.model("full-edit-retry", "gemini", edit_prompt,
                                    tokens=6200,
                                    metadata={"source_sha256": original["text_sha256"],
                                              "fallback_reason": str(exc)})
            self.current = self.inspect(self.remember_version(edited), original)
            self.attempts.append({"role": "independent_edit", "provider": "gemini",
                                  "status": "accepted_after_qa",
                                  "before": original["text_sha256"],
                                  "after": self.current["text_sha256"]})
            return self.current
        if ovh_trial():
            format_rules = ("\nОбязательная форма: короткое вступление, ровно шесть разделов с "
                            "жирными заголовками и 3000–6000 знаков без пробелов. Последний "
                            "абзац приглашает подписаться и называет канал точно.")
            writer = "groq" if groq_ovh_klein_pilot() else "ovh-gptoss"
            raw = self.model("draft", writer, brief + format_rules,
                             metadata={"topic": self.title}, tokens=3000 if writer == "groq" else None)
            original = self.remember_version(raw)
            self.current = original
            self.memory.article(self.id, draft_provider=writer, independent_editor_provider="ovh")
            prompt = (brief + format_rules + "\nОтредактируй черновик до готового текста. "
                      "Сохрани проверяемые факты и поддержанные источниками выводы. "
                      "Верни только полный текст.\nЧЕРНОВИК:\n" + raw)
            if self.memory.retry:
                prompt = prompt.replace("Верни только полный текст.", "")
                prompt += (
                    '\nОтвет верни единым JSON без Markdown-обрамления. Поле article содержит полный '
                    'отредактированный текст с буквальными Markdown-заголовками и переносами, '
                    'закодированными в JSON. Проверь именно текст в article. Поле issues — список '
                    'оставшихся редакционных замечаний или []. Поле claims — все проверяемые '
                    'утверждения из article, включая числа и причинность. У каждого четыре поля: '
                    'claim (дословно из article), source (S1, S2 и т. д.), quote (дословная цитата '
                    'из указанного источника), verdict (supported или unsupported). '
                    'Поле image_plan — объект с subject и images: всего семь разных изображений, '
                    'два для раздела 1 и по одному для разделов 2–6. Для каждого укажи section, kind '
                    '(photo или illustration), subject, brief и query для Wikimedia Commons. '
                    'Используй оба вида. Не придумывай URL, лицензии или факты.')
                verdict = self.model("full-edit-with-audit", "ovh", prompt,
                                     validate=lambda data: None, tokens=14000,
                                     metadata={"source_sha256": original["text_sha256"]})
                edited = verdict.get("article")
                if not isinstance(edited, str) or not edited.strip():
                    raise Blocked("empty_visible_content")
                self.memory.article(self.id, trial_image_plan=verdict.get("image_plan"))
                self.current = self.remember_version(edited)
                self.current = self.inspect(self.current, original, submitted_audit=verdict)
            else:
                edited = self.model("full-edit", "ovh", prompt,
                                    metadata={"source_sha256": original["text_sha256"]},
                                    tokens=12000 if groq_ovh_klein_pilot() else None)
                self.current = self.remember_version(edited)
                self.current = self.inspect(self.current, original)
            return self.current
        from .editorial_alt_models import enabled as alternative_test
        author, editor = ("kilo", "openrouter") if alternative_test() else ("groq", "openrouter")
        if without_openrouter():
            editor = "ovh-gptoss"
        try:
            raw = self.model("draft", author, brief, metadata={"topic": self.title})
        except Blocked as exc:
            if alternative_test() or str(exc) in {"production_deadline", "groq_quota_wait_exceeds_deadline"}:
                # Kilo/ModelScope were already tried as writers in the
                # isolated route. OpenRouter must stay the independent editor.
                raise
            author, editor = "openrouter", "groq"
            raw = self.model("draft-fallback", author, brief, metadata={"topic": self.title})
        if alternative_test():
            with self.memory.lock:
                draft = next((call for call in reversed(self.memory.data["calls"])
                              if call["article"] == self.id and call["status"] == "responded"
                              and self.memory.data["tasks"][call["task"]]["operation"].startswith("draft")), None)
            if draft:
                author = draft["provider"]
                self.memory.article(self.id, draft_model=draft["answer"].get("model"))
        self.memory.article(self.id, draft_provider=author, independent_editor_provider=editor)
        self.current = self.remember_version(raw)
        original = self.current
        edit_prompt = (brief + "\nТы независимый литературный редактор. Полностью отредактируй "
                       "черновик, исправь композицию и тон. Сохрани сюжет и важные факты. "
                       "Если объём мал, развивай только недостаточно раскрытые места. "
                       "Сохрани естественный CTA. Верни полностью отредактированный текст.\nЧЕРНОВИК:\n"
                       + raw)
        edited = None
        try:
            edited = self.model("full-edit", editor, edit_prompt,
                                metadata={"source_sha256": original["text_sha256"]})
            self.remember_version(edited)
        except Blocked as exc:
            can_retry = str(exc) in {"empty_visible_content", "connection_failed", "connection_timeout", "truncated_response"}
            if can_retry:
                try:
                    edited = self.model("full-edit-retry", editor, edit_prompt,
                                        metadata={"source_sha256": original["text_sha256"]})
                    self.remember_version(edited)
                except Blocked as last:
                    self.blockers.append(f"independent_edit_unavailable: {last}")
            else:
                self.blockers.append(f"independent_edit_unavailable: {exc}")
        self.current = self.inspect(original)
        if edited and canonical(parse_article(edited)) != canonical(original["article"]):
            candidate = self.inspect(self.remember_version(edited), self.current)
            old_report, new_report = self.current["report"], candidate["report"]
            accepted = improves(old_report, new_report) or (
                not old_report["issues"] and not new_report["issues"]
                and all(new_report[key]["status"] == old_report[key]["status"]
                        for key in ("model_a", "grammar", "editorial", "facts")))
            self.attempts.append(self.change("independent_edit", editor, self.current, candidate, accepted))
            if accepted:
                self.current = candidate
        elif edited:
            self.attempts.append({"role": "independent_edit", "status": "unchanged",
                                  "before": self.current["text_sha256"], "after": self.current["text_sha256"]})
        self.revise(brief)
        return self.current

    def change(self, role, provider, before, after, accepted):
        return {"role": role, "provider": provider, "status": "accepted" if accepted else "rejected",
                "before": before["text_sha256"], "after": after["text_sha256"],
                "addressed_issues": before["report"]["issues"], "candidate_issues": after["report"]["issues"]}

    def revise(self, brief):
        from .editorial_alt_models import enabled as alternative_test
        limit = min(2, self.escalation["agent_revision_limit"]) if alternative_test() else self.escalation["agent_revision_limit"]
        seen = {self.current["text_sha256"], *(row["after"] for row in self.attempts if "after" in row)}
        for attempt in range(1, limit + 1):
            report = self.current["report"]
            if not report["issues"]:
                break
            if self.memory.time_left() < 22 * 60:
                self.blockers.append("revision_deferred_deadline")
                break
            prompt = (brief + "\nИсправь только перечисленные проблемы. Сохрани факты и сюжет. "
                      "Если зафиксирована ошибка section_structure, сделай ровно шесть или семь разделов "
                      "с жирными заголовками и краткое вступление перед первым заголовком. "
                      "Не повторяй отклонённые изменения. Верни полный исправленный текст. "
                      f"\nПРОБЛЕМЫ:\n{json.dumps(report['issues'], ensure_ascii=False)}"
                      f"\nУЖЕ ПРОВЕРЕННЫЕ РЕЗУЛЬТАТЫ:\n{json.dumps(self.attempts, ensure_ascii=False)}"
                      f"\nТЕКУЩАЯ ВЕРСИЯ:\n{self.current['raw']}")
            errors = []
            response = None
            providers = ("kilo",) if without_openrouter() else (
                ("openrouter", "kilo") if alternative_test() else ("groq", "openrouter"))
            for provider in providers:
                try:
                    response = self.model(f"repair-{attempt}", provider, prompt)
                    break
                except Blocked as exc:
                    errors.append(f"{provider}: {exc}")
            if response is None:
                self.attempts.append({"role": "agent", "status": "blocked", "errors": errors})
                break
            candidate = self.remember_version(response)
            if candidate["text_sha256"] in seen:
                self.attempts.append({"role": "agent", "provider": provider, "status": "unchanged_or_repeated",
                                      "before": self.current["text_sha256"], "after": candidate["text_sha256"]})
                continue
            seen.add(candidate["text_sha256"])
            candidate = self.inspect(candidate, self.current, repair=True)
            accepted = improves(report, candidate["report"])
            self.attempts.append(self.change("agent", provider, self.current, candidate, accepted))
            if accepted:
                self.current = candidate
        if self.current["report"]["issues"]:
            self.assistant = ("assistant_handoff_pending" if sum(
                x["role"] == "agent" and x["status"] in {"accepted", "rejected"}
                for x in self.attempts) == limit
                else "agent_revisions_pending")

    def images(self):
        from .editorial_alt_models import enabled as alternative_test
        pilot = groq_ovh_klein_pilot()
        # Mystical scenes use Klein; science retains real photos + Klein mixed media.
        sourced_illustrations = False
        article = self.current["article"]
        count = len(article["sections"])
        if not 6 <= count <= 7:
            raise Blocked("image_plan_layout_requires_six_or_seven_sections")
        guide = ("Только illustration. Сделай 7–8 кинематографичных, разных сцен." if self.channel == "legends"
                 else "Скомбинируй оба типа. Предпочти реальные фотографии, когда они точно показывают явление. "
                 "Photo для реальных объектов "
                 "и наблюдений, illustration для объяснения механизмов, аналогий и реконструкций. "
                 "Для photo дай краткий английский поисковый запрос; визуализация не изображает доказательство.")
        if sourced_illustrations:
            if self.channel == "legends":
                guide = ("Подбери разные атмосферные фотографии или иллюстрации, "
                         "подходящие конкретным разделам вымышленной истории.")
            guide += (" Для каждой фотографии и иллюстрации составь краткий английский запрос "
                      "для поиска в Wikimedia Commons. Планируй сюжетно точные кадры, опираясь "
                      "на объекты, места и сцены, для которых реально найти самостоятельные "
                      "свободно лицензированные работы. Не выдавай иллюстрацию за документальный кадр.")
        if self.channel == "cinema":
            guide = ("Только photo: подлинные кадры именно этого фильма из открытых источников. "
                     "Для каждого кадра дай английский запрос по сцене и персонажам; не придумывай эпизоды. "
                     "Постеры, фан-арт и кадры из других экранизаций не подходят.")
        allowed_kinds = ("photo" if self.channel == "cinema" else
                         "illustration" if self.channel == "legends" and not sourced_illustrations else
                         "photo или illustration")
        sample_kind = "illustration" if self.channel == "legends" else "photo"
        prompt = (f"{guide} Поле kind: {allowed_kinds}. Укажи одно точное значение. "
                  f"Строго {count+1} разных изображений на {count} разделов: "
                  "два разных изображения привяжи к разделу 1 (лучшее по смыслу и качеству "
                  "будет отдельной обложкой сразу после оглавления, второе — сразу после "
                  "первого заголовка), ровно по одному — к каждому разделу 2 и далее. "
                  "После каждого жирного заголовка должен идти кадр без пустых абзацев. "
                  "Оцени каждую сцену по смыслу и не вставляй отвлечённые заглушки. Верни JSON "
                  '{"subject":"English overall theme","images":[{"section":1,"kind":"'
                  + sample_kind + '",'
                  '"subject":"English concrete scene or subject","brief":"English detailed visual brief",'
                  '"query":"English search phrase"}]}.\n'
                  + self.current["raw"])
        plan = None
        last = None
        if os.environ.get("EDITORIAL_IMAGE_RECOVERY_ONLY") == "true":
            # Reuse the existing model-created image plan, never ask Groq again.
            with self.memory.lock:
                cached = next((
                    row["result"] for row in self.memory.data["tasks"].values()
                    if row["article"] == self.id and row["operation"] == "image-plan-1"
                    and row["status"] == "completed" and isinstance(row.get("result"), dict)
                ), None)
            if cached is None:
                raise Blocked("image_recovery_missing_original_plan")
            plan = json_reply(cached)
            validate_plan(plan, count, self.channel, self.config)
        providers = ("groq", "kilo") if without_openrouter() else (
            ("groq",) if alternative_test() else ("groq", "openrouter"))
        if ovh_trial():
            plan = self.memory.data["articles"][self.id].get("trial_image_plan")
            try:
                if not isinstance(plan, dict):
                    raise Blocked("image_plan_missing_from_ovh_review")
                validate_plan(plan, count, self.channel, self.config, sourced_illustrations=True)
            except Blocked:
                if ((not ovh_finalize() and not pilot)
                        or (pilot and self.memory.retry and not gemini_pilot())):
                    raise
                if ovh_recover() or pilot:
                    layout = ", ".join(map(str, [1, 1, *range(2, count + 1)]))
                    prompt += (f"\nПерепроверь JSON перед ответом: окончательный массив images "
                               f"должен иметь ровно {count + 1} элементов; их section в порядке "
                               f"записей: [{layout}]. Не включай другие варианты и пояснения.")
                if pilot:
                    prompt += " Укажи две иллюстрации для Klein. Для всех остальных записей используй photo."
                provider = "gemini" if gemini_pilot() else "ovh"
                plan = self.model("image-plan-1", provider, prompt,
                                  validate=lambda x: validate_plan(x, count, self.channel, self.config,
                                                                   sourced_illustrations=True),
                                  metadata={"text_sha256": self.current["text_sha256"]}, tokens=9500)
                self.memory.article(self.id, trial_image_plan=plan)
            providers = ()
        for attempt, provider in enumerate(providers, 1):
            try:
                plan = self.model(f"image-plan-{attempt}", provider,
                                  prompt + (f"\nИсправь: {last}." if last else ""),
                                  validate=lambda x: validate_plan(x, count, self.channel, self.config,
                                                                   sourced_illustrations=sourced_illustrations),
                                  metadata={"text_sha256": self.current["text_sha256"]},
                                  tokens=1800 if groq_gemini_klein_pilot() else 6000 if alternative_test() else (2800 if provider == "groq" else 5500))
                break
            except Blocked as exc:
                last = exc
        if plan is None:
            raise last
        scenes = plan["images"]
        if pilot:
            if sum(scene["kind"] == "illustration" for scene in scenes) != 2:
                raise Blocked("image_plan_requires_two_klein_illustrations")
            scenes = sorted(scenes, key=lambda scene: scene["kind"] != "illustration")
        for index, scene in enumerate(scenes, 1):
            path = self.folder / "images" / f"{self.current['text_sha256'][:12]}-{index:02d}.jpg"
            existing = read_json(path.with_suffix(".json"))
            if (existing and path.is_file() and digest(path.read_bytes()) == existing["sha256"]
                    and (not sourced_illustrations or existing["rights_status"] != "rights_review_pending")
                    and existing["section"] == scene["section"] and existing["scene"] == scene["subject"]):
                self.pictures.append(existing)
                continue
            try:
                if self.film:
                    executor = "public-film-stills"
                    fn = lambda _, s=scene, p=path: cinema.stills(
                        s, plan["subject"], p, self.image_index, self.film["film"], self.cinema_registry)
                elif sourced_illustrations or scene["kind"] == "photo":
                    executor = "commons-verified-license" if sourced_illustrations else "commons-nasa"
                    # In the mixed-media science pilot, try traceable NASA photos after
                    # Commons; NASA rights are explicitly left pending for owner review.
                    # Only the sourced-illustration route requires Commons license metadata.
                    options = {"verified_only": True} if sourced_illustrations else {}
                    fn = lambda _, s=scene, p=path: photo(s, plan["subject"], p, self.image_index, **options)
                else:
                    executor = "klein"
                    fn = lambda task, s=scene, p=path: generated(s, plan["subject"], p, self.image_index,
                                                                 task, self.id, self.channel)
                result = self.task(f"image-{index:02d}", executor,
                                   {"scene": scene, "text_sha256": self.current["text_sha256"],
                                    **({"source": executor} if sourced_illustrations or self.film else {})},
                                   fn, minimum_seconds=155)
                if not path.is_file() or digest(path.read_bytes()) != result["sha256"]:
                    raise Blocked("stored_image_unavailable_or_sha_mismatch")
                self.pictures.append(result)
            except Blocked as exc:
                self.blockers.append(f"image_{index:02d}: {exc}")
                if str(exc) == "production_deadline" or str(exc).startswith("visual_matcher_unavailable"):
                    break
        if len(self.pictures) != len(plan["images"]):
            self.blockers.append("incomplete_image_plan")
        if self.channel == "science" and {row["kind"] for row in self.pictures} != {"photo", "illustration"}:
            self.blockers.append("science_mixed_media_missing")

    def run(self):
        existing = self.memory.data["articles"].get(self.id, {})
        from .editorial_alt_models import enabled as alternative_test
        if existing.get("status") == "review_pending" and alternative_test():
            return existing["status"]
        if existing.get("status") in ("review_pending", "rights_review_pending") and not self.memory.retry:
            return existing["status"]
        if os.environ.get("EDITORIAL_IMAGE_RECOVERY_ONLY") == "true":
            if not self.memory.retry or self.channel != "cinema":
                raise Blocked("image_recovery_requires_cinema_retry")
            # Do not rerun any text model or QA. Read ONLY the immutable
            # article text and report preserved in the authenticated state.
            self.title = existing["title"]
            topic = existing["topic_plan"]
            self.records = topic["records"]
            self.film = {k: topic[k] for k in ("film", "format", "spoilers", "detailed_plot_evidence")}
            original_sha = existing["text_sha256"]
            raw_path = self.folder / "versions" / (original_sha + ".md")
            report_path = self.folder / "versions" / (original_sha + ".json")
            if not raw_path.is_file() or not report_path.is_file():
                raise Blocked("image_recovery_immutable_original_missing")
            self.current = self.remember_version(raw_path.read_text(encoding="utf-8"),
                                                  report=read_json(report_path))
            if self.current["text_sha256"] != original_sha:
                raise Blocked("image_recovery_original_sha_mismatch")
            self.memory.article(self.id, status="running", channel=self.channel)
            try:
                self.images()
            except Blocked as exc:
                self.blockers.append(str(exc))
            return self.finish()
        self.memory.article(self.id, status="running", channel=self.channel, prompt_version=PROMPT_VERSION,
                            started_at=existing.get("started_at") or now())
        try:
            plan = self.plan()
            self.title = plan["title"]
            self.records = plan.get("records", [])
            if self.channel == "cinema":
                self.film = {k: plan[k] for k in ("film", "format", "spoilers", "detailed_plot_evidence")}
            self.prose(plan)
            self.images()
        except Blocked as exc:
            self.blockers.append(str(exc))
        except Exception as exc:
            if isinstance(exc, StorageError):
                raise
            self.blockers.append("execution_error_" + type(exc).__name__)
        return self.finish()

    def finish(self):
        summary = {"blockers": self.blockers, "history": self.attempts,
                   "assistant_handoff": self.assistant,
                   "assistant_attempts": 0, "agent_attempts": sum(
                       row["role"] == "agent" and row["status"] in {"accepted", "rejected"}
                       for row in self.attempts),
                   "assistant_revision_limit": self.escalation["assistant_revision_limit"],
                   "revision_calls_attempted": sum(row["role"] == "agent" for row in self.attempts),
                   "assistant_handoff_reason": ("chatgpt_work_transport_not_connected"
                                                if self.assistant == "assistant_handoff_pending" else None),
                   "publication_authorized": False}
        if self.film:
            summary["film"] = self.film
        status = "blocked"
        if self.current:
            report = self.current["report"]
            if not report:
                report = {**structural_checks(self.current["article"], self.prior[1]),
                          "text_sha256": self.current["text_sha256"], "article": self.current["article"],
                          "detectors": {name: {"status": "unavailable"} for name in REQUIRED_CHECKS},
                          "model_a": {"status": "unavailable"}, "grammar": {"status": "unavailable"},
                          "editorial": {"status": "unavailable"}, "facts": {"status": "unavailable", "evidence": []}}
            checks = all(report[k]["status"] == "completed" for k in ("model_a", "grammar", "editorial"))
            facts_ok = self.channel == "legends" or report["facts"]["status"] == "completed"
            if not checks:
                self.blockers.append("editorial_checks_incomplete")
            if not facts_ok:
                self.blockers.append("fact_check_incomplete")
            if report["issues"]:
                self.blockers.append("substantive_issues_pending")
            if len(self.pictures) < self.config["minimum_images"]:
                self.blockers.append("image_count_below_minimum")
            visual_issues = layout_issues(
                self.current["article"], self.pictures,
                minimum=self.config["minimum_images"], maximum=self.config["maximum_images"])
            if visual_issues:
                self.blockers.extend(visual_issues)
            summary["layout_status"] = "layout_blocked" if visual_issues else "verified"
            summary["layout_issues"] = visual_issues
            pending_rights = any(row["rights_status"] == "rights_review_pending" for row in self.pictures)
            status = ("partial_review_pending" if self.blockers else
                      "rights_review_pending" if pending_rights and not self.film else "review_pending")
            summary.update(status=status, rights_status="rights_review_pending" if pending_rights else
                           "metadata_verified" if self.pictures else "unavailable")
            if self.film and pending_rights:
                summary["image_use"] = "private_editorial_review_only"
            folder = self.memory.folder / "review" / self.id
            try:
                self.task("review-package", "docx-html-export",
                          {"text_sha256": report["text_sha256"], "checks_sha256": digest(
                              json.dumps(report, sort_keys=True, ensure_ascii=False)),
                           "image_sha256": [row["sha256"] for row in self.pictures], "status": status},
                          lambda _: package(folder, self.title,
                                            sorted((self.folder / "versions").glob("*")), report,
                                            self.pictures, self.folder / "images", summary), minimum_seconds=-300)
            except Blocked as exc:
                status = "package_failed"
                self.blockers.append(f"document_export_failed: {exc}")
        self.memory.article(self.id, **{**summary, "status": status, "finished_at": now(),
                                       "text_sha256": self.current["text_sha256"] if self.current else None})
        return status


def settings():
    cfg = read_json(CONFIG / "editorial_production.json")
    policy = read_json(CONFIG / "ai_revision_policy.json")
    site = read_json(CONFIG / "site.json")
    if site.get("publication_enabled") is not False or policy.get("publication_authorized") is not False:
        raise Blocked("require_publication_freeze")
    if any(channel not in CHANNELS or (count and
           (channel not in policy["current_channels"] or channel in policy["paused_channels"]))
           for channel, count in cfg["articles_per_channel"].items()):
        raise Blocked("invalid_active_channel")
    if any(type(n) is not int or not 0 <= n <= 6 for n in cfg["articles_per_channel"].values()):
        raise Blocked("invalid_article_count")
    budget = cfg["groq_budget"]
    lower, upper = budget["target_daily_tokens"]
    if (not 0 < lower <= upper <= budget["daily_tokens"] or
            not 0 < budget["repair_reserve_tokens"] < budget["daily_tokens"] or
            not 0 < budget["experimental_daily_tokens"] or
            not all(key in {"rpd", "tpd", "rpm", "tpm", "itpm", "otpm"}
                    and type(value) is int and value > 0 for key, value in budget["organization_limits"].items())):
        raise Blocked("invalid_groq_budget")
    return cfg


def deadline(day, hour, tz):
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(hour), tz).astimezone(timezone.utc)


def groq_plan(memory):
    ready = {"review_pending", "rights_review_pending"}
    pending = Counter()
    count = 0
    for channel, amount in memory.config["articles_per_channel"].items():
        for index in range(1, amount + 1):
            article = f"{channel}-{index:02d}"
            entry = memory.data["articles"].get(article, {})
            if entry.get("status") in ready:
                continue
            count += 1
            tasks = [t for t in memory.data["tasks"].values() if t["article"] == article and
                     t["status"] == "completed"]
            done = {t["operation"] for t in tasks}
            if not entry.get("title") and channel == "legends":
                pending["topics"] += 1
            for stage in ("draft", "editorial-audit", "image-plan"):
                if not any(stage == name or name.startswith(stage + "-") for name in done):
                    pending[stage] += 1
            if not any(name.startswith("repair-") for name in done):
                pending["repair"] += 1
    return forecast(memory.groq_rows(), memory.config["groq_budget"], memory.groq_lane,
                    datetime.now(timezone.utc), memory.time_left(), count, pending)


def status(memory):
    entries = memory.data["articles"]
    expected = {f"{channel}-{i:02d}" for channel, n in memory.config["articles_per_channel"].items()
                for i in range(1, n + 1)}
    result = {"day": memory.day, "articles": {article: entries.get(article, {}).get("status", "missing")
                                         for article in sorted(expected)},
            "calls": dict(Counter(x["provider"] for x in memory.data["calls"])),
            "production_started_at": memory.data.get("started_at"),
            "late_start_seconds": memory.data.get("late_start_seconds"),
            "provider_stops": memory.data["provider_stops"],
            "groq_forecast": groq_plan(memory),
            "delivery": memory.data.get("delivery", {"status": "not_connected"}),
            "publication_authorized": False}
    if groq_ovh_klein_pilot():
        draft_calls = [row for row in memory.data["calls"]
                       if row["provider"] == "groq" and row["status"] == "responded"]
        counts = [row.get("total_tokens") for row in draft_calls]
        if counts and all(type(count) is int for count in counts):
            result["groq_pilot_capacity"] = {
                "tokens_for_one_draft": sum(counts),
                "nine_draft_projection": 9 * sum(counts),
            }
    if gemini_pilot():
        calls = [row for row in memory.data["calls"] if row["provider"] == "gemini"]
        result["gemini_budget"] = {"project_rpm_configured": gemini_limit("EDITORIAL_GEMINI_RPM", 3, 30),
                                   "local_daily_ceiling": memory.config["daily_calls"]["gemini"],
                                   "per_article_ceiling": memory.config["per_article_calls"]["gemini"],
                                   "planned_articles": 9, "base_calls": 18,
                                   "calls": len(calls),
                                   "tokens": sum(row.get("usage", {}).get("total_tokens", 0) for row in calls)}
    return result


def review(memory):
    review_dir = memory.folder / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    report = status(memory)
    report["diagnostics"] = {article: entry.get("blockers", [])
                             for article, entry in memory.data["articles"].items()}
    write_json(review_dir / "Статус комплекта.json", report)
    packet = memory.folder / "Комплект согласования.zip"
    sha = zip_packet(review_dir, packet, articles=report["articles"])
    write(packet.with_suffix(".sha256"), (sha + "\n").encode("utf-8"))
    memory.save()
    if not memory.private_git:
        delivery = {"status": "delivery_integration_pending", "reason": "no_verified_owner_transport", "package_sha256": sha}
    else:
        counts = Counter(report["articles"].values())
        message = ", ".join(f"{status}: {n}" for status, n in sorted(counts.items()))
        try:
            url = memory.private_git.notify(memory.day, message)
            if not url:
                raise StorageError("notification_issue_not_returned")
            delivery = {"status": "owner_notification_created", "url": url, "read_confirmed": False,
                        "package_url": memory.private_git.review_url(memory.day), "package_sha256": sha}
        except StorageError as exc:
            delivery = {"status": "private_package_stored_notification_failed",
                        "reason": str(exc), "package_sha256": sha,
                        "package_url": memory.private_git.review_url(memory.day)}
    memory.data["delivery"] = delivery
    memory.save()
    return delivery


def health(storage):
    required = ("GROQ_API_KEY", "OPENROUTER_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
    return {"configuration_only": True, "private_storage": "connected" if storage else "not_connected",
            "credentials_present": {name: bool(os.environ.get(name)) for name in required},
            "free_account_attested": {s: os.environ.get(var) == "true" for s, var in (
                ("klein", "EDITORIAL_CF_FREE_ONLY"), ("groq", "EDITORIAL_GROQ_FREE_ONLY"))},
            "local_executables": {cmd: bool(shutil.which(cmd)) for cmd in
                                  ("java", "aiw-ru", "ai-accent-detector", "git")},
            "assistant_handoff": "not_connected"}


def validate_klein():
    """One owner-authorized free request with a generic, unpublished test scene."""
    import io
    from PIL import Image, ImageOps

    scene = "an empty tram stop in the rain with a red shelter and tram rails at night"
    raw, params = klein(f"Editorial test image of {scene}. Clean composition, no text or logos.")
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if image.size != (params["width"], params["height"]):
        raise Blocked("klein_image_dimension_mismatch")
    result = {"klein": "completed", "model": MODELS["klein"], "parameters": params,
              "image_sha256": digest(raw), "publication_authorized": False}
    try:
        model, values, vector = similarity(image, [scene, "a portrait of a cat indoors"])
        result["visual_check"] = {"model": model, "scene_score": values[0],
                                  "unrelated_score": values[1], "vector_size": len(vector),
                                  "status": "completed" if values[0] > values[1] else "findings"}
    except Blocked as exc:
        result["visual_check"] = {"status": "unavailable", "reason": str(exc)}
    return result


def audit(memory, storage):
    report = status(memory)
    incidents, durations = Counter(), {}
    for task in memory.data["tasks"].values():
        if task.get("error"):
            incidents[(task["executor"], task["error"])] += 1
        if "elapsed_seconds" in task:
            durations.setdefault(task["executor"], []).append(task["elapsed_seconds"])
    return {**report, "readiness": health(storage),
            "incidents": [{"executor": executor, "reason": reason, "count": count}
                          for (executor, reason), count in incidents.most_common()],
            "average_seconds": {executor: round(sum(samples)/len(samples), 2)
                                for executor, samples in durations.items()},
            "limits": memory.config["daily_calls"], "provider_stops": memory.data["provider_stops"],
            "repository_workflows_with_schedules": [path.name for path in (ROOT / ".github/workflows").glob("*.yml")
                if re.search(r"(?m)^  schedule:", path.read_text(encoding="utf-8"))]}


def cycle_settings(command, validation_window=None, encrypted_mode=False, validate_cinema=False):
    cfg = settings()
    if validate_cinema:
        if (command not in {"daily", "retry"} or not validation_window or not encrypted_mode
                or os.environ.get("EDITORIAL_ISOLATED_VALIDATION") != "true"):
            raise Blocked("cinema_validation_requires_isolated_encrypted_cycle")
        cfg = {**cfg, "articles_per_channel": {**cfg["articles_per_channel"], "cinema": 1}}
        if os.environ.get("EDITORIAL_TEST_FREE_ONLY") == "true":
            # The configured 10 OpenRouter calls cover the two production
            # channels only. A three-channel isolated QA requires a separate
            # bounded test envelope, still enforced by verified $0 model pricing
            # and the provider's actual account quota.
            cfg = {**cfg, "per_article_calls": {
                **cfg["per_article_calls"], "openrouter": 12},
                "daily_calls": {**cfg["daily_calls"], "openrouter": 36}}
    if without_openrouter() and os.environ.get("EDITORIAL_ALT_MODELS_TEST") != "true":
        raise Blocked("openrouter_disable_requires_alternate_validation")
    if os.environ.get("EDITORIAL_ALT_MODELS_TEST") == "true":
        from .editorial_alt_models import require_isolated
        require_isolated()
        if command not in {"daily", "retry"} or not validation_window or not encrypted_mode:
            raise Blocked("alt_test_requires_encrypted_validation")
        if groq_gemini_klein_pilot():
            # One article for each currently requested test channel.
            # A fresh article requires one topic, one draft, one image-plan
            # request on Groq and at most one edit plus one QA on Gemini.
            cfg = {**cfg, "articles_per_channel": {"legends": 1, "cinema": 1},
                   "per_article_calls": {**cfg["per_article_calls"],
                                         "groq": 3, "gemini": 2,
                                         "klein": 8, "openrouter": 0,
                                         "ovh": 0, "ovh-gptoss": 0, "kilo": 0},
                   "daily_calls": {**cfg["daily_calls"],
                                   "groq": 6, "gemini": 4,
                                   "klein": 16, "openrouter": 0,
                                   "ovh": 0, "ovh-gptoss": 0, "kilo": 0}}
            if os.environ.get("EDITORIAL_CINEMA_ONLY_SEED") == "true":
                cfg["articles_per_channel"] = {"cinema": 1}
                cfg["per_article_calls"]["groq"] = 2
                cfg["daily_calls"]["groq"] = 2
                cfg["daily_calls"]["gemini"] = 2
                cfg["daily_calls"]["klein"] = 8
        else:
            if without_openrouter():
                raise Blocked("openrouter_required_after_ovh_removal")
            cfg = {**cfg,
                   "per_article_calls": {**cfg["per_article_calls"], "kilo": 15,
                                         "openrouter": 5, "klein": 12},
                   "daily_calls": {**cfg["daily_calls"], "kilo": 32,
                                   "openrouter": 10, "klein": 24}}
            if os.environ.get("EDITORIAL_TWO_CHANNEL_PILOT") == "true":
                cfg["articles_per_channel"] = {"legends": 1, "cinema": 1}
    if ovh_trial():
        raise Blocked("ovh_removed_from_project")
    return cfg


def execute(command, state, private_git=None, day=None, validation_window=None, encrypted_mode=False,
            *, validate_cinema=False):
    cfg = cycle_settings(command, validation_window, encrypted_mode, validate_cinema)
    tz = ZoneInfo(cfg["timezone"])
    today = datetime.now(tz).date().isoformat()
    day = day or today
    if date.fromisoformat(day).isoformat() != day:
        raise Blocked("invalid_cycle_date")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if isinstance(private_git, PrivateGit) and command != "health":
        private_git.restore(state, day)
    with (state / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cutoff = deadline(day, cfg["production_stop"], tz)
        if validation_window:
            cutoff = datetime.now(timezone.utc) + timedelta(minutes=validation_window)
        memory = Memory(state, day, cutoff, cfg, private_git, retry=command == "retry",
                        lane="experiment" if validation_window else "production")
        if command == "health":
            return health(private_git)
        if command == "status":
            return status(memory)
        if command == "audit":
            return audit(memory, private_git)
        if command == "learn":
            result = learn(state, cfg["learning_min_cases"])
            token = os.environ.get("EDITORIAL_LEARNING_PR_TOKEN")
            if any(row["status"] == "candidate_improved" for row in result["proposals"]):
                if token:
                    try:
                        result["proposal_pull_requests"] = propose_pull_requests(result, token)
                    except (RuntimeError, OSError, ValueError):
                        result["proposal_status"] = "github_proposal_integration_pending"
                else:
                    result["proposal_status"] = "github_proposal_integration_pending"
            write_json(memory.folder / "learning.json", result)
            memory.save()
            return result
        if command == "review":
            return review(memory)
        if not private_git and not encrypted_mode:
            raise Blocked("storage_configuration_pending: encrypted_or_private_storage_required")
        from .editorial_alt_models import enabled as alternative_test
        if day != today and not (alternative_test() and command == "retry" and encrypted_mode
                                 and memory.file.is_file() and day < today):
            raise Blocked("live_generation_only_for_current_moscow_date")
        if "started_at" not in memory.data:
            memory.data["started_at"] = now()
            memory.data["late_start_seconds"] = max(0, round((datetime.now(timezone.utc) -
                                                              deadline(day, cfg["start"], tz)).total_seconds()))
            memory.save()
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(json.dumps({"initial_groq_forecast": groq_plan(memory)}), flush=True)
        prior = archive(state, day)
        image_index = ImageIndex(memory, prior[2])

        def channel_runner(channel, n):
            for number in range(1, n + 1):
                Article(memory, channel, number, prior, image_index).run()

        with ThreadPoolExecutor(max_workers=len(CHANNELS)) as executor:
            jobs = [executor.submit(channel_runner, channel, n)
                    for channel, n in cfg["articles_per_channel"].items() if n]
            for job in jobs:
                job.result()
        write_json(memory.folder / "audit.json", audit(memory, private_git))
        write_json(memory.folder / "learning.json", learn(state, cfg["learning_min_cases"]))
        memory.save()
        return status(memory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("daily", "retry", "review", "status", "audit", "learn", "health"))
    parser.add_argument("--date")
    parser.add_argument("--encrypted-mode", action="store_true")
    parser.add_argument("--validate-cinema", action="store_true",
                        help="Include one test film in an encrypted isolated validation cycle")
    parser.add_argument("--validate-klein", action="store_true",
                        help="One authorized free isolated image call; do not store the image")
    parser.add_argument("--local-state", type=Path, help="Private local inspection directory outside this repository")
    parser.add_argument("--validation-window", type=int, choices=(90, 120),
                        help="Manual validation only, with a separate private state repository")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.validate_klein:
            if args.command != "health" or os.environ.get("EDITORIAL_ISOLATED_VALIDATION") != "true":
                raise Blocked("klein_validation_requires_isolated_health")
            probe = validate_klein()
            print(json.dumps(probe, sort_keys=True))
            return 0 if probe["visual_check"]["status"] == "completed" else 2
        if args.command in {"daily", "retry"} and os.environ.get("GITHUB_ACTIONS") != "true":
            from .editorial_quota import current_status
            if current_status(datetime.now(timezone.utc).date().isoformat()) != "ready":
                raise Blocked("groq_quota_unverified_or_blocked")
        with TemporaryDirectory(prefix="zen-editorial-") as temporary:
            storage_repo = os.environ.get("EDITORIAL_PRIVATE_REPOSITORY")
            token = os.environ.get("EDITORIAL_PRIVATE_TOKEN")
            if args.encrypted_mode:
                from .editorial_delivery import owner_key
                if not args.local_state or token or storage_repo:
                    raise Blocked("storage_configuration_pending: configure_one_storage_backend")
                owner_key()
            if args.validation_window and (args.command not in {"daily", "retry"}
                    or os.environ.get("EDITORIAL_ISOLATED_VALIDATION") != "true"
                    or (not args.encrypted_mode and (not storage_repo
                        or storage_repo != os.environ.get("EDITORIAL_VALIDATION_REPOSITORY")
                        or storage_repo == os.environ.get("EDITORIAL_DAILY_REPOSITORY")))):
                raise Blocked("validation_requires_isolated_state")
            if args.local_state and args.local_state.resolve().is_relative_to(ROOT):
                raise Blocked("local_state_must_be_outside_repository")
            storage = PrivateGit(storage_repo, Path(temporary) / "storage", token) if storage_repo or token else None
            state = args.local_state or Path(temporary) / "editorial"
            result = execute(args.command, state, storage, args.date, args.validation_window, args.encrypted_mode,
                             validate_cinema=args.validate_cinema)
            if args.encrypted_mode and args.command in {"daily", "retry"}:
                cfg = cycle_settings(args.command, args.validation_window, args.encrypted_mode, args.validate_cinema)
                memory = Memory(state, result["day"], datetime.now(timezone.utc), cfg)
                review(memory)
        public = result
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # Actions logs on a public repository must omit private resource links.
            public = {k: v for k, v in result.items() if k not in {"url", "package_url", "delivery"}}
            if "delivery" in result:
                public["delivery"] = result["delivery"]["status"]
        print(json.dumps(public, ensure_ascii=False, sort_keys=True))
        if args.command == "review" and result["status"] != "owner_notification_created":
            return 2
        from .editorial_alt_models import enabled as alternative_test
        ready = {"review_pending"}
        if not (args.validate_cinema or alternative_test()):
            ready.add("rights_review_pending")
        if args.command in {"daily", "retry"} and any(s not in ready for s in result["articles"].values()):
            return 2
    except (Blocked, StorageError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, (Blocked, StorageError)) else type(exc).__name__
        print(json.dumps({"status": "blocked", "reason": message}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
