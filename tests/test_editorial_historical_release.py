from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from src.editorial_historical_release import recover_or_call, materialize_verified_selection
from src.editorial_providers import Blocked


class HistoricalReleaseTests(unittest.TestCase):
    def test_json_response_and_receipt_do_not_overwrite_each_other(self):
        with TemporaryDirectory() as work:
            response = b'{"01": "test data"}'
            path = Path(work) / "result.json"
            callback = Mock(return_value=(response, {"model": "fixture"}))
            memory = SimpleNamespace(data={"calls": []}, call=lambda *args: args[-1]())
            self.assertEqual(recover_or_call(memory, "op", "groq", callback, path), response)
            self.assertEqual(recover_or_call(memory, "op", "groq", callback, path), response)
            callback.assert_called_once()
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(Blocked, "saved_response_sha_mismatch"):
                recover_or_call(memory, "op", "groq", callback, path)

    def test_reserved_request_is_not_reissued(self):
        with TemporaryDirectory() as work:
            provider = Mock()
            memory = SimpleNamespace(data={"calls": [{"task": "op", "status": "reserved"}]})
            with self.assertRaisesRegex(Blocked, "no_resend"):
                recover_or_call(memory, "op", "klein", provider, Path(work) / "scene.jpg")
            provider.assert_not_called()

    def test_incomplete_audit_never_materializes_an_archive(self):
        with TemporaryDirectory() as work:
            folder = Path(work)
            with self.assertRaisesRegex(Blocked, "full_audit_not_ready"):
                materialize_verified_selection(folder, folder / "output")
            self.assertFalse((folder / "output").exists())
