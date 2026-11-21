"""Analyze authorized metadata exports locally. Never contact Dzen."""
from collections import Counter
from datetime import date


def analyze(rows=None, *, own=False, launched=False):
    if own and not launched:
        return {"status":"disabled_before_launch"}
    if rows is None:
        return {"status":"authorized_export_not_connected"}
    summary = {}
    dimensions = {}
    dates = {}
    for row in rows:
        if row.get("permission") != "authorized_metadata_export" or not row.get("source"):
            raise ValueError("Each observation requires permitted provenance")
        if any(k in row for k in ("body","body_html","article_text","paragraphs")):
            raise ValueError("Import metadata only")
        group = (row["channel"], row["topic"])
        bucket = summary.setdefault(group, Counter())
        bucket["posts"] += 1
        for key in ("heading_count","intro_length","illustrations","public_reactions"):
            if key in row:
                v = row[key]
                if type(v) is not int or v<0: raise ValueError("Invalid metadata count")
                bucket[key] += v
        if "published_on" in row:
            dates.setdefault(row["channel"], set()).add(date.fromisoformat(row["published_on"]))
        for field in ("headline_form","intro_style","section_structure","cover_type"):
            if field in row:
                key = (row["channel"],field,str(row[field]))
                bucket2 = dimensions.setdefault(key, Counter())
                bucket2["posts"] += 1
                bucket2["public_reactions"] += row.get("public_reactions",0)
                if own:
                    bucket2.update({k:row[k] for k in ("impressions","opens","completions","subscriptions")})
        if own:
            for key in ("impressions","opens","completions","engagements","subscriptions"):
                v = row[key]
                if type(v) is not int or v<0: raise ValueError("Invalid metric")
                bucket[key] += v
    groups = []
    for (channel,topic), counts in summary.items():
        result = dict(channel=channel,topic=topic,**counts)
        if own:
            result["ctr"] = counts["opens"]/counts["impressions"] if counts["impressions"] else None
        groups.append(result)
    return {"status":"completed","groups":groups,
            "formats":[dict(channel=c,field=f,value=v,**n) for (c,f,v),n in dimensions.items()],
            "cadence":[dict(channel=c,active_dates=len(days),span_days=(max(days)-min(days)).days+1)
                       for c,days in dates.items()]}


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    from editorial_ai_check import ROOT
    from editorial_auto_release import officially_launched
    from editorial_writer import private_path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--own", action="store_true")
    args = parser.parse_args()
    enabled = officially_launched(json.loads((ROOT/"config/site.json").read_text()))
    rows = None
    if args.export and (not args.own or enabled):
        rows = json.loads(private_path(args.export).read_text())
    print(json.dumps(analyze(rows, own=args.own, launched=enabled), ensure_ascii=False))
