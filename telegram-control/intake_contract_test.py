"""Offline cross-language contract check against the actual pinned PR85 Core."""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

core_root = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(core_root))
from src.editorial_zen_core import Core, CoreBlocked

client_path = pathlib.Path(__file__).with_name("intake_client.py")
spec = importlib.util.spec_from_file_location("intake_client", client_path)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
envelope_path = pathlib.Path(sys.argv.pop(1))


class IntakeContract(unittest.TestCase):
    def test_worker_payload_to_real_core(self):
        envelope = json.loads(envelope_path.read_text())
        with tempfile.TemporaryDirectory() as temp:
            core = Core(pathlib.Path(temp) / "test.sqlite")
            try:
                job_id = envelope["intent"]["id"]
                self.assertEqual(client.accept(core, envelope, job_id), job_id)
                self.assertEqual(client.accept(core, envelope, job_id), job_id)
                snapshot = core.snapshot()
                self.assertEqual(len(snapshot["jobs"]), 1)
                self.assertEqual(len(snapshot["jobs"][0]["articles"]), 3)
                self.assertEqual(snapshot["budget"]["mainDailyRub"], "300")
                self.assertEqual(snapshot["budget"]["reserveDailyRub"], "50")
                self.assertFalse(snapshot["executionEnabled"])
                self.assertFalse(snapshot["publicationAuthorized"])
                self.assertFalse(snapshot["rssEnabled"])
                for flags in ("executionEnabled", "publicationAuthorized", "rssEnabled"):
                    with self.assertRaises(ValueError):
                        client.accept(core, {**envelope, flags: True}, job_id)
                with self.assertRaises(ValueError):
                    client.accept(core, envelope, "different")
                changed = json.loads(json.dumps(envelope))
                changed["intent"]["plan"]["items"][0]["count"] = 3
                with self.assertRaises(CoreBlocked):
                    client.accept(core, changed, job_id)
            finally:
                core.db.close()


if __name__ == "__main__":
    unittest.main()
