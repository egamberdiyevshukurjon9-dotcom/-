# -*- coding: utf-8 -*-
import json
import unittest
import urllib.error
import urllib.request

import _setup  # noqa: F401
from commands import Command
from remote import NUMERIC_COMMANDS, SIMPLE_COMMANDS, PAGE, RemoteServer, SessionState, new_token


def call(url, method="GET", body=None, headers=None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8"), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8"), dict(error.headers)


class RemoteServerTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.state = SessionState()
        self.server = RemoteServer(self.sent.append, self.state, token="sirli123", host="127.0.0.1", port=0)
        port = self.server.start()
        self.base = "http://127.0.0.1:%d" % port
        self.addCleanup(self.server.stop)

    def test_token_is_required_everywhere(self):
        for path in ("/", "/api/status", "/api/command/next"):
            status, body, _ = call(self.base + path)
            self.assertEqual(status, 401, path)
        status, _, _ = call(self.base + "/api/command/next?t=notogri", method="POST")
        self.assertEqual(status, 401)
        self.assertEqual(self.sent, [])

    def test_page_is_served_with_token(self):
        status, body, headers = call(self.base + "/?t=sirli123")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Кейинги", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body, PAGE)

    def test_simple_command_get_and_post(self):
        for method in ("GET", "POST"):
            status, body, _ = call(self.base + "/api/command/next?t=sirli123", method=method,
                                   body=b"" if method == "POST" else None)
            self.assertEqual((status, json.loads(body)["ok"]), (200, True))
        self.assertEqual(self.sent, [Command("next", manual=True)] * 2)
        self.assertTrue(all(c.manual for c in self.sent))

    def test_numeric_commands(self):
        call(self.base + "/api/command/goto?t=sirli123&n=7", method="POST")
        call(self.base + "/api/command/jump?t=sirli123&n=-3", method="POST")
        call(self.base + "/api/command/auto_on?t=sirli123", method="POST")      # рақамсиз — стандарт
        self.assertEqual([(c.intent, c.arg) for c in self.sent],
                         [("goto", 7), ("jump", -3), ("auto_on", None)])

    def test_json_body_and_header_token(self):
        status, body, _ = call(self.base + "/api/command", method="POST",
                               body=json.dumps({"command": "goto", "n": 5}).encode(),
                               headers={"X-Token": "sirli123", "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual((self.sent[0].intent, self.sent[0].arg), ("goto", 5))
        status, _, _ = call(self.base + "/api/command/prev", method="POST",
                            headers={"Authorization": "Bearer sirli123"})
        self.assertEqual(status, 200)

    def test_bad_requests_are_rejected_without_sending(self):
        cases = ["/api/command/format_disk?t=sirli123", "/api/command/goto?t=sirli123",
                 "/api/command/goto?t=sirli123&n=abc", "/api/command/goto?t=sirli123&n=0",
                 "/api/command/goto?t=sirli123&n=100000", "/api/command/jump?t=sirli123&n=5000",
                 "/api/command/__import__?t=sirli123", "/api/command/?t=sirli123"]
        for path in cases:
            status, body, _ = call(self.base + path, method="POST", body=b"")
            self.assertEqual(status, 400, path)
        status, _, _ = call(self.base + "/api/command", method="POST", body=b"{buzuq",
                            headers={"X-Token": "sirli123"})
        self.assertEqual(status, 400)
        status, _, _ = call(self.base + "/yoq?t=sirli123")
        self.assertEqual(status, 404)
        self.assertEqual(self.sent, [])

    def test_status_reflects_the_session_state(self):
        self.state.update(slide=3, total=12, elapsed=75, listening=False, auto=15, bookmark=5)
        self.state.add_message("Кейинги слайд  [3/12]")
        status, body, _ = call(self.base + "/api/status?t=sirli123")
        data = json.loads(body)
        self.assertEqual((status, data["slide"], data["total"], data["bookmark"]), (200, 3, 12, 5))
        self.assertFalse(data["listening"])
        self.assertEqual(data["messages"], ["Кейинги слайд  [3/12]"])

    def test_rate_limit(self):
        self.server.rate = 5
        codes = [call(self.base + "/api/status?t=sirli123")[0] for _ in range(12)]
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 200)

    def test_every_whitelisted_command_is_executable_by_the_router(self):
        from router import CommandRouter
        for name in SIMPLE_COMMANDS | set(NUMERIC_COMMANDS):
            self.assertTrue(hasattr(CommandRouter, "_do_" + name), name)

    def test_tokens_are_random_and_urls_contain_them(self):
        self.assertNotEqual(new_token(), new_token())
        self.assertEqual(len(new_token()), 12)
        self.assertIn("?t=sirli123", self.server.urls()[0])


if __name__ == "__main__":
    unittest.main()
