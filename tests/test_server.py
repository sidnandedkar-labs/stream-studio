from argparse import Namespace
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import make_server, validate_messages


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(Namespace(port=0, demo=True, model="", max_tokens=512, timeout=60, no_usage=False))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_config_has_no_credentials(self):
        with urllib.request.urlopen(self.base + "/api/config") as response:
            value = json.load(response)
            self.assertEqual(set(value), {"demo", "model", "max_tokens"})
            self.assertTrue(value["demo"])

    def test_demo_stream_ends_with_measurements(self):
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps({"messages": [{"role": "user", "content": "Hi"}]}).encode(), headers={"Content-Type": "application/json", "Origin": self.base})
        with urllib.request.urlopen(req) as response:
            events = [json.loads(line) for line in response]
        self.assertEqual(events[-1]["type"], "metrics")
        self.assertTrue(events[-1]["synthetic"])
        self.assertIsNone(events[-1]["end_to_end_tps"])
        self.assertIn("synthetic", "".join(e.get("text", "") for e in events))

    def test_cross_origin_requests_rejected(self):
        req = urllib.request.Request(self.base + "/api/chat", data=b"{}", headers={"Content-Type": "application/json", "Origin": "https://untrusted.example"})
        with self.assertRaises(urllib.error.HTTPError) as raised: urllib.request.urlopen(req)
        self.assertEqual(raised.exception.code, 403)

    def test_unrecognized_host_rejected(self):
        req = urllib.request.Request(self.base + "/api/config", headers={"Host": "untrusted.example"})
        with self.assertRaises(urllib.error.HTTPError) as raised: urllib.request.urlopen(req)
        self.assertEqual(raised.exception.code, 403)

    def test_no_arbitrary_file_serving(self):
        with self.assertRaises(urllib.error.HTTPError) as raised: urllib.request.urlopen(self.base + "/server.py")
        self.assertEqual(raised.exception.code, 404)

    def test_bad_input_rejected(self):
        invalid = [None, [], [{"role": "system", "content": "x"}], [{"role": "user", "content": "x"*24001}], [{"role": "assistant", "content": "x"}]]
        for value in invalid:
            with self.assertRaises(ValueError): validate_messages(value)

    def test_ui_has_local_assets_and_security_headers(self):
        with urllib.request.urlopen(self.base) as response:
            body = response.read().decode()
            self.assertIn('src="/app.js"', body)
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])


if __name__ == "__main__": unittest.main()
