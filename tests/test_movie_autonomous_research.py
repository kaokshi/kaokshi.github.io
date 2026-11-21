"""Offline tests of autonomous film identification and grounded source retrieval."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from movie_autonomous_research import movie_identity, movie_sources
from editorial_writer import Pending
from editorial_two_channel_pilot import run_one


def claim(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def premiere(year):
    return {"mainsnak": {"datavalue": {"value": {"time": f"+{year}-01-01T00:00:00Z"}}}}


MOVIE = {
    "labels": {"ru": {"value": "Тестовый фильм"}},
    "aliases": {},
    "claims": {
        "P31": [claim("Q11424")],
        "P577": [premiere(2024)],
        "P57": [claim("Q201")],
        "P161": [claim("Q202")],
    },
    "sitelinks": {"ruwiki": {"title": "Тестовый фильм (фильм, 2024)"}},
}
PLOT = (
    "== Сюжет ==\n" +
    "Герой прибывает в незнакомый город и сталкивается с загадочным письмом. " * 6 +
    "\n== В ролях ==\n"
)


def mocked_api(endpoint, params):
    if params["action"] == "wbsearchentities":
        return {"search": [{"id": "Q100"}, {"id": "Q999"}]}
    if params["action"] == "wbgetentities" and "claims" in params["props"]:
        return {"entities": {
            "Q100": MOVIE,
            "Q999": {"labels": {"ru": {"value": "Тестовый фильм"}},
                     "claims": {"P31": [claim("Q571")], "P577": [premiere(2024)]}},
        }}
    if params["action"] == "wbgetentities":
        return {"entities": {
            "Q201": {"labels": {"ru": {"value": "Режиссёр Проверенный"}}},
            "Q202": {"labels": {"ru": {"value": "Актёр Подтверждённый"}}},
        }}
    if params["action"] == "query":
        return {"query": {"pages": [{"title": "Тестовый фильм (фильм, 2024)", "extract": PLOT}]}}
    raise AssertionError("unexpected request")


class CinemaResearchTests(unittest.TestCase):
    def test_movie_name_parsed_without_owner_input(self):
        self.assertEqual(movie_identity("Обзор фильма «Тестовый фильм» (2024) — без спойлеров"),
                         ("Тестовый фильм", 2024))

    def test_retrieves_identified_film_and_actual_plot(self):
        result = movie_sources("discover", {"topic": "«Тестовый фильм» (2024) — обзор"},
                               get_json=mocked_api)
        self.assertEqual(result["film"]["qid"], "Q100")
        self.assertEqual(result["film"]["directors"], ["Режиссёр Проверенный"])
        self.assertEqual(result["film"]["cast"], ["Актёр Подтверждённый"])
        self.assertEqual(len(result["documents"]), 2)
        self.assertTrue(all(r["kind"] == "secondary" for r in result["documents"]))
        self.assertIn("загадочным письмом", result["documents"][1]["text"])
        self.assertFalse(result["plot_events_verified_against_film"])
        self.assertFalse(result["character_roles_verified"])
        self.assertFalse(result["publication_authorized"])

    def test_wrong_year_never_matches(self):
        with self.assertRaisesRegex(Pending, "ambiguous"):
            movie_sources("discover", {"topic": "«Тестовый фильм» (2025)"},
                          get_json=mocked_api)

    def test_missing_plot_blocks_author(self):
        def no_plot(endpoint, params):
            data = mocked_api(endpoint, params)
            if params["action"] == "query":
                data["query"]["pages"][0]["extract"] = "Краткая статья без описания сюжета."
            return data
        with self.assertRaisesRegex(Pending, "plot"):
            movie_sources("discover", {"topic": "«Тестовый фильм» (2024)"},
                          get_json=no_plot)

    def test_multiple_matching_films_require_disambiguation_not_guessing(self):
        def duplicate(endpoint, params):
            data = mocked_api(endpoint, params)
            if params["action"] == "wbgetentities" and "claims" in params["props"]:
                data["entities"]["Q999"] = dict(MOVIE)
            return data
        with self.assertRaisesRegex(Pending, "ambiguous"):
            movie_sources("discover", {"topic": "«Тестовый фильм» (2024)"},
                          get_json=duplicate)

    def test_missing_films_never_fabricate_sources(self):
        def empty(endpoint, params):
            return {"search": []}
        with self.assertRaisesRegex(Pending, "No film"):
            movie_sources("discover", {"topic": "«Несуществующая лента» (2024)"},
                          get_json=empty)

    def test_cinema_queue_uses_automatic_source_research(self):
        class RunnerResult:
            returncode = 2
            stdout = ""
            stderr = "fact_check_pending"
        commands = []
        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return RunnerResult()
        with patch("editorial_two_channel_pilot.dossier_summary",
                   return_value={"article_ready_for_owner": False}):
            run_one(Path("/tmp/private-zen-case"), "cinema", "«Тестовый фильм» (2024)",
                    runner=fake_run)
        self.assertIn("--movie-research", commands[0])
        self.assertNotIn("--europe-pmc", commands[0])


if __name__ == "__main__":
    unittest.main()
