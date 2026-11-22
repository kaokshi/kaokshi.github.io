"""Cinema evidence stays behind release, identity and image-rights gates."""
from datetime import date, timedelta
import unittest

from src.editorial_cinema import evidence
from src.editorial_providers import Blocked
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
