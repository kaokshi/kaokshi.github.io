"""Daily private owner ZIP manifest tests (no delivery or public artifact)."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_daily_owner_packet import pack_day


def plan():
    out=[]
    for channel in ("legends","science","cinema"):
        for n in range(3):
            entry={"channel":channel,"topic":f"Новая архивная тема для {channel} номер {n}"}
            if channel=="science": entry["research_query"]="published studies evidence"
            out.append(entry)
    return {"date":"2026-11-21","tasks":out}


class DailyOwnerTests(unittest.TestCase):
    def test_missing_nine_cases_reported_honestly(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            work=root/"work"
            work.mkdir()
            out=root/"review.zip"
            report=pack_day(plan(),work,out)
            self.assertEqual(report["requested"],9)
            self.assertEqual(report["included"],0)
            self.assertEqual(report["not_included"],9)
            self.assertEqual(report["transport_status"],"not_connected")
            with ZipFile(out) as archive:
                self.assertEqual(set(archive.namelist()),{"MANIFEST.json","README.md"})
                self.assertEqual(len(json.loads(archive.read("MANIFEST.json"))["tasks"]),9)

    def test_refuses_in_place_output_or_overwrite(self):
        with TemporaryDirectory() as tmp:
            work=Path(tmp)/"private"
            work.mkdir()
            with self.assertRaisesRegex(ValueError,"outside"):
                pack_day(plan(),work,work/"review.zip")
            out=Path(tmp)/"review.zip"
            pack_day(plan(),work,out)
            with self.assertRaisesRegex(ValueError,"overwrite"):
                pack_day(plan(),work,out)


if __name__=="__main__":
    unittest.main()
