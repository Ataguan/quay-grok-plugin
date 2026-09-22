#!/usr/bin/env python3
"""Tests for the Quay MCP server. Standard library only."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402


PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]


def public_resolver(host, port, socktype):  # noqa: ANN001
    return PUBLIC


def private_resolver(host, port, socktype):  # noqa: ANN001
    return PRIVATE


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self, _n: int) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, status: int = 201, body: bytes = b""):
        self.status = status
        self.body = body or b'{"ok":true,"item":{"id":"abc","type":"places","title":"Lisbon"}}'
        self.requests = []

    def open(self, request, timeout=None):  # noqa: ANN001
        self.requests.append(request)
        if self.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, self.status, "bad", hdrs=None, fp=_BytesIO(self.body)
            )
        return FakeResponse(self.status, self.body)


class _BytesIO:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, _n: int) -> bytes:
        return self._data

    def close(self) -> None:
        return None


class UrlTests(unittest.TestCase):
    def test_accepts_project_origin_and_full_ingest_path(self):
        origin = server.ingest_url("https://demo.supabase.co", public_resolver)
        full = server.ingest_url("https://demo.supabase.co/functions/v1/ingest", public_resolver)
        self.assertEqual(origin, "https://demo.supabase.co/functions/v1/ingest")
        self.assertEqual(full, origin)

    def test_rejects_unsafe_urls(self):
        cases = [
            "http://demo.supabase.co",
            "https://user:pass@demo.supabase.co",
            "https://demo.supabase.co/functions/v1/ingest?token=secret",
            "https://demo.supabase.co/other",
            "https://localhost/functions/v1/ingest",
            "https://169.254.169.254/functions/v1/ingest",
            "https://10.0.0.5/functions/v1/ingest",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(server.QuayError):
                    server.ingest_url(raw, public_resolver)

    def test_rejects_host_that_resolves_private(self):
        with self.assertRaises(server.QuayError):
            server.ingest_url("https://internal.example", private_resolver)


class CardTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ["QUAY_SOURCE"] = 'Scout "bot"'

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_places_card_uses_sanitized_source(self):
        card = server.build_card(
            "dock_places",
            {
                "title": "Saturday",
                "source_ask": "Find a cafe",
                "places": [{"name": "Fábrica", "lat": 38.7, "lng": -9.1}],
            },
        )
        self.assertEqual(card["type"], "places")
        self.assertEqual(card["source"], "Scout bot")
        self.assertEqual(card["payload"][0]["name"], "Fábrica")
        self.assertEqual(card["source_ask"], "Find a cafe")

    def test_calendar_requires_iso_dates(self):
        with self.assertRaises(server.QuayError):
            server.build_card(
                "dock_calendar",
                {"title": "Day", "events": [{"title": "Write", "starts_at": "tomorrow", "ends_at": "later"}]},
            )

    def test_articles_keep_reading_fields(self):
        card = server.build_card(
            "dock_articles",
            {
                "title": "Porto",
                "articles": [
                    {
                        "title": "Ribeira",
                        "body": "Stay up the hill.",
                        "references": [{"title": "UNESCO", "url": "https://whc.unesco.org/en/list/755/"}],
                    }
                ],
            },
        )
        article = card["payload"][0]
        self.assertEqual(article["body"], "Stay up the hill.")
        self.assertEqual(article["references"][0]["title"], "UNESCO")
        self.assertFalse(article["read"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ["QUAY_URL"] = "https://demo.supabase.co"
        os.environ["QUAY_API_KEY"] = "quay_test_key"
        os.environ["QUAY_SOURCE"] = "Scout"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_dock_posts_bearer_to_ingest_and_hides_the_key(self):
        opener = FakeOpener()
        result = server.dock(
            "dock_note",
            {"title": "Note", "body": "Hello"},
            public_resolver,
            opener,
        )
        self.assertTrue(result["ok"])
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://demo.supabase.co/functions/v1/ingest")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer quay_test_key")
        posted = json.loads(request.data.decode())
        self.assertEqual(posted["type"], "note")
        self.assertEqual(posted["source"], "Scout")
        text = json.dumps(result)
        self.assertNotIn("quay_test_key", text)

    def test_status_reports_host_without_the_key(self):
        opener = FakeOpener(200, b'{"name":"Quay ingest","types":["places","note"]}')
        report = server.status_report(public_resolver, opener)
        self.assertEqual(report["host"], "demo.supabase.co")
        self.assertTrue(report["key_present"])
        self.assertEqual(report["contract"], "Quay ingest")
        self.assertNotIn("quay_test_key", json.dumps(report))
        self.assertEqual(opener.requests[0].get_method(), "GET")

    def test_http_error_omits_the_key(self):
        opener = FakeOpener(401, b'{"error":"unauthorized"}')
        with self.assertRaises(server.QuayError) as caught:
            server.dock("dock_note", {"title": "Note", "body": "Hello"}, public_resolver, opener)
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("quay_test_key", str(caught.exception))

    def test_missing_key_is_a_tool_error(self):
        os.environ.pop("QUAY_API_KEY")
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "quay_status"}},
            public_resolver,
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("QUAY_API_KEY", response["result"]["content"][0]["text"])


class ProtocolTests(unittest.TestCase):
    def test_stdio_roundtrip_lists_tools_and_reports_missing_config(self):
        script = Path(__file__).resolve().parent / "server.py"
        env = os.environ.copy()
        env.pop("QUAY_URL", None)
        env.pop("QUAY_API_KEY", None)
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "dock_todos", "arguments": {"title": "Pack", "items": [{"text": "Passport"}]}},
            },
        ]
        with subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        ) as proc:
            assert proc.stdin and proc.stdout and proc.stderr
            for message in messages:
                payload = json.dumps(message).encode()
                proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
            proc.stdin.close()
            raw = proc.stdout.read()
            proc.stderr.read()
            proc.wait(timeout=5)
            returncode = proc.returncode
        decoded = _decode_frames(raw)
        self.assertEqual(decoded[0]["id"], 1)
        self.assertEqual(decoded[0]["result"]["serverInfo"]["name"], "quay")
        names = [tool["name"] for tool in decoded[1]["result"]["tools"]]
        self.assertIn("dock_places", names)
        self.assertIn("dock_articles", names)
        self.assertTrue(decoded[2]["result"]["isError"])
        self.assertIn("QUAY_URL", decoded[2]["result"]["content"][0]["text"])
        self.assertEqual(returncode, 0)


def _decode_frames(raw: bytes) -> list[dict]:
    frames = []
    index = 0
    while index < len(raw):
        header_end = raw.find(b"\r\n\r\n", index)
        if header_end < 0:
            break
        header = raw[index:header_end].decode()
        length = int(header.split(":", 1)[1].strip())
        start = header_end + 4
        frames.append(json.loads(raw[start : start + length]))
        index = start + length
    return frames


if __name__ == "__main__":
    unittest.main()
