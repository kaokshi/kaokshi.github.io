"""No network access or credentials required to test the quota reporting logic."""
import datetime as dt
import unittest
from unittest.mock import patch

from src.cloudflare_quota import QuotaCheckError, check_quota, extract_neurons


class CloudflareQuotaTests(unittest.TestCase):
    def test_aggregated_neurons(self):
        used, calls = extract_neurons({
            "data": {"viewer": {"accounts": [{
                "aiInferenceAdaptiveGroups": [
                    {"count": 3, "sum": {"totalNeurons": 112.25}},
                    {"count": 2, "sum": {"totalNeurons": 40.0}},
                ]
            }]}}
        })
        self.assertEqual((used, calls), (152.25, 5))

    @patch("src.cloudflare_quota.api_json")
    def test_report_uses_utc_day_and_capped_remaining(self, api):
        api.side_effect = [
            {"success": True, "result": {"status": "active"}},
            {"data": {"viewer": {"accounts": [{
                "aiInferenceAdaptiveGroups": [{"count": 9, "sum": {"totalNeurons": 10005.75}}]
            }]}}},
        ]
        report = check_quota(
            "fake-test-token", "fake-test-account",
            dt.datetime(2026, 11, 23, 1, 20, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(report["estimated_used_neurons"], 10005.75)
        self.assertEqual(report["estimated_free_remaining_neurons"], 0)
        self.assertEqual(report["period_utc"], "2026-11-23")
        self.assertFalse(report["billing_authoritative"])
        self.assertEqual(api.call_count, 2)
        self.assertEqual(
            api.call_args.args[2]["variables"]["start"], "2026-11-23T00:00:00Z"
        )

    def test_missing_token_or_account_fails_closed(self):
        for token, account in [("", "id"), ("token", "")]:
            with self.subTest(token=bool(token), account=bool(account)):
                with self.assertRaises(QuotaCheckError):
                    check_quota(token, account)

    def test_graphql_error_is_not_zero_usage(self):
        with self.assertRaises(QuotaCheckError):
            extract_neurons({"errors": [{"message": "denied"}]})

    def test_invalid_analytics_is_not_zero_usage(self):
        for bad in [
            {"data": {"viewer": {"accounts": []}}},
            {"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": [
                {"count": 1, "sum": {"totalNeurons": -1}}
            ]}]}}},
            {"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": [
                {"count": 1, "sum": {"totalNeurons": "nan"}}
            ]}]}}},
        ]:
            with self.assertRaises(QuotaCheckError):
                extract_neurons(bad)


if __name__ == "__main__":
    unittest.main()
