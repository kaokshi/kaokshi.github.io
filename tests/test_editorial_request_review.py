"""Verify on-demand latest archive selection and safe extraction."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile,ZIP_DEFLATED
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_request_review import candidate_runs,safely_extract_plaintext,WORKFLOW_NAME

def make_zip(names):
    out=io.BytesIO()
    with ZipFile(out,"w",ZIP_DEFLATED) as archive:
        for path,data in names.items():
            archive.writestr(path,data)
    return out.getvalue()

class OnDemandReviewTests(unittest.TestCase):
    def test_latest_finished_runs_only(self):
        def command(argv):
            return json.dumps([
                {"databaseId":100,"status":"in_progress","name":WORKFLOW_NAME},
                {"databaseId":99,"status":"completed","name":WORKFLOW_NAME},
                {"databaseId":98,"status":"completed","name":"Another workflow"},
                {"databaseId":97,"status":"completed","name":WORKFLOW_NAME},
            ])
        self.assertEqual(candidate_runs("kaokshi/kaokshi.github.io",command=command),["99","97"])

    def test_extract_exact_real_review_with_no_publication(self):
        data={"MANIFEST.json":json.dumps({"articles":[{"channel":"legends"}],
                         "publication_authorized":False,"rss_authorized":False}),
              "articles/01-legends/draft.md":"Приватный тестовый текст"}
        with TemporaryDirectory() as td:
            folder=Path(td)/"private"
            manifest=safely_extract_plaintext(make_zip(data),folder)
            self.assertEqual(len(manifest["articles"]),1)
            self.assertEqual((folder/"articles/01-legends/draft.md").read_text(),"Приватный тестовый текст")
            with self.assertRaisesRegex(ValueError,"already exists"):
                safely_extract_plaintext(make_zip(data),folder)

    def test_reject_path_traversal_and_publication(self):
        good={"MANIFEST.json":json.dumps({"articles":[],"publication_authorized":False}),
              "../outside.txt":"sensitive"}
        with TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,"Unsafe"):
                safely_extract_plaintext(make_zip(good),Path(td)/"private")
            altered={"MANIFEST.json":json.dumps({"articles":[],"publication_authorized":True})}
            with self.assertRaisesRegex(ValueError,"publishing"):
                safely_extract_plaintext(make_zip(altered),Path(td)/"private")

if __name__=="__main__":
    unittest.main()
