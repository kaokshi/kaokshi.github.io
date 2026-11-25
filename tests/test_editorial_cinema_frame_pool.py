"""Synthetic offline regression for Opus frame cache, adapted from session 3."""
import unittest
from unittest.mock import patch
from src import editorial_routerai_two_articles as t
from src.editorial_http import Blocked

class FramePoolTests(unittest.TestCase):
    def test_fetch_decode_once_per_frame_and_score_once_per_subject(self):
        film={"qid":"Q1","year":2020}
        rows=[{"sha256":str(i)} for i in range(20)]
        cache={}
        with patch.object(t,"fetch_still",side_effect=lambda row,*a,**k:(row["sha256"].encode(),row)) as fetch, patch.object(t,"decode_candidate",side_effect=lambda raw:(raw,None)) as decode, patch.object(t,"compare_pixels",return_value=([0.5],[])) as compare:
            for subject in ["a","b","c","d","e","f","g","a"]:
                self.assertEqual(len(t.ranked_film_stills(cache,film,rows+rows,subject,None)),20)
            self.assertEqual(fetch.call_count,20)
            self.assertEqual(decode.call_count,20)
            self.assertEqual(compare.call_count,140)
    def test_failed_fetch_cached(self):
        film={"qid":"Q1","year":2020}
        with patch.object(t,"fetch_still",side_effect=Blocked("synthetic_failure")) as fetch:
            cache={}
            for subject in ["a","b"]:
                self.assertEqual(t.ranked_film_stills(cache,film,[{"sha256":"1"}],subject,None),[])
            self.assertEqual(fetch.call_count,1)
