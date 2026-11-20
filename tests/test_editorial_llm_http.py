"""Real loopback HTTP integration tests; no weights, external API or mock send()."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_local_llm import complete, ProviderFailure


class LocalHttpTransportTests(unittest.TestCase):
    def serve(self, backend, responses):
        requests = []
        responses = iter(responses)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                requests.append((self.path, payload))
                status, data = next(responses)
                raw = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = {
            "ZEN_LLM_BACKEND": backend,
            "ZEN_LLM_MODEL": "test-only",
            "ZEN_LLM_URL": f"http://127.0.0.1:{server.server_port}",
            "ZEN_LLM_TIMEOUT": "30",
        }
        return env, requests

    @staticmethod
    def article():
        return {"title": "Проверка", "paragraphs": ["Тестовый абзац."], "cta": "Подпишитесь на канал."}

    def test_llama_cpp_real_http_and_recovery_from_503(self):
        good = {"choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps(self.article(), ensure_ascii=False)}}]}
        env, calls = self.serve("llama.cpp", [(503, {"error": "busy"}), (200, good)])
        output = complete({"task": "write", "instruction": "Верни структуру статьи"}, env=env)
        self.assertEqual(output, self.article())
        self.assertEqual(len(calls), 2)
        for path, body in calls:
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(body["response_format"]["type"], "json_schema")
            self.assertEqual(body["model"], "test-only")

    def test_ollama_real_http_structured_json(self):
        good = {"done_reason": "stop",
                "message": {"content": json.dumps(self.article(), ensure_ascii=False)}}
        env, calls = self.serve("ollama", [(200, good)])
        self.assertEqual(
            complete({"task": "write", "instruction": "Верни структуру статьи"}, env=env),
            self.article(),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/api/chat")
        self.assertIn("format", calls[0][1])
        self.assertFalse(calls[0][1]["stream"])

    def test_rejects_bad_json_without_claiming_success(self):
        bad = {"choices": [{"finish_reason": "stop",
                            "message": {"content": "not JSON"}}]}
        env, calls = self.serve("llama.cpp", [(200, bad), (200, bad)])
        with self.assertRaises(ProviderFailure):
            complete({"task": "write", "instruction": "Тест"}, env=env)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
