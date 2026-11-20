"""Tests for advisory readability and science source discovery."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import editorial_enhancements as e

class FakeReadability:
    def __init__(self,text,preset):
        self.flesch_reading_easy=51.5

class FakeWorks:
    def search(self, query):
        assert query == "red sprites upper atmosphere"
        return self
    def get(self, per_page):
        return [{"id":"https://openalex.org/W1","display_name":"Paper about atmospheric red sprites",
                 "publication_year":2024,"doi":"https://doi.org/10.1000/test"}]

class TestEnhancements(unittest.TestCase):
    def test_all_channels_are_advisory(self):
        for channel in ("legends","science","cinema"):
            result=e.readability({"channel":channel,"body_html":"<p>Оригинальная статья.</p>"},FakeReadability)
            self.assertEqual(result["status"],"completed")
            self.assertIsNone(result["threshold"])
            self.assertEqual(result["purpose"],"advisory")

    def test_research_only_curated_science(self):
        result=e.science_sources({"slug":"science-01-krasnye-spraity","channel":"science"},FakeWorks)
        self.assertEqual(result["status"],"completed")
        self.assertFalse(result["fact_verified"])
        self.assertEqual(len(result["works"]),1)
        self.assertEqual(e.science_sources({"slug":"cinema-01-truman-show-real-photos","channel":"cinema"})["status"],"not_applicable")
        self.assertEqual(e.science_sources({"slug":"science-future","channel":"science"})["status"],"query_needs_editor")

    def test_does_not_authorize_any_article(self):
        path=ROOT/"content/articles/legends-01-prohodnaya-17.json"
        before=path.read_bytes()
        result=e.inspect([path],research=False)
        self.assertFalse(result["publication_authorized"])
        self.assertFalse(result["article_modified"])
        self.assertEqual(before,path.read_bytes())
if __name__=="__main__":
    unittest.main()
