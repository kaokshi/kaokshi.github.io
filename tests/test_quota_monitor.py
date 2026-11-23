"""Offline regression tests for least-privileged provider quota monitoring."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "src" / "editorial_quota_monitor.py"
spec = importlib.util.spec_from_file_location("editorial_quota_monitor_test_module", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QuotaMonitorSafetyTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_groq_key_does_not_probe(self):
        self.assertEqual(module.groq()["status"], "missing_key")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_gemini_key_does_not_probe(self):
        self.assertEqual(module.gemini()["status"], "missing_key")

    @patch.dict(os.environ, {}, clear=True)
    def test_cloudflare_requires_separate_analytics_token(self):
        self.assertEqual(module.cloudflare()["status"], "read_only_analytics_token_not_configured")

    @patch.dict(os.environ, {}, clear=True)
    def test_google_monitoring_requires_oidc(self):
        result = module.gcp_gemini_metrics()
        self.assertEqual(result["status"], "not_configured")

    @patch.dict(os.environ, {}, clear=True)
    def test_private_archive_requires_private_secret(self):
        result = module.private_store("quota-monitor/latest.json", b"{}")
        self.assertEqual(result["status"], "missing_private_write_token")


if __name__ == "__main__":
    unittest.main()
