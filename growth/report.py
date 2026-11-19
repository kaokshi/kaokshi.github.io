#!/usr/bin/env python3
"""Offline, read-only analysis of two explicitly supplied CSV exports."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

CHANNELS = ("cinema", "legends", "science")
ORGANIC = ("date", "channel", "article_id", "impressions", "opens", "reads", "subscribers_delta")
ADS = ("date", "channel", "campaign_id", "spend_rub", "impressions", "clicks", "paid_subscriptions", "paid_reads")

def load(path, required, numbers):
    if not path:
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: missing columns {set(required)-set(reader.fieldnames or [])}")
        rows = []
        for lineno, row in enumerate(reader, 2):
            if row["channel"] not in CHANNELS:
                raise ValueError(f"{path}:{lineno}: unknown channel")
            values = {"channel": row["channel"]}
            for field in numbers:
                raw = (row.get(field) or "").strip().replace(",", ".")
                # Blank means metric is absent: not zero.
                if not raw:
                    values[field] = None
                else:
                    value = float(raw)
                    if value < 0 and field != "subscribers_delta":
                        raise ValueError(f"{path}:{lineno}: {field} negative")
                    values[field] = value
            rows.append(values)
        return rows

def divide(n, d):
    return n/d if d not in (0, None) and n is not None else None

def fmt(value, suffix="", digits=1):
    return "н/д" if value is None else f"{value:,.{digits}f}{suffix}".replace(",", " ")

def analyze(organic, ads):
    lines = ["# Отчёт редакции по трём каналам", "",
             "Источники: CSV из Студии Дзена и отдельно из VK Рекламы. "
             "Органические и рекламные показатели **не смешиваются**.", ""]
    for channel in CHANNELS:
        org = [row for row in organic if row["channel"] == channel]
        paid = [row for row in ads if row["channel"] == channel]
        def sum_or_none(rows, key):
            xs = [r[key] for r in rows if r.get(key) is not None]
            return sum(xs) if xs else None
        oi=sum_or_none(org,"impressions"); op=sum_or_none(org,"opens"); rd=sum_or_none(org,"reads")
        spend=sum_or_none(paid,"spend_rub"); ps=sum_or_none(paid,"paid_subscriptions")
        pr=sum_or_none(paid,"paid_reads")
        lines.extend([f"## {channel}",f"- Органика: показы — {fmt(oi, digits=0)}; открытия — {fmt(op, digits=0)}; дочитывания — {fmt(rd, digits=0)}",
                      f"- CTR органики: {fmt(100*divide(op,oi) if divide(op,oi) is not None else None, '%')}",
                      f"- Доля дочитываний: {fmt(100*divide(rd,op) if divide(rd,op) is not None else None, '%')}",
                      f"- VK: расходы — {fmt(spend, ' ₽', 2)}, платные подписки — {fmt(ps, digits=0)}, платные дочитывания — {fmt(pr, digits=0)}",
                      f"- Стоимость платной подписки: {fmt(divide(spend,ps), ' ₽', 2)}",
                      f"- Стоимость платного дочитывания: {fmt(divide(spend,pr), ' ₽', 2)}", ""])
    lines.append("**Решение по бюджету: только после отдельного одобрения владельца.**")
    return "\n".join(lines)+"\n"

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--organic",type=Path)
    parser.add_argument("--ads",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    org=load(args.organic,ORGANIC,("impressions","opens","reads","subscribers_delta"))
    paid=load(args.ads,ADS,("spend_rub","impressions","clicks","paid_subscriptions","paid_reads"))
    report=analyze(org,paid)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(report,encoding="utf8")
    else:
        print(report)
if __name__=="__main__":
    main()
