"""Shared server config, local-only hosts, and unique ticket ids."""
import os
import socket
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from context_lab.agent_api import context
from context_lab.egress import EgressClient
from context_lab.knowledge import allocate_ticket
from context_lab.provider import ModelEndpoint, assert_local_host
from context_lab.store import Store


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _RedirectTargetState:
    hits = []


class _RedirectHTTPHandler(BaseHTTPRequestHandler):
    redirect_to = ""
    ok_body = b'{"ok": true}'

    def log_message(self, format, *args):
        return

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            location = self.server.redirect_to
            if location == "same":
                port = self.server.server_address[1]
                location = f"http://127.0.0.1:{port}/v1/target"
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return
        if self.path == "/v1/target":
            _RedirectTargetState.hits.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.ok_body)
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == "/v1/target":
            _RedirectTargetState.hits.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.ok_body)
            return
        self.send_response(404)
        self.end_headers()


class _RedirectHTTPServer(HTTPServer):
    def __init__(self, address, redirect_to):
        self.redirect_to = redirect_to
        super().__init__(address, _RedirectHTTPHandler)


def _serve(server):
    server.serve_forever(poll_interval=0.01)


def _start_server(redirect_to):
    server = _RedirectHTTPServer(("127.0.0.1", 0), redirect_to)
    thread = threading.Thread(target=_serve, args=(server,), daemon=True)
    thread.start()
    return server, thread


class ServerConfigTests(unittest.TestCase):
    def test_allocate_ticket_unique_within_second(self):
        ids = {allocate_ticket() for _ in range(20)}
        self.assertEqual(len(ids), 20)
        for ticket in ids:
            self.assertRegex(ticket, r"^work-\d{8}-\d{6}-[0-9a-f]{4}$")

    def test_local_only_rejects_public_host(self):
        with self.assertRaises(ValueError):
            assert_local_host("https://api.openai.com/v1")
        with self.assertRaises(ValueError):
            ModelEndpoint(base_url="https://example.com/v1", model="x", local_only=True)

    def test_local_only_accepts_loopback(self):
        endpoint = ModelEndpoint(base_url="http://127.0.0.1:12345/v1", model="x", local_only=True)
        self.assertTrue(endpoint.base.startswith("http://127.0.0.1"))
        self.assertTrue(endpoint.local_only)

    def test_local_only_opener_disables_env_proxies(self):
        endpoint = ModelEndpoint(base_url="http://127.0.0.1:12345/v1", model="x", local_only=True)
        opener = endpoint._opener()
        self.assertFalse(any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers))
        self.assertTrue(any(type(h).__name__ == "LoopbackRedirectHandler" for h in opener.handlers))

    def test_remote_http_rejected_when_local_only_off(self):
        with self.assertRaises(ValueError):
            EgressClient("http://api.example.com/v1", local_only=False)
        with self.assertRaises(ValueError):
            ModelEndpoint(base_url="http://api.example.com/v1", model="x", local_only=False)

    def test_remote_https_allowed_when_local_only_off(self):
        client = EgressClient("https://api.example.com/v1", api_key="secret", local_only=False)
        self.assertFalse(client.local_only)
        self.assertTrue(any(type(h).__name__ == "OriginPinnedRedirectHandler" for h in client._build_opener().handlers))

    def test_cross_host_redirect_refused_without_leaking_authorization(self):
        target_port = _free_port()
        origin_server, _ = _start_server(f"http://127.0.0.1:{target_port}/v1/target")
        origin_port = origin_server.server_address[1]
        _RedirectTargetState.hits.clear()
        client = EgressClient(f"http://127.0.0.1:{origin_port}/v1", api_key="secret-key", local_only=False)
        with self.assertRaises(ValueError):
            client.post_json("/chat/completions", {"model": "m"})
        self.assertEqual(_RedirectTargetState.hits, [])
        origin_server.shutdown()
        origin_server.server_close()

    def test_same_origin_redirect_still_followed_when_local_only_off(self):
        _RedirectTargetState.hits.clear()
        server, _ = _start_server("same")
        port = server.server_address[1]
        client = EgressClient(f"http://127.0.0.1:{port}/v1", api_key="secret-key", local_only=False)
        result = client.post_json("/chat/completions", {"model": "m"})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(_RedirectTargetState.hits), 1)
        self.assertIn("Authorization", _RedirectTargetState.hits[0])
        server.shutdown()
        server.server_close()

    def test_local_only_still_rejects_off_loopback_redirect(self):
        origin_server, _ = _start_server("https://example.com/v1/target")
        origin_port = origin_server.server_address[1]
        _RedirectTargetState.hits.clear()
        endpoint = ModelEndpoint(base_url=f"http://127.0.0.1:{origin_port}/v1", model="x", local_only=True)
        with patch.dict(os.environ, {"CONTEXT_LAB_API_KEY": "secret-key"}, clear=False):
            with self.assertRaises(ValueError):
                endpoint.request("/chat/completions", {"model": "x"})
        self.assertEqual(_RedirectTargetState.hits, [])
        origin_server.shutdown()
        origin_server.server_close()

    @patch.dict(os.environ, {
        "CONTEXT_LAB_BASE_URL": "http://127.0.0.1:9/v1",
        "CONTEXT_LAB_MODEL": "m",
        "CONTEXT_LAB_EMBEDDING_MODEL": "",
        "CONTEXT_LAB_LOCAL_ONLY": "1",
    }, clear=False)
    def test_mcp_context_wires_planner_from_env(self):
        temp = tempfile.TemporaryDirectory()
        store = Store(str(Path(temp.name) / "memory.sqlite3"))
        try:
            src = store.add_source({"project": "app", "ticket": "", "title": "t", "body": "Brand colors are blue"})
            store.put_memories([{
                "id": "C-1", "project": "app", "ticket": "", "kind": "constraint", "status": "confirmed",
                "title": "Brand", "claim": "Brand colors are blue", "source_ids": [src["id"]],
            }])
            with patch("context_lab.provider.ModelEndpoint.plan", return_value={"actions": [], "needs": []}):
                view = context(store, {"project": "app", "query": "Change button color"}, detail="agent")
            self.assertIn("run_id", view)
        finally:
            store.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
