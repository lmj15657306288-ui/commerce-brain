import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from laya_client import LayaClient, LayaClientError


class _Handler(BaseHTTPRequestHandler):
    response_mode = "ok"
    seen_authorization = None

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if urlparse(self.path).path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "loaded": ["typed-decisions"],
            "device": "mps",
        }).encode("utf-8"))

    def do_POST(self):
        _Handler.seen_authorization = self.headers.get("Authorization")
        if _Handler.response_mode == "timeout":
            time.sleep(0.35)
        if _Handler.response_mode == "unauthorized":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"detail":"secret-value-must-not-leak"}')
            return
        if _Handler.response_mode == "invalid-json":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{")
            return
        if _Handler.response_mode == "list":
            body = b"[]"
        else:
            body = json.dumps({
                "model": "laya-rl-agent",
                "answers": {"next_action": {"type": "choice", "choice": "OBSERVE"}},
                "routing": {},
                "usage": {},
            }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class LayaClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        _Handler.response_mode = "ok"
        _Handler.seen_authorization = None

    def client(self, **kwargs):
        return LayaClient(base_url=self.base_url, **kwargs)

    def test_only_loopback_http_is_allowed(self):
        with self.assertRaisesRegex(LayaClientError, "loopback"):
            LayaClient(base_url="https://example.com")
        with self.assertRaisesRegex(LayaClientError, "loopback"):
            LayaClient(base_url="http://192.0.2.1:8765")

    def test_health_is_parsed_and_latency_is_recorded(self):
        health = self.client(api_key="local-secret").health()
        self.assertEqual("ok", health.status)
        self.assertEqual(("typed-decisions",), health.loaded)
        self.assertEqual("mps", health.device)
        self.assertGreaterEqual(health.latency_ms, 0)

    def test_systemone_sends_bearer_and_parses_object(self):
        value = self.client(api_key="local-secret").systemone(
            state={"roi": 1.2},
            questions={"next_action": {"type": "choice"}},
        )
        self.assertEqual("OBSERVE", value["answers"]["next_action"]["choice"])
        self.assertEqual("Bearer local-secret", _Handler.seen_authorization)

    def test_http_failure_does_not_expose_response_body(self):
        _Handler.response_mode = "unauthorized"
        with self.assertRaises(LayaClientError) as context:
            self.client(api_key="local-secret").systemone(state={}, questions={})
        self.assertEqual("HTTP_401", context.exception.code)
        self.assertNotIn("secret-value", str(context.exception))
        self.assertNotIn("local-secret", str(context.exception))

    def test_invalid_json_and_schema_fail_closed(self):
        _Handler.response_mode = "invalid-json"
        with self.assertRaisesRegex(LayaClientError, "invalid JSON"):
            self.client().systemone(state={}, questions={})
        _Handler.response_mode = "list"
        with self.assertRaisesRegex(LayaClientError, "JSON object"):
            self.client().systemone(state={}, questions={})

    def test_timeout_is_normalized_to_safe_error(self):
        _Handler.response_mode = "timeout"
        with self.assertRaisesRegex(LayaClientError, "unavailable"):
            self.client(timeout_seconds=0.1).systemone(state={}, questions={})

    def test_request_json_rejects_non_finite_values(self):
        with self.assertRaisesRegex(LayaClientError, "valid JSON"):
            self.client().systemone(state={"roi": float("inf")}, questions={})


if __name__ == "__main__":
    unittest.main()
