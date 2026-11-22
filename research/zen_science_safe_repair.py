#!/usr/bin/env python3
"""One content-preserving, SOURCE-AWARE science repair for the same immutable article.

No model calls. Download the prior completed private-in-runner GitHub artifact via
GitHub Actions; trim one unjustified numeric Kp prediction, fix the Russian CTA
recognizer, recompute the current version's four AI detectors, rebuild the exact
article with the original eight NASA images. No publication.
"""
import hashlib,json,re,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"research"),str(ROOT/"src")]
from editorial_reader_quality import text_stats,quality_flags
import zen_autonomous_science_owner_review as science

OLD_HASH="a5642220f4cbedff77ff1ec4314c929d2e784e85616e9e526c3486e924630c27"
UNVERIFIED_KP=r"Амплитуда смещения коррелирует с планетным индексом Kp: при Kp=5[^.]+\."
REPLACEMENT="Для оценки видимости используют текущие прогнозы космической погоды."
def stats(text):
    d=text_stats(text,"science")
    # Russian CTA wording may use "подпишитесь", not only "подписывайтесь".
    d["subscription_cta_at_end"]=bool(re.search(
       r"\bподпиш(?:итесь|ись)\b|\bподпис(?:ывайтесь|аться)\b",
       text[-600:],re.I))
    return {"chars_no_spaces":d["chars_without_whitespace"],
      "bold_headers":d["bold_section_headers"],"cta":d["subscription_cta_at_end"],
      "repeated_fivegrams":d["repeated_fivegrams"],"flags":quality_flags(d)}
def run(src,work):
    work.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(src) as z:
        safe=[f for f in z.namelist() if f.startswith("images/") and
          f.endswith(".jpg") and ".." not in f]
        for name in ("final.md","image_sources.json"):
            if name not in z.namelist():raise RuntimeError("missing_input_"+name)
        old=z.read("final.md").decode("utf8")
        sources=json.loads(z.read("image_sources.json"))
        if len(safe)<7 or len(sources)<7:raise RuntimeError("insufficient_original_images")
        for p in safe:
            out=work/p
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_bytes(z.read(p))
    oldhash=hashlib.sha256(old.encode()).hexdigest()
    if oldhash!=OLD_HASH:raise RuntimeError("previous_final_sha_mismatch")
    updated,n=re.subn(UNVERIFIED_KP,REPLACEMENT,old,count=1)
    if n!=1:raise RuntimeError("numeric_science_claim_not_found_no_uncontrolled_edit")
    checks=stats(updated)
    if checks["flags"]:raise RuntimeError("still_noncompliant_after_safe_repair_"+",".join(checks["flags"]))
    digest=hashlib.sha256(updated.encode()).hexdigest()
    (work/"final.md").write_text(updated,encoding="utf8")
    (work/"image_sources.json").write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding="utf8")
    science.docx(updated,sources,work/"article.docx")
    (work/"article.html").write_text(science.article_html(updated,sources),encoding="utf8")
    doc_report=science.four_detectors(updated,work)
    output={
     "schema":"zen-science-owner-review-safe-repair-v1",
     "title":science.TITLE,"channel":"science",
     "previous_final_sha256":oldhash,"final_sha256":digest,
     "changed_from_previous_version":digest!=oldhash,
     "changes":["Removed unsupported exact Kp-to-latitude numeric prediction",
                "Fixed Russian CTA detection (подпишитесь) without changing CTA wording"],
     "editorial_revision_count":2,
     "new_mechanical_checks":checks,
     "ai_diagnostics_current_final_sha":doc_report,
     "images_count":len(sources),"images_from_nasa_originals":True,
     "cover_choice":"strongest of retrieved pictures by declared image heuristic",
     "section_heading_before_images":True,"captions":False,
     "image_rights_reviewed":False,
     "claim_by_claim_fact_check_completed":False,
     "external_plagiarism_completed":False,
     "owner_approved":False,"publication_authorized":False,
     "status":"readable_review_ready_not_for_release"}
    assert doc_report.get("sha256")==digest
    assert doc_report.get("all_completed") is True
    assert (work/"article.html").read_text().count("<img ")==len(sources)
    (work/"report.json").write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    archive=work/"science-corrected-owner-review.zip"
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
       for filename in ("final.md","article.docx","article.html","report.json","image_sources.json"):
           z.write(work/filename,arcname=filename)
       for p in sorted((work/"images").glob("*.jpg")):z.write(p,arcname="images/"+p.name)
    return output
if __name__=="__main__":
    if len(sys.argv)!=2:raise SystemExit("usage: script <readable_artifact.zip>")
    path=Path(sys.argv[1])
    folder=Path(__import__("os").environ.get("RUNNER_TEMP","/tmp"))/"zen-science-repaired"
    try:out=run(path,folder)
    except Exception as e:
       folder.mkdir(parents=True,exist_ok=True)
       (folder/"failure.json").write_text(json.dumps({"status":"failed","error_type":type(e).__name__}),encoding="utf8")
       raise
    print("ZEN_SCIENCE_SAFE_REPAIR_REPORT",json.dumps(out,ensure_ascii=False))
