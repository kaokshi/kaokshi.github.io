"""Offline fixtures for privately registering and retrieving archive footage."""
import json
import unittest
from unittest.mock import patch

from src.editorial_cinema import fetch_still
from src.editorial_cinema_catalogue import refresh_catalogue
from src.editorial_http import Blocked
from tests.test_editorial_zen_executor import Store


class CatalogueTests(unittest.TestCase):
    def test_catalogue_saves_hashed_assets_and_reuses_existing_entry(self):
        store = Store()
        qid = "Q555"
        film = {"qid": qid, "original_title": "Fixture", "title": "Fixture", "year": 1910}
        origin = {"asset_url": "https://upload.wikimedia.org/a.webm",
                  "source_page": "https://commons.wikimedia.org/wiki/File:A.webm", "license": "Public domain"}
        candidates = [(qid, "A.webm", film)]
        frames = [(100 + n, hex(n)[2:], bytes([n])) for n in range(12)]
        with patch("src.editorial_cinema_catalogue.request", return_value=b"video") as download:
            for count in (1, 0):
                result = refresh_catalogue(store, discover=lambda: candidates, lookup=lambda _: origin,
                                           extract=lambda _: frames)
                self.assertEqual(result, {"added": count, "available": 1})
            download.assert_called_once()
        raw, _ = store.read("sources/cinema.json")
        still = json.loads(raw)[qid]["stills"][0]
        self.assertEqual(fetch_still(still, film, private_read=lambda path: store.read(path)[0])[0], b"\x00")
        with self.assertRaisesRegex(Blocked, "film_still_private_path_invalid"):
            fetch_still({**still, "private_path": "ledger.sqlite"}, film, private_read=lambda _: b"\x00")
        with self.assertRaisesRegex(Blocked, "film_still_sha_mismatch"):
            fetch_still(still, film, private_read=lambda _: b"different")
