"""Opt-in Dzen Studio browser pilot. No automatic login or challenge bypass."""
import argparse
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from zen_dzen_xlsx_import import parse_report
from zen_dzen_snapshot_store import import_snapshot

def check_url(url):
    p=urlsplit(url)
    if p.scheme!="https" or p.netloc!="dzen.ru" or not p.path.startswith("/profile/editor") or p.username or p.password:
        raise ValueError("studio_navigation_not_allowed")

def capture(page, destination):
    check_url(page.url)
    def guard():
        check_url(page.url)
        if page.get_by_text("Статистика канала",exact=True).count()!=1:
            raise RuntimeError("studio_statistics_not_confirmed")
        for text in ("Войти", "Подтвердите, что вы не робот", "Введите код"):
            if page.get_by_text(text,exact=True).count():
                raise RuntimeError("owner_action_required")
    guard()
    buttons=page.get_by_role("button",name="Отчёт",exact=True)
    if buttons.count()!=1:
        raise RuntimeError("report_button_not_unique")
    buttons.click(timeout=10000)
    period=page.get_by_text("90 дней",exact=True)
    if period.count()!=1:
        raise RuntimeError("report_period_not_unique")
    period.click(timeout=10000)
    guard()
    if buttons.count()!=2:
        raise RuntimeError("report_confirmation_not_unique")
    with page.expect_download(timeout=30000) as pending:
        buttons.last.click(timeout=10000)
    download=pending.value
    if not download.suggested_filename.lower().endswith(".xlsx"):
        raise RuntimeError("unexpected_download_type")
    download.save_as(str(destination))
    if destination.stat().st_size>5_000_000:
        raise RuntimeError("download_size_limit")

def run(profile, studio_url, db, channel, *, owner_confirmed=False):
    if not owner_confirmed or channel!="science":
        raise ValueError("pilot_not_authorized")
    check_url(studio_url)
    profile=Path(profile).resolve()
    db=Path(db).resolve()
    if not profile.is_dir() or profile.name!="science":
        raise ValueError("dedicated_science_profile_required")
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="zen-report-") as tmp, sync_playwright() as p:
        dest=Path(tmp)/"report.xlsx"
        context=p.chromium.launch_persistent_context(str(profile),headless=False,accept_downloads=True)
        try:
            page=context.pages[0] if context.pages else context.new_page()
            page.goto(studio_url,wait_until="domcontentloaded",timeout=30000)
            capture(page,dest)
            report=parse_report(dest,channel)
            if (date.fromisoformat(report["period_end"])-date.fromisoformat(report["period_start"])).days!=89:
                raise ValueError("unexpected_report_period")
            return import_snapshot(db,dest,channel)
        finally:
            context.close()

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--profile",required=True)
    parser.add_argument("--studio-url",required=True)
    parser.add_argument("--db",required=True)
    parser.add_argument("--owner-confirmed",action="store_true")
    args=parser.parse_args()
    print(run(args.profile,args.studio_url,args.db,"science",owner_confirmed=args.owner_confirmed))
