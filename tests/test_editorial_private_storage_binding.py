"""The encrypted owner-review configuration is independent of production activation."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from src.editorial_delivery import owner_key
from src.editorial_zen_cloud import REPO


class PrivateStorageBindingTests(unittest.TestCase):
    def test_existing_private_store_and_public_key_remain_available(self):
        storage = json.loads(Path("config/editorial_production.json").read_text())["storage"]
        self.assertEqual(storage["private_repository"], REPO)
        public_key = storage["owner_public_key"]
        self.assertRegex(public_key, r"^[0-9a-f]{64}$")
        with patch.dict("os.environ", {"EDITORIAL_REVIEW_PUBLIC_KEY": public_key}):
            self.assertEqual(owner_key(), public_key)
        core = json.loads(Path("config/zen_core.json").read_text())
        self.assertIs(core["execution_enabled"], False)
