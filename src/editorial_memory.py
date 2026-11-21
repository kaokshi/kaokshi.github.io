"""Persistent, private genre experience and shared topic registry for daily drafting."""
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
import re
import sqlite3

from editorial_agent_v1 import article_digest
from editorial_writer import topic_key


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
            "experience":experience(root,channel),"instruction":"Предложи пять разных новых конкретных тем. "
            "Учитывай использованные материалы всех жанров. Для науки предложи проверяемый вопрос, для мистики завязку."})
        db.execute("BEGIN IMMEDIATE")
        prior = reserved()
        if prior:
            return prior[0]
        used.update(v[0] for v in db.execute("SELECT topic FROM daily_topics"))
        for topic in result["topics"]:
            key = topic_key(topic)
            words = set(key.split())
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
    "duplicate_image": ("photo_plan", "Выбирай разные кадры и источники, исключая уже принятые изображения."),
    "cover_selection_pending": ("scene_plan", "Включи сцену, где предмет основной загадки различим в уменьшенном виде."),
    "section_headings_invalid": ("section_plan", "Сгруппируй соседние смысловые абзацы и озаглавь каждую группу."),
}


def recalled_provider(root, channel, provider):
    def invoke(task, payload):
        cues = [dict(row, reminder=REMINDERS[row["observed"]][1]) for row in experience(root,channel)
                if row["observed"] in REMINDERS and REMINDERS[row["observed"]][0] == task]
        return provider(task, {**payload, "genre_experience":cues} if cues else payload)
    invoke.identity = getattr(provider, "identity", None)
    return invoke
