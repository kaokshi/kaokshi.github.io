"""Parse owner-exported Dzen XLSX snapshots; never infer daily metrics or CTR."""
import hashlib
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from openpyxl import load_workbook

CHANNELS = {"science": "За гранью очевидного", "cinema": "Кадр за Кадром", "legends": "Легенды тёмных улиц"}
HEADERS = ("Дата публикации", "Платформа", "Тип публикации", "Публикация", "Ссылка на публикацию", "Показы", "Открытия", "Дочитывания и просмотры", "Время просмотра в минутах", "Комментарии", "Подписки", "Лайки")
FIELDS = ("impressions", "opens", "reads_and_views", "view_minutes", "comments", "subscriptions", "likes")

def parse_report(path, channel):
    path = Path(path)
    if channel not in CHANNELS or path.stat().st_size > 5_000_000:
        raise ValueError("invalid_channel_or_file_size")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries)>200 or sum(x.file_size for x in entries)>30_000_000:
            raise ValueError("workbook_expansion_limit")
    wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        sheet = wb["Статистика"]
        sheet.reset_dimensions()
        rows = iter(sheet.iter_rows(values_only=True, max_col=12))
        title = next(rows)[0]
        match = re.fullmatch(r'Публикации канала "([^"]+)", по времени события, период (\d{4}-\d{2}-\d{2}) - (\d{4}-\d{2}-\d{2})', str(title))
        if not match or match[1] != CHANNELS[channel]:
            raise ValueError("channel_or_report_scope_mismatch")
        start, end = map(date.fromisoformat, match.groups()[1:])
        if not 0 <= (end-start).days <= 366:
            raise ValueError("invalid_period")
        header = next((r for _,r in zip(range(5),rows) if any(v is not None for v in r)), None)
        if header != HEADERS:
            raise ValueError("unsupported_header")
        def metrics(row):
            values = row[5:12]
            if any(type(v) is not int or not 0 <= v <= 10**12 for v in values):
                raise ValueError("invalid_metric")
            return dict(zip(FIELDS,values))
        total = next(rows)
        if total[0] != "Всего":
            raise ValueError("missing_total")
        totals = metrics(total)
        articles, seen = [], set()
        for n,row in enumerate(rows):
            if n>=10000:
                raise ValueError("row_limit")
            if not any(v is not None for v in row):
                continue
            stamp,platform,kind,name,url = row[:5]
            if not isinstance(stamp,datetime) or platform!="Все платформы" or not isinstance(name,str) or not 0<len(name)<=1000 or not isinstance(kind,str) or not isinstance(url,str):
                raise ValueError("invalid_article")
            p=urlsplit(url)
            if p.scheme!="https" or p.netloc!="dzen.ru" or not re.fullmatch(r"/media/id/[0-9a-f]{24}/[0-9a-f]{24}",p.path):
                raise ValueError("unsupported_article_url")
            canonical="https://dzen.ru"+p.path
            if canonical in seen:
                raise ValueError("duplicate_article")
            seen.add(canonical)
            articles.append({"publication_at":stamp.isoformat(),"kind":kind,"title":name,"url":canonical,"metrics":metrics(row)})
        sums={k:sum(a["metrics"][k] for a in articles) for k in FIELDS}
        if sums!=totals:
            raise ValueError("total_mismatch")
        return {"schema":"zen-dzen-publication-snapshot/v1","source":"owner_export_dzen_studio","channel":channel,"period_start":start.isoformat(),"period_end":end.isoformat(),"scope":"event_time_all_platforms","file_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"totals":totals,"articles":articles,"ctr":None,"daily":None,"automatic_sync":False,"publication_authorized":False}
    finally:
        wb.close()
