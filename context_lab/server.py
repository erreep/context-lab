"""A local-only inspection UI. No external assets, telemetry or API keys in HTML."""
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .engine import DATA_ROOT, PACKAGE_ROOT
from .service import dispatch, info, scopes
from .store import Store


def serve(db_path, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logging.info(format, *args)

        def send(self, code, body, mime="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            host_header = self.headers.get("Host", "")
            hostname = host_header.split(":")[0]
            if hostname not in {"127.0.0.1", "localhost"}:
                self.send(403, {"error": "Localhost requests only"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host_header:
                self.send(403, {"error": "Cross-origin requests are not allowed"})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            path = urlparse(self.path).path
            if path == "/":
                return self.send(200, (PACKAGE_ROOT / "static/index.html").read_bytes(), "text/html")
            if path == "/api/scenarios":
                # Expected IDs are deliberately omitted from the interactive task picker.
                suite = json.loads((DATA_ROOT / "scenarios.json").read_text())
                return self.send(200, {"cases": [{k: v for k, v in c.items() if k != "expected"} for c in suite["cases"]]})
            store = Store(db_path)
            try:
                if path == "/api/scopes":
                    return self.send(200, scopes(store))
                if path == "/api/info":
                    query = parse_qs(urlparse(self.path).query)
                    if "project" in query:
                        project = query["project"][0] if query["project"] else ""
                        ticket = query["ticket"][0] if "ticket" in query else ""
                        return self.send(200, info(store, project=project, ticket=ticket))
                    return self.send(200, info(store))
                if path == "/api/export":
                    return self.send(200, store.export())
                if path.startswith("/api/source/"):
                    result = store.source(path.rsplit("/", 1)[1])
                    return self.send(200 if result else 404, result or {"error": "Source not found"})
                if path.startswith("/api/revisions/"):
                    return self.send(200, {"revisions": store.revisions(path.rsplit("/", 1)[1])})
                self.send(404, {"error": "Not found"})
            finally:
                store.close()

        def do_POST(self):
            if not self.allowed():
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "Send application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    return self.send(413, {"error": "Request must contain 1 byte to 2 MB of JSON"})
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Request must be an object")
                path = urlparse(self.path).path
                if not path.startswith("/api/"):
                    return self.send(404, {"error": "Not found"})
                store = Store(db_path)
                try:
                    result = dispatch(store, path[5:], payload)
                finally:
                    store.close()
                self.send(200, result)
            except (ValueError, TypeError, KeyError, OSError) as e:
                self.send(400, {"error": str(e)})
            except Exception:
                logging.exception("Request failed")
                self.send(500, {"error": "Local request failed. See server log."})
    server = ThreadingHTTPServer((host, port), Handler)
    store = Store(db_path)
    try:
        waiting = sum(1 for m in store.memories() if m.get("status") == "candidate")
    finally:
        store.close()
    print(f"Context Lab is running at http://{host}:{server.server_address[1]}", flush=True)
    print("Nothing waiting to confirm" if waiting == 0 else f"{waiting} waiting to confirm", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
