"""Persistent, private genre experience and shared topic registry for daily drafting."""
from contextlib import closing
from hashlib import sha256
import json
import re
import sqlite3

from editorial_agent_v1 import article_digest
from editorial_writer import topic_key
from editorial_layout import FIELD_LINE


def connect(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(root / "editorial-memory.sqlite3", timeout=30)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS versions(
            slug TEXT, digest TEXT, channel TEXT, title TEXT, data TEXT,
            PRIMARY KEY(slug,digest));
        CREATE TABLE IF NOT EXISTS observations(
            slug TEXT, digest TEXT, channel TEXT, defect TEXT,
            PRIMARY KEY(slug,digest,defect));
        CREATE TABLE IF NOT EXISTS daily_topics(
            day TEXT, channel TEXT, topic TEXT, normalized TEXT UNIQUE,
            PRIMARY KEY(day,channel));
        CREATE TABLE IF NOT EXISTS images(
            slug TEXT, file TEXT, sha256 TEXT, phash TEXT, cover INTEGER,
            PRIMARY KEY(slug,file,sha256));
    """)
    return db


def remember(root, case, assembly=None, asset_root=None):
    """Retain source and revision history. Only checked findings become retrieval cues."""
    with closing(connect(root)) as db, db:
        for v in case["versions"]:
            a = v["article"]
            digest = article_digest(a)
            if digest != v["text_sha256"]:
                raise ValueError("Memory source hash mismatch")
            db.execute("INSERT INTO versions VALUES(?,?,?,?,?) ON CONFLICT(slug,digest) DO UPDATE SET data=excluded.data",
                       (a["slug"], digest, a["channel"], a["title"], json.dumps(v, ensure_ascii=False)))
            report = v.get("report") or {}
            findings = set()
            if report.get("text_sha256") == digest:
                findings.update(report.get("findings", []))
                if report.get("facts", {}).get("status") == "fact_check_pending":
                    findings.add("fact_check_pending")
            if re.search(r"<p>\s*(?:start\s*:?\s*\d+|cta)\s*</p>", a["body_html"], re.I):
                findings.add("writer_field_in_prose")
            for defect in findings:
                db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?)",
                           (a["slug"], digest, a["channel"], defect))
        if assembly and asset_root:
            import imagehash
            from PIL import Image
            from editorial_cover import asset, verify_cover
            verify_cover(assembly, asset_root)
            for row in assembly["cover_selection"]["images"]:
                with Image.open(asset(asset_root, row["file"])) as img:
                    phash = str(imagehash.phash(img))
                db.execute("INSERT OR IGNORE INTO images VALUES(?,?,?,?,?)",
                           (assembly["slug"], row["file"], row["sha256"], phash, int(row["is_cover"])))


def image_conflicts(root, assembly, asset_root):
    import imagehash
    from PIL import Image
    from editorial_cover import asset
    with closing(connect(root)) as db:
        old = db.execute("SELECT slug,file,sha256,phash,cover FROM images WHERE slug<>?", (assembly["slug"],)).fetchall()
    conflicts = []
    for row in assembly["cover_selection"]["images"]:
        with Image.open(asset(asset_root, row["file"])) as im:
            current = imagehash.phash(im)
        for slug, name, digest, phash, cover in old:
            if name == row["file"] or digest == row["sha256"] or current - imagehash.hex_to_hash(phash) <= 5:
                conflicts.append({"article": slug, "prior_file": name, "candidate": row["file"], "prior_cover": bool(cover)})
    return conflicts


def experience(root, channel):
    with closing(connect(root)) as db:
        return [{"observed": defect, "articles": n} for defect,n in db.execute(
            "SELECT defect,COUNT(DISTINCT slug) AS n FROM observations WHERE channel=? "
            "GROUP BY defect ORDER BY n DESC,defect LIMIT 10", (channel,))]


def distinctive_terms(title):
    # Compare Russian inflections in short prior titles against longer proposed briefs.
    common = {"почему", "котор", "этого", "какие", "когда", "можно", "внутр", "загля",
              "этой", "своей", "через", "такое", "явлен", "влиян", "иссле", "научн"}
    return {word[:5] for word in topic_key(title).split() if len(word) >= 5 and word[:5] not in common}


def choose_topic(root, channel, day, provider, corpus):
    if channel not in {"legends","science"}:
        raise ValueError("Paused or unknown channel")
    with closing(connect(root)) as db, db:
        def reserved():
            return db.execute("SELECT topic FROM daily_topics WHERE day=? AND channel=?", (day,channel)).fetchone()
        prior = reserved()
        if prior:
            return prior[0]
        used = set(corpus)
        used.update(v[0] for v in db.execute("SELECT DISTINCT title FROM versions"))
        used.update(v[0] for v in db.execute("SELECT topic FROM daily_topics"))
        result = provider("topics", {"channel":channel, "used":sorted(used)[-80:],
            "experience":experience(root,channel),"instruction":(
            "Предложи три разные завязки вымышленных мистических рассказов. В каждой нужен герой, место и конкретное происшествие. Никаких пояснений."
            if channel=="legends" else "Предложи три конкретных научных вопроса, которые можно проверить по первичным исследованиям. Никаких вымышленных происшествий.")})
        db.execute("BEGIN IMMEDIATE")
        prior = reserved()
        if prior:
            return prior[0]
        used.update(v[0] for v in db.execute("SELECT topic FROM daily_topics"))
        for topic in result["topics"]:
            key = topic_key(topic)
            words = set(key.split())
            if channel=="legends" and ("научный вопрос" in key or "научное исследование" in key):
                continue
            terms = distinctive_terms(topic)
            if channel == "science" and any(len(terms & old) >= 2 and len(terms & old) * 2 >= len(old)
                                            for old in (distinctive_terms(title) for title in used) if old):
                continue
            if len(words)<3 or any(len(words & other)/len(words | other) >= .60
                                  for other in (set(topic_key(u).split()) for u in used)):
                continue
            db.execute("INSERT INTO daily_topics VALUES(?,?,?,?)", (day, channel, topic, key))
            return topic
    raise ValueError("No sufficiently distinct proposed topic")


# Approved editorial reminders. Observations select relevant examples, never change policy.
REMINDERS = {
    "writer_field_in_prose": ("write", "Абзацы содержат только прозу. Служебные start, heading и cta находятся в отдельных полях."),
    "short_article": ("write", "Разверни пример или сцену до финала, прежде чем проверить длину."),
    "fact_check_pending": ("extract_claims", "Разделяй проверяемые утверждения и привязывай вывод к масштабу первоисточника."),
    "similar_article": ("topics", "Измени завязку или исследовательский вопрос, сверившись с известными работами."),
    "media_production_pending": ("scene_plan","Проверь доступность генератора и сохрани конкретную сцену для каждой позиции."),
    "draft_rejected": ("write","Проверяй название, массив абзацев и отдельное поле подписки."),
    "duplicate_image": ("photo_plan", "Выбирай разные кадры и источники, исключая уже принятые изображения."),
    "cover_selection_pending": ("cover_selection", "Сверь миниатюру и независимое обоснование по видимому объекту. Включи сцену, где предмет основной загадки различим в уменьшенном виде."),
    "section_headings_invalid": ("section_plan", "Сгруппируй соседние смысловые абзацы и озаглавь каждую группу."),
}


def recalled_provider(root, channel, provider):
    def invoke(task, payload):
        cues = [dict(row, reminder=REMINDERS[row["observed"]][1]) for row in experience(root,channel)
                if row["observed"] in REMINDERS and REMINDERS[row["observed"]][0] == task]
        invoke.last_reminders = cues
        return provider(task, {**payload, "genre_experience":cues} if cues else payload)
    invoke.identity = getattr(provider, "identity", None)
    return invoke


def evaluate_field_reminder(root, channel, provider):
    """Blind, paired micro-task on unseen inputs; tests recognition, not article quality."""
    controls = (
        (["Сторож закрыл дверь.", "start:3", "На часах было три."], [2]),
        (["cta", "В заявке написали слово start.", "heading: Окно"], [1,3]),
        (["Вера произнесла: «Начали!»", "Канал называется «Старт»."], []),
    )
    if not any(r["observed"]=="writer_field_in_prose" for r in experience(root,channel)):
        return {"status":"no_matched_experience"}
    results = []
    reminder = REMINDERS["writer_field_in_prose"][1]
    with closing(connect(root)) as db:
        seen = [v[0] for v in db.execute("SELECT data FROM versions")]
    for lines, expected in controls:
        if any(all(line in text for line in lines) for text in seen):
            raise ValueError("Control appeared in editorial memory")
        pair = {}
        for variant in ("baseline","candidate"):
            instruction = "Укажи номера служебных строк от 1. Сохрани художественный текст."
            if variant == "candidate":
                instruction += " " + reminder
            reply = provider("field_review", {"lines":lines,"instruction":instruction})
            actual = reply["positions"]
            if any(type(n) is not int or not 1<=n<=len(lines) for n in actual):
                raise ValueError("Control response contains an invalid position")
            pair[variant] = {"positions":actual,"errors":len(set(expected)^set(actual))}
        results.append({"input":lines,"expected":expected,**pair})
    improved = (any(r["candidate"]["errors"]<r["baseline"]["errors"] for r in results)
                and all(r["candidate"]["errors"]<=r["baseline"]["errors"] for r in results))
    result = {"status":"completed","channel":channel,"model":getattr(provider,"identity",None),
              "controls":results,"improvement_demonstrated":improved,"activated":False,
              "rollback":"No deployment. Remove this retrieval cue by reverting its versioned rule."}
    from editorial_revision import save_case
    save_case(root/f"field-reminder-{channel}.json",result)
    return result


def historical_drafts(root):
    with closing(connect(root)) as db:
        return [json.loads(row[0])["article"] for row in db.execute("SELECT data FROM versions")]


def remember_failure(root, slug, channel, status, reason, raw=None):
    data = json.dumps({"status":status,"reason":reason,"response":raw},sort_keys=True,ensure_ascii=False)
    key = sha256(data.encode()).hexdigest()
    with closing(connect(root)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS failures(slug TEXT,digest TEXT,channel TEXT,data TEXT,"
                   "PRIMARY KEY(slug,digest))")
        db.execute("INSERT OR IGNORE INTO failures VALUES(?,?,?,?)",(slug,key,channel,data))
        db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?)",(slug,key,channel,status))
        if isinstance(raw, dict) and isinstance(raw.get("paragraphs"), list):
            if any(FIELD_LINE.match(line) for para in raw["paragraphs"] if isinstance(para, str)
                   for line in para.splitlines()):
                db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?)",
                           (slug, key, channel, "writer_field_in_prose"))


def proposal_report(root, channel):
    roles = {"topics":"выбор тем","write":"автор","section_plan":"редактор","extract_claims":"фактчекинг",
             "scene_plan":"иллюстратор","photo_plan":"фоторедактор","cover_selection":"обложка"}
    proposals = []
    for row in experience(root,channel):
        if row["observed"] in REMINDERS:
            task, instruction = REMINDERS[row["observed"]]
            proposals.append({**row,"role":roles[task],"proposal":instruction,"state":"requires_holdout"})
    return proposals


def writer_experience_report(root, channel, prior, response):
    """Measure the known field leak on the raw model output before any repairs."""
    lines = [line for paragraph in response.get("paragraphs", []) if isinstance(paragraph, str)
             for line in paragraph.splitlines()]
    previous = []
    with closing(connect(root)) as db:
        for slug, digest in db.execute(
                "SELECT slug,digest FROM observations WHERE channel=? AND defect='writer_field_in_prose'",
                (channel,)):
            previous.append({"article": slug, "text_sha256": digest})
    seen = sum(bool(FIELD_LINE.match(line)) for line in lines)
    return {"channel": channel, "prior_observations": previous, "applied_reminders": prior,
            "raw_output_sha256": sha256(json.dumps(response, sort_keys=True).encode()).hexdigest(),
            "field_lines": seen, "measured_lines": len(lines),
            "outcome": ("no_comparable_history" if not any(r["observed"] == "writer_field_in_prose" for r in prior) else "no_prose" if not lines
                        else "defect_recurred" if seen else "defect_absent_on_this_draft"),
            "prompt_deployed": False}
