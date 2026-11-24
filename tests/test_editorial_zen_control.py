"""Offline contract tests: remote pause and invalid observations prevent model dispatch."""
import unittest
from src.editorial_zen_control import validate_control, control_guard
from src.editorial_zen_core import CoreBlocked
from src.editorial_zen_executor import execute
from tests.test_editorial_zen_executor import Store
from src.editorial_zen_cloud import CloudCore


class ControlTest(unittest.TestCase):
    def test_control_observation_never_implicitly_authorizes_execution(self):
        now = 1795536000000
        row = {"schema": "zen-core/control-v1", "jobId": "fixture", "observedAt": now,
               "version": 2, "mode": "autonomous", "jobMode": "autonomous", "stopped": False,
               "controlClear": True, "reason": None, "executionEnabled": False,
               "publicationAuthorized": False, "rssEnabled": False}
        self.assertEqual(validate_control(row, "fixture", "autonomous", now=now, require_execution=False), 2)
        with self.assertRaisesRegex(CoreBlocked, "execution_disabled"):
            validate_control(row, "fixture", "autonomous", now=now)
        for change in ({"stopped": True}, {"controlClear": False}, {"reason": "job_cancelled"},
                       {"observedAt": now - 31000}, {"jobMode": "manual"}, {"mode": "manual"},
                       {"rssEnabled": True}, {"publicationAuthorized": True}, {"jobId": "other"}):
            with self.assertRaises(CoreBlocked):
                validate_control({**row, **change}, "fixture", "autonomous", now=now, require_execution=False)

    def test_executor_stops_before_even_reading_catalog(self):
        cloud = CloudCore(Store())
        cloud.access(lambda c: c.submit({"id": "fixture", "mode": "autonomous", "plan": {
            "items": [{"channel": "science", "count": 1}], "day": None, "instructions": ""}}), change=True)
        def guard(*_):
            raise CoreBlocked("control_blocked")
        def unexpected(*_, **__):
            self.fail("provider contacted despite blocked control")
        with self.assertRaisesRegex(CoreBlocked, "control_blocked"):
            execute(cloud, "fixture/science/1", "fixture", guard=guard, transport=unexpected)

    def test_invalid_endpoint_is_rejected(self):
        for endpoint in ("http://host/internal/zen-core/control", "https://host/other", "https://host/internal/zen-core/control?x=1"):
            with self.assertRaises(CoreBlocked):
                control_guard(endpoint, "a" * 64)


if __name__ == "__main__":
    unittest.main()
