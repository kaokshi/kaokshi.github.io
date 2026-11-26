"""Strict offline import of Dzen Studio publication statistics (observed XLSX format).
No network, no write to production, no inferred CTR or income.
"""
import hashlib
import io
import json
import math
import re
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

CHANNELS = {"cinema": "Кадр за Кадром", "science": "За гранью очевидного",
            "legends": "Легенды темных улиц"}
HEADERS = ("Дата публикации", "Платформа", "Тип публикации", "Публикация",
           "Ссылка на публикацию", "Показы", "Открытия", "Дочитывания и просмотры",
           "Время просмотра в минутах", "Комментарии", "Подписки", "Лайки")
METRICS = ("impressions", "opens", "reads_and_views", "watch_minutes",
           "comments", "subscriptions", "likes")
MAX_BYTES = 10_000_000

def norm(s):
    return s.strip().casefold().replace("ё", "е")

def xml(z, path):
    raw = z.read(path)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe_xml")
    return ET.fromstring(raw)

def cells(root, strings):
    rows = {}
    for cell in root.findall(".//{*}sheetData/{*}row/{*}c"):
        ref = cell.get("r", "")
        m = re.fullmatch(r"([A-Z])([1-9][0-9]{0,5})", ref)
        if not m or cell.find("{*}f") is not None:
            raise ValueError("unsupported_cell")
        col, row = ord(m[1])-65, int(m[2])
        if col >= 12 or row > 50000:
            raise ValueError("unsupported_dimensions")
        val = cell.findtext("{*}v")
        kind = cell.get("t", "n")
        if kind == "s":
            val = strings[int(val)]
        elif kind == "inlineStr":
            val = "".join(t.text or "" for t in cell.findall(".//{*}t"))
        elif kind == "n" and val is not None:
            val = float(val)
            if not math.isfinite(val):
                raise ValueError("invalid_numeric")
            if val.is_integer():
                val = int(val)
        elif kind != "n":
            raise ValueError("unsupported_cell_type")
        r = rows.setdefault(row, [None]*12)
        if r[col] is not None:
            raise ValueError("duplicate_cell")
        r[col] = val
    return rows

def counts(values, keys):
    result = {}
    for k, v in zip(keys, values):
        if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
            raise ValueError("invalid_metric")
        if k != "watch_minutes" and int(v) != v:
            raise ValueError("noninteger_count")
        result[k] = int(v) if int(v) == v else v
    return result

def parse_sheet(rows, channel, name):
    n = 12 if name == "Статистика" else 9
    keys = METRICS[:n-5]
    title = rows.get(1, [None])[0]
    m = re.fullmatch(r'Публикации канала "(.+)", по времени события, период (\d{4}-\d{2}-\d{2}) - (\d{4}-\d{2}-\d{2})', title or "")
    if not m or norm(m[1]) != norm(CHANNELS[channel]):
        raise ValueError("channel_or_report_kind_mismatch")
    start, end = date.fromisoformat(m[2]), date.fromisoformat(m[3])
    if start > end or tuple(rows.get(3, [])[:n]) != HEADERS[:n]:
        raise ValueError("invalid_period_or_header")
    total = rows.get(4, [])
    if len(total) != 12 or total[0] != "Всего":
        raise ValueError("missing_total")
    totals = counts(total[5:n], keys)
    publications, owners = {}, set()
    platform = "Все платформы" if n == 12 else "Дзен"
    for idx, row in sorted(rows.items()):
        if idx <= 4 or all(v is None for v in row):
            continue
        if row[1] != platform or not isinstance(row[2], str) or not isinstance(row[3], str):
            raise ValueError("invalid_publication")
        link = re.fullmatch(r"https://dzen\.ru/media/id/([a-f0-9]{24})/([a-f0-9]{24})", row[4] or "")
        if not link or link[2] in publications:
            raise ValueError("invalid_or_duplicate_publication_id")
        owners.add(link[1])
        serial = row[0]
        if type(serial) not in (int, float) or not 25569 <= serial < 100000:
            raise ValueError("invalid_publication_date")
        published = (datetime(1899, 12, 30) + timedelta(days=serial)).isoformat(timespec="seconds")
        publications[link[2]] = {"id": link[2], "published_at_excel_local": published,
                                "type": row[2], "title": row[3], "url": row[4],
                                "metrics": counts(row[5:n], keys)}
    if len(owners) > 1:
        raise ValueError("mixed_channel_ids")
    sums = {k: sum(p["metrics"][k] for p in publications.values()) for k in keys}
    return {"start": start.isoformat(), "end": end.isoformat(),
            "channel_id": next(iter(owners), None), "totals": totals,
            "publication_count": len(publications),
            "publications": [publications[k] for k in sorted(publications)],
            "total_minus_publication_sum": {k: totals[k]-sums[k] for k in keys}}

def import_report(data, channel, expected_channel_id=None):
    if channel not in CHANNELS or not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("invalid_input")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        entries = z.infolist()
        if len(entries) > 100 or sum(e.file_size for e in entries) > 50_000_000 or len({e.filename for e in entries}) != len(entries):
            raise ValueError("unsafe_archive")
        book = xml(z, "xl/workbook.xml")
        props = book.find("{*}workbookPr")
        if props is not None and props.get("date1904") in ("1", "true"):
            raise ValueError("unsupported_date_epoch")
        rels = {r.get("Id"): r.get("Target") for r in xml(z, "xl/_rels/workbook.xml.rels")
                if r.get("TargetMode") != "External"}
        strings = ["".join(t.text or "" for t in si.findall(".//{*}t"))
                   for si in xml(z, "xl/sharedStrings.xml").findall("{*}si")] if "xl/sharedStrings.xml" in z.namelist() else []
        sheets = {}
        for s in book.findall(".//{*}sheets/{*}sheet"):
            name = s.get("name")
            rid = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rels.get(rid, "")
            target = target.removeprefix("/xl/")
            if name not in ("Статистика", "Дзен") or name in sheets or not re.fullmatch(r"worksheets/sheet[0-9]+\.xml", target):
                raise ValueError("unsupported_sheet")
            sheets[name] = parse_sheet(cells(xml(z, "xl/"+target), strings), channel, name)
        if set(sheets) != {"Статистика", "Дзен"}:
            raise ValueError("missing_sheet")
    main, platform = sheets["Статистика"], sheets["Дзен"]
    if any(main[k] != platform[k] for k in ("start", "end", "channel_id")):
        raise ValueError("sheet_identity_mismatch")
    if expected_channel_id is not None and main["channel_id"] != expected_channel_id:
        raise ValueError("channel_binding_mismatch")
    result = {"schema": "zen-dzen-studio/v1", "source": "dzen_studio_xlsx",
              "channel": channel, "period_basis": "event_time", "granularity": "period",
              "channel_binding": "expected_id_matched" if expected_channel_id is not None else "title_and_internal_id_only",
              "all_platforms": main, "dzen_platform": platform,
              "ctr": None, "revenue": None}
    # Exact semantic replay is independent of ZIP metadata and row order.
    result["snapshot_id"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    result["file_sha256"] = hashlib.sha256(data).hexdigest()
    return result
