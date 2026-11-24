import importlib.util
from pathlib import Path
import sys
import unittest

from src.editorial_zen_cloud import CloudCore, STATE
from src.editorial_zen_core import CoreBlocked
from tests.test_editorial_zen_executor import Store

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "telegram-control"))
import intake_runner as runner


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        CloudCore(self.store).access(lambda c: c.snapshot(), change=True)

    def test_cursor_persists_across_short_lived_processes_and_wraps(self):
        key = "production:index:000000000000001:job:1"
        def first(core, endpoint, secret, *, max_pages, cursor):
            self.assertIsNone(cursor)
            return {"delivered": 0, "skipped": 0, "nextCursor": key}
        self.assertTrue(runner.cycle(self.store, "fixture", "fixture", consume=first)["morePages"])
        self.assertEqual(runner.validate_checkpoint(self.store.read(runner.CHECKPOINT)[0]), key)
        def next_cycle(core, endpoint, secret, *, max_pages, cursor):
            self.assertEqual(cursor, key)
            return {"delivered": 0, "skipped": 0, "nextCursor": None}
        self.assertFalse(runner.cycle(self.store, "fixture", "fixture", consume=next_cycle)["morePages"])
        self.assertIsNone(runner.validate_checkpoint(self.store.read(runner.CHECKPOINT)[0]))

    def test_no_checkpoint_write_on_empty_queue(self):
        raw = self.store.read(STATE)
        runner.cycle(self.store, "fixture", "fixture", consume=lambda *a, **k: {"delivered": 0, "skipped": 0, "nextCursor": None})
        self.assertEqual(self.store.read(STATE), raw)
        self.assertIsNone(self.store.read(runner.CHECKPOINT)[0])

    def test_missing_ledger_and_invalid_cursor_fail_closed(self):
        def unexpected(*a, **k):
            self.fail("queue queried before validation")
        with self.assertRaisesRegex(CoreBlocked, "existing_cloud_ledger_required"):
            runner.cycle(Store(), "fixture", "fixture", consume=unexpected)
        self.store.data[runner.CHECKPOINT] = b'{"schema":"bad","cursor":null}'
        with self.assertRaisesRegex(CoreBlocked, "checkpoint_invalid"):
            runner.cycle(self.store, "fixture", "fixture", consume=unexpected)

    def test_transport_failure_does_not_advance_cursor(self):
        key = "production:index:000000000000001:job:1"
        runner.cycle(self.store, "fixture", "fixture", consume=lambda *a, **k: {"delivered": 0, "skipped": 0, "nextCursor": key})
        before = self.store.read(runner.CHECKPOINT)
        def unavailable(*a, **k):
            raise OSError("synthetic outage")
        with self.assertRaises(OSError):
            runner.cycle(self.store, "fixture", "fixture", consume=unavailable)
        self.assertEqual(self.store.read(runner.CHECKPOINT), before)
