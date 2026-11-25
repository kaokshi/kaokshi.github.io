"""Cinema evidence stays behind release, identity and image-rights gates."""
from datetime import date, timedelta
import unittest


from src.editorial_cinema import evidence
from src.editorial_http import Blocked
from src.movie_autonomous_research import release_dates


class CinemaReleaseTests(unittest.TestCase):
    def test_catalog_excludes_unconfirmed_and_future_releases(self):
        future = date.today() + timedelta(days=20)
        plan = {"film_title": "Тест фильма", "year": future.year,
                "format": "review", "spoilers": False}
        identity = {"film": {"years": [future.year], "directors": ["Director"], "cast": ["Cast"],
                             "release_dates": [future.isoformat()]}}
        with self.assertRaisesRegex(Blocked, "film_release_not_confirmed"):
            evidence(plan, {}, resolver=lambda *_: identity)

    def test_release_date_requires_day_precision_and_valid_calendar(self):
        def claim(value, precision, rank="normal"):
            return {"rank": rank, "mainsnak": {"datavalue": {"value": {"time": value, "precision": precision}}}}
        entity = {"claims": {"P577": [claim("+1998-06-05T00:00:00Z", 11),
            claim("+1998-00-00T00:00:00Z", 9), claim("+1998-02-30T00:00:00Z", 11),
            claim("+1998-06-04T00:00:00Z", 11, "deprecated")]}}
        self.assertEqual(release_dates(entity), ["1998-06-05"])

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
