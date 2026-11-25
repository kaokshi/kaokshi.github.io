"""Cinema evidence stays behind release, identity and image-rights gates."""
from datetime import date, timedelta
import unittest


from src.editorial_cinema import evidence
from src.editorial_http import Blocked
from src.movie_autonomous_research import release_dates, release_years


class CinemaReleaseTests(unittest.TestCase):
    def test_catalog_excludes_unconfirmed_and_future_releases(self):
        future = date.today() + timedelta(days=20)
        plan = {"film_title": "Тест фильма", "year": future.year,
                "format": "review", "spoilers": False}
        identity = {"film": {"years": [future.year], "directors": ["Director"], "cast": ["Cast"],
                             "release_dates": [future.isoformat()]}}
        with self.assertRaisesRegex(Blocked, "film_release_not_confirmed"):
            evidence(plan, {}, resolver=lambda *_: identity)

    def test_original_release_boundary_and_rerelease(self):
        from unittest.mock import Mock

        plan = {"film_title": "Synthetic fixture", "year": 1998,
                "format": "review", "spoilers": False}
        resolver = Mock()
        with self.assertRaisesRegex(Blocked, "film_original_release_before_minimum"):
            evidence(plan, {}, resolver=resolver)
        resolver.assert_not_called()

        plan["year"] = 1999
        film = {"qid": "Q1", "title": "Synthetic fixture", "original_title": "Synthetic fixture",
                "years": [1999, 1998], "directors": ["Director"], "cast": ["Cast"]}
        resolver.return_value = {"film": film, "documents": [{"text": "Synthetic catalogue fixture"}]}
        source = {"url": "https://www.paramount.com/fixture", "status": "accessible",
                  "text": "Independent source fixture about Synthetic fixture"}
        fetch = Mock(return_value=[source])
        with self.assertRaisesRegex(Blocked, "film_original_release_before_minimum"):
            evidence(plan, {}, resolver=resolver, fetch=fetch)
        fetch.assert_not_called()

        film["years"] = [2025, 1999]
        self.assertEqual(evidence(plan, {}, resolver=resolver, fetch=fetch)["film"]["year"], 1999)
        with self.assertRaisesRegex(Blocked, "film_original_release_year_mismatch"):
            evidence({**plan, "year": 2025}, {}, resolver=resolver, fetch=fetch)

    def test_synthetic_film_requires_independent_source_and_limits_plot_detail(self):
        plan = {"film_title": "Synthetic fixture", "year": 1999, "format": "retelling",
                "spoilers": True, "sources": ["https://www.paramount.com/fixture"]}
        film = {"qid": "Q1", "title": "Synthetic fixture", "original_title": "Synthetic fixture",
                "years": [1999], "cast": ["Actor"], "directors": ["Director"]}
        def resolve(*_):
            return {"film": film, "documents": [{"text": "Synthetic catalogue fixture"}]}
        source = {"status": "accessible", "url": plan["sources"][0],
                  "text": "Independent source fixture about Synthetic fixture"}
        result = evidence(plan, {}, resolver=resolve, fetch=lambda *_: [source])
        self.assertEqual(result["format"], "review")
        self.assertFalse(result["spoilers"])
        self.assertFalse(result["detailed_plot_evidence"])
        with self.assertRaisesRegex(Blocked, "film_independent_source_unavailable"):
            evidence(plan, {}, resolver=resolve, fetch=lambda *_: [])

    def test_release_date_requires_day_precision_and_valid_calendar(self):
        def claim(value, precision, rank="normal"):
            return {"rank": rank, "mainsnak": {"datavalue": {"value": {"time": value, "precision": precision}}}}
        entity = {"claims": {"P577": [claim("+1998-06-05T00:00:00Z", 11),
            claim("+1998-00-00T00:00:00Z", 9), claim("+1998-02-30T00:00:00Z", 11),
            claim("+1998-06-04T00:00:00Z", 11, "deprecated")]}}
        self.assertEqual(release_dates(entity), ["1998-06-05"])
        self.assertEqual(release_years(entity), [1998, 1998, 1998])

    def test_private_still_has_provenance_without_a_rights_requirement(self):
        from src.editorial_cinema import fetch_still, registered_stills
        from src.editorial_memory import digest

        film = {"qid": "Q1", "original_title": "Fixture", "year": 2001}
        still = {"original_title": "Fixture", "year": 2001, "scene": "Synthetic fixture",
                 "source_type": "official_press", "sha256": digest(b"still"),
                 "source_page": "https://www.paramount.com/fixture", "asset_url": "https://www.paramount.com/still.jpg"}
        self.assertEqual(registered_stills(film, {"Q1": {"stills": [still]}}), [still])
        raw, metadata = fetch_still(still, film, downloader=lambda *_, **__: b"still")
        self.assertEqual(raw, b"still")
        self.assertEqual(metadata["rights_status"], "not_checked")
        with self.assertRaisesRegex(Blocked, "film_still_sha_mismatch"):
            fetch_still(still, film, downloader=lambda *_, **__: b"wrong")
        with self.assertRaisesRegex(Blocked, "url_outside_allowed_sources"):
            fetch_still({**still, "source_page": "https://other.example/fixture"}, film)
