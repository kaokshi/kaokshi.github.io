"""Offline evidence tests for free PubMed fallback; absolutely no internet access."""
import io
import sys
from pathlib import Path
import unittest
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_pubmed import pubmed_sources
from editorial_research import science_research
from editorial_writer import Pending

SEARCH=b'<eSearchResult><IdList><Id>12345</Id><Id>67890</Id></IdList></eSearchResult>'
FETCH=b'''<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
<ArticleTitle>Randomized trial of a verified intervention</ArticleTitle>
<Abstract><AbstractText Label="RESULTS">Patients receiving the intervention experienced an observed improvement compared to control subjects.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
</Article></MedlineCitation></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID>67890</PMID><Article>
<ArticleTitle>Review of previous trials</ArticleTitle>
<Abstract><AbstractText>Several previous trials were summarized and discussed in this comprehensive review of treatment results.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList>
</Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>'''


class Download:
    def __init__(self,data): self.data=data
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,limit): return self.data[:limit]


class ResearchFallbackTests(unittest.TestCase):
    def test_pubmed_reads_real_record_and_excludes_review(self):
        calls=[]
        def open_url(url,timeout):
            calls.append((url,timeout))
            self.assertEqual(urlsplit(url).hostname,"eutils.ncbi.nlm.nih.gov")
            return Download(SEARCH if "esearch" in url else FETCH)
        result=pubmed_sources("discover",{"topic":"clinical intervention","search_query":"INVALID EUROPEPMC SYNTAX"},open_url=open_url)
        self.assertEqual(len(calls),2)
        self.assertEqual(parse_qs(urlsplit(calls[0][0]).query)["term"],["clinical intervention"])
        self.assertEqual(len(result["documents"]),1)
        self.assertEqual(result["documents"][0]["kind"],"primary")
        self.assertEqual(result["documents"][0]["url"],"https://pubmed.ncbi.nlm.nih.gov/12345/")
        self.assertIn("observed improvement",result["documents"][0]["text"])

    def test_fallback_only_on_real_outage(self):
        def unavailable(*args):raise Pending("fact_check_pending","Europe PMC timed out")
        def alternate(task,payload):return {"documents":[{"url":"https://pubmed.ncbi.nlm.nih.gov/12345/"}]}
        response=science_research("discover",{"topic":"experiment"},europe=unavailable,fallback=alternate)
        self.assertEqual(len(response["documents"]),1)

    def test_empty_europe_does_not_fake_outage(self):
        def empty(task,payload):return {"documents":[]}
        def must_not_call(*args):self.fail("unexpected fallback")
        self.assertEqual(science_research("discover",{"topic":"test"},
                        europe=empty,fallback=must_not_call),{"documents":[]})

    def test_both_unavailable_remain_pending(self):
        def fail(*args):raise Pending("fact_check_pending","timeout")
        with self.assertRaisesRegex(Pending,"no verified source"):
            science_research("discover",{"topic":"test"},europe=fail,fallback=fail)

    def test_untrusted_doctype_rejected(self):
        def open_url(url,timeout):return Download(b"<!DOCTYPE xml [<!ENTITY x 'evil'>]><eSearchResult/>")
        with self.assertRaisesRegex(Pending,"failed"):
            pubmed_sources("discover",{"topic":"bad"},open_url=open_url)

    def test_ambiguous_journal_article_is_not_claimed_primary(self):
        ambiguous=FETCH.replace(b"Randomized Controlled Trial",b"Journal Article")
        def open_url(url,timeout):return Download(SEARCH if "esearch" in url else ambiguous)
        result=pubmed_sources("discover",{"topic":"study"},open_url=open_url)
        self.assertEqual(result["documents"][0]["kind"],"secondary")

    def test_bad_id_does_not_make_request(self):
        def open_url(url,timeout):return Download(b"<eSearchResult><IdList><Id>12345<script></Id></IdList></eSearchResult>")
        self.assertEqual(pubmed_sources("discover",{"topic":"study"},open_url=open_url)["documents"],[])

if __name__=="__main__":
    unittest.main()
