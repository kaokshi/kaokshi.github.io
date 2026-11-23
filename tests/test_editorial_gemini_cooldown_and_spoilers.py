"""Offline: a 503 never silently spends another Gemini request; cinema remains spoiler-safe."""
from datetime import datetime, timedelta, timezone
import unittest

from src.editorial_memory import gemini_cooldown_seconds
from src.editorial_cinema import spoiler_risk


class GeminiCooldownTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 11, 23, 13, 20, tzinfo=timezone.utc)

    def failed(self, error, before=0, after=0):
        return {"provider": "gemini", "status": "failed", "error": error,
                "completed_at": (self.at - timedelta(seconds=before)).isoformat(),
                "retry_after_seconds": after}

    def test_503_is_deferred_for_four_minutes(self):
        self.assertEqual(gemini_cooldown_seconds(
            [self.failed("http_503_transient", before=30)], self.at), 210)
        self.assertEqual(gemini_cooldown_seconds(
            [self.failed("http_503_transient", before=250)], self.at), 0)

    def test_429_defers_and_honors_retry_after(self):
        self.assertEqual(gemini_cooldown_seconds(
            [self.failed("http_429_transient", before=40, after=320)], self.at), 280)

    def test_timeout_defers_unknown_outcome(self):
        self.assertEqual(gemini_cooldown_seconds(
            [self.failed("connection_failed", before=50)], self.at), 70)

    def test_authentication_does_not_trigger_transient_retry_policy(self):
        self.assertEqual(gemini_cooldown_seconds(
            [self.failed("http_401_authorization")], self.at), 0)

    def test_other_provider_does_not_pause_gemini(self):
        x = self.failed("http_503_transient")
        x["provider"] = "groq"
        self.assertEqual(gemini_cooldown_seconds([x], self.at), 0)


class CinemaSpoilerTests(unittest.TestCase):
    def test_final_rescue_is_not_eligible_for_spoiler_free_visuals(self):
        self.assertTrue(spoiler_risk({
            "subject": "Final scene showing the rescued girl emerging from the woods",
            "brief": "Prisoners final reveal"}))

    def test_generic_detective_scene_stays_eligible(self):
        self.assertFalse(spoiler_risk({
            "subject": "Detective in a dimly lit office",
            "brief": "A close-up of a rain-covered window and desk",
            "query": "Prisoners 2013 detective still"}))


if __name__ == "__main__":
    unittest.main()
