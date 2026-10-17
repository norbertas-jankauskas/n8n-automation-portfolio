"""Local Jobber/Supabase fixtures; handler tests need no HTTP server."""

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PAGES = {
    None: (
        [
            {"id": "INV-1", "client": "Acme", "status": "paid",
             "total": "1200.00", "issuedAt": "2026-10-05"},
            {"id": "INV-2", "client": "Beta", "status": "paid",
             "total": "450.50", "issuedAt": "2026-10-06"},
        ],
        {"hasNextPage": True, "endCursor": "c2"},
    ),
    "c2": (
        [
            {"id": "INV-3", "client": "Acme", "status": "awaiting_payment",
             "total": "300.00", "issuedAt": "2026-10-07"},
            {"id": "INV-4", "client": "Gamma", "status": "paid",
             "total": "abc", "issuedAt": "2026-10-08"},
        ],
        {"hasNextPage": True, "endCursor": "c3"},
    ),
    "c3": (
        [{"id": "INV-5", "client": "Beta", "status": "paid",
          "total": "999.99", "issuedAt": "2026-10-13"}],
        {"hasNextPage": False, "endCursor": "c3"},
    ),
}
CONFLICT_KEYS = {"invoices": "id", "kpi_weekly": "iso_week"}


class MockJobberSupabase:
    """One instance per server process; all request logic stays in memory."""

    def __init__(self):
        self.tables = {table: {} for table in CONFLICT_KEYS}
        self.c2_throttled = False
        self.request_log = []

    def handle(self, method, path, query=None, headers=None, body=None):
        """Return (status, JSON value), without sockets or filesystem writes.

        query is a parse_qs-style mapping (scalar strings also work).
        body is an already decoded JSON value. Prefer lookup ignores case.
        """
        query = {} if query is None else query
        headers = {} if headers is None else headers
        prefer = next((value for key, value in headers.items()
                       if key.lower() == "prefer"), "")
        status, response = self._dispatch(method, path, query, prefer, body)
        self.request_log.append(deepcopy({
            "method": method, "path": path, "query": query,
            "Prefer": prefer, "body": body, "status": status,
        }))
        return status, deepcopy(response)

    def _dispatch(self, method, path, query, prefer, body):
        if method == "GET" and path == "/__state":
            return 200, {table: list(rows.values())
                         for table, rows in self.tables.items()}
        if method != "POST":
            return 404, {"error": "not_found"}
        if not isinstance(body, dict):
            return 400, {"error": "expected_json_object"}
        if path == "/graphql":
            variables = body.get("variables")
            variables = variables if isinstance(variables, dict) else {}
            cursor = body.get("after", variables.get("after"))
            if cursor is not None and not isinstance(cursor, str):
                return 400, {"error": "unknown_cursor"}
            if cursor not in PAGES:
                return 400, {"error": "unknown_cursor"}
            if cursor == "c2" and not self.c2_throttled:
                self.c2_throttled = True
                return 429, {"error": "rate_limited"}
            nodes, page_info = PAGES[cursor]
            return 200, {"data": {"invoices": {
                "nodes": nodes, "pageInfo": page_info,
            }}}
        table = path.removeprefix("/rest/v1/")
        if path != "/rest/v1/" + table or table not in CONFLICT_KEYS:
            return 404, {"error": "not_found"}
        conflict_key = CONFLICT_KEYS[table]
        conflict = query.get("on_conflict")
        if isinstance(conflict, list):
            conflict = conflict[0] if len(conflict) == 1 else None
        if conflict != conflict_key:
            return 400, {"error": "invalid_on_conflict"}
        if not isinstance(prefer, str) or "resolution=merge-duplicates" not in prefer:
            return 400, {"error": "missing_merge_preference"}
        key = body.get(conflict_key)
        if not isinstance(key, str) or not key:
            return 400, {"error": "missing_conflict_key"}
        self.tables[table][key] = deepcopy(body)
        return 201, body


def make_http_handler(mock, log_path):
    """Adapt HTTP input to the pure handler and append one JSON log line."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            parsed = urlsplit(self.path)
            body = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 0:
                    body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                # Malformed POST bodies follow the handler's 400 path.
                body = None
            status, response = mock.handle(
                self.command, parsed.path, parse_qs(parsed.query),
                dict(self.headers.items()), body,
            )
            with Path(log_path).open("a", encoding="utf-8") as log:
                log.write(json.dumps(mock.request_log[-1], ensure_ascii=False) + "\n")
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5690)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args(argv)
    mock = MockJobberSupabase()
    with HTTPServer(("127.0.0.1", args.port),
                    make_http_handler(mock, args.log)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
