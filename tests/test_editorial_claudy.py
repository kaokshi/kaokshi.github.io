import unittest
from src.editorial_claudy import native_request, request_bound, inspect_response, preview_editorial_routes

class ClaudyExecutorTests(unittest.TestCase):
    model="anthropic/claude-sonnet-5.5"
    def payload(self):
        return native_request(self.model,[{"role":"system","content":"Правила"},{"role":"user","content":"Статья"}],"high",1000)
    def test_native_request_and_bound(self):
        payload=self.payload()
        self.assertEqual(payload["model"],"claude-sonnet-5-5")
        self.assertEqual(payload["output_config"]["effort"],"high")
        self.assertFalse(request_bound(self.model,payload)["dispatch_authorized"])
        self.assertNotIn("output_config",native_request(self.model,[{"role":"system","content":"A"},{"role":"user","content":"B"}],"off",100))
    def test_response_no_cost_receipt(self):
        data={"id":"msg1","model":"claude-sonnet-5-5","stop_reason":"end_turn","content":[{"type":"text","text":"Статья"}],"usage":{"input_tokens":100,"output_tokens":200,"cost":0.01}}
        r=inspect_response(self.model,self.payload(),data,"req1")
        self.assertTrue(r["accepted"])
        self.assertEqual(r["estimated_rub"],"0.033")
        self.assertFalse(r["settlement_verified"])
        self.assertNotIn("cost",r["evidence"]["usage"])
        data["usage"]["output_tokens"]=1001
        r=inspect_response(self.model,self.payload(),data,"req1")
        self.assertEqual(r["reason"],"claudy_usage_over_bound")
        self.assertEqual(r["evidence"]["request_id"],"req1")
    def test_rejects_multimodal_and_unconfigured_models(self):
        with self.assertRaises(ValueError):
            native_request(self.model,[{"role":"system","content":"A"},{"role":"user","content":[]}],"high",100)
        with self.assertRaises(ValueError):
            native_request("anthropic/claude-haiku-5.5",[],"high",100)
    def test_target_routes(self):
        models={"plan":"qwen/qwen3.6-plus","draft":self.model,"edit":"deepseek/deepseek-v4-pro-0813","visual_plan":"qwen/qwen3.6-plus","images":"bytedance-seed/seedream-5-0-flash"}
        result=preview_editorial_routes(models)
        self.assertEqual(result["draft"]["target_provider"],"claudy")
        self.assertEqual(result["images"]["target_provider"],"routerai")
        self.assertTrue(all(not r["dispatch_authorized"] for r in result.values()))
        with self.assertRaises(ValueError):
            preview_editorial_routes({**models,"images":self.model})

    def test_pinned_settings_preview(self):
        import hashlib
        import json
        from src.editorial_claudy import preview_pinned_routes
        from src.editorial_zen_core import MODEL_ROLES, CoreBlocked
        models={"plan":"qwen/qwen3.6-plus","draft":self.model,"edit":"deepseek/deepseek-v4-pro-0813","visual_plan":"qwen/qwen3.6-plus","images":"bytedance-seed/seedream-5-0-flash"}
        levels={r:"off" for r in MODEL_ROLES}
        levels["draft"]="high"
        sha=hashlib.sha256(json.dumps([[r,models[r],levels[r]] for r in MODEL_ROLES],separators=(",",":")).encode()).hexdigest()
        settings={"models":{"revision":7,"hash":sha,"value":models},"reasoning":levels}
        result=preview_pinned_routes(settings)
        self.assertEqual(result["revision"],7)
        self.assertEqual(result["reasoning"]["draft"],"high")
        self.assertFalse(result["execution_enabled"])
        settings["reasoning"]["draft"]="low"
        with self.assertRaises(CoreBlocked):
            preview_pinned_routes(settings)
