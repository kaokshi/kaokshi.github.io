import unittest
from unittest.mock import Mock
from src.editorial_routerai_mcp import read_generation_receipt, read_all_statement


class MCPReadOnlyTests(unittest.TestCase):
    def test_generation_lookup_uses_only_read_tool(self):
        call = Mock(return_value={"structuredContent":{"id":"rai-fixture","cost":0.25}})
        result = read_generation_receipt({"schema":"zen-core/provider-generation-v1",
                                          "generationId":"rai-fixture"}, call=call)
        self.assertEqual(result["id"], "rai-fixture")
        call.assert_called_once_with("tools/call", {"name":"get_generation",
                                                    "arguments":{"id":"rai-fixture"}}, 3)

    def test_invalid_receipt_does_not_call_provider(self):
        call = Mock()
        for receipt in (None, {}, {"schema":"zen-core/provider-generation-v1",
                                    "generationId":"bad\nheader"}):
            with self.assertRaises(ValueError):
                read_generation_receipt(receipt, call=call)
        call.assert_not_called()

    def test_complete_statement_pagination(self):
        call = Mock(side_effect=[
            {"structuredContent":{"transactions":[{"id":"one"}],"next_cursor":"next"}},
            {"structuredContent":{"transactions":[{"id":"two"}],"next_cursor":None}},
        ])
        self.assertEqual(len(read_all_statement({"limit":100}, call=call)), 2)
        self.assertEqual(call.call_args.args[1]["arguments"]["cursor"], "next")

    def test_repeated_transaction_fails_closed(self):
        call = Mock(side_effect=[
            {"structuredContent":{"transactions":[{"id":"one"}],"next_cursor":"next"}},
            {"structuredContent":{"transactions":[{"id":"one"}],"next_cursor":None}},
        ])
        with self.assertRaises(SystemExit):
            read_all_statement({"limit":100}, call=call)
