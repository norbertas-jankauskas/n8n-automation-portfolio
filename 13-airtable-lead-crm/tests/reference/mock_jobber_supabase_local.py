"""Offline mock of the Jobber GraphQL invoices API and Supabase REST upserts.

Pure request handling lives in ``MockState.handle``; ``MockHandler`` is a thin
``http.server`` wrapper. The server binds 127.0.0.1 only, makes no outbound
calls and writes nothing except the optional JSON-lines request log.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit


HOST = "127.0.0.1"
DEFAULT_PORT = 5690
MERGE_DUPLICATES = "resolution=merge-duplicates"

INVOICES = {
    "INV-1": {"id": "INV-1", "client": "Acme", "status": "paid",
              "total": "1200.00", "issuedAt": "2026-10-05"},
    "INV-2": {"id": "INV-2", "client": "Beta", "status": "paid",
              "total": "450.50", "issuedAt": "2026-10-06"},
    "INV-3": {"id": "INV-3", "client": "Acme", "status": "awaiting_payment",
              "total": "300.00", "issuedAt": "2026-10-07"},
    "INV-4": {"id": "INV-4", "client": "Gamma", "status": "paid",
              "total": "abc", "issuedAt": "2026-10-08"},
    "INV-5": {"id": "INV-5", "client": "Beta", "status": "paid",
              "total": "999.99", "issuedAt": "2026-10-13"},
}
# cursor -> (invoice ids, hasNextPage, endCursor)
PAGES = {
    None: (("INV-1", "INV-2"), True, "c2"),
    "c2": (("INV-3", "INV-4"), True, "c3"),
    "c3": (("INV-5",), False, None),
}
RATE_LIMITED_CURSOR = "c2"
# table -> conflict key
TABLES = {"invoices": "id", "kpi_weekly": "iso_week"}


def parse_body(raw):
    """Return the decoded JSON body, the raw text, or None when empty."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def log_entry(method, path, query, prefer, body, status):
    return {"method": method, "path": path, "query": query, "prefer": prefer,
            "body": body, "status": status}


class MockState:
    """In-memory tables plus the one-shot 429 flag for a single process."""

    def __init__(self, fixture=None):
        self.fixture = fixture
        self.tables = {name: {} for name in TABLES}
        self.rate_limited = False

    def handle(self, method, path, query, prefer, body):
        """Return (status, JSON payload or None) for one decoded request."""
        if path == "/graphql":
            if method != "POST":
                return 405, {"error": "method_not_allowed"}
            return self._graphql(body)
        if path.startswith("/rest/v1/"):
            table = path[len("/rest/v1/"):]
            if table not in TABLES:
                return 404, {"error": "unknown_table"}
            if method != "POST":
                return 405, {"error": "method_not_allowed"}
            return self._upsert(table, query, prefer, body)
        if path == "/__state":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, self.snapshot()
        return 404, {"error": "not_found"}

    def snapshot(self):
        return {name: [rows[key] for key in sorted(rows)]
                for name, rows in self.tables.items()}

    def _graphql(self, body):
        if not isinstance(body, dict):
            return 400, {"errors": [{"message": "body must be a JSON object"}]}
        cursor = body.get("after")
        if cursor is None:
            variables = body.get("variables")
            if isinstance(variables, dict):
                cursor = variables.get("after")
        if not isinstance(cursor, (str, type(None))) or cursor not in PAGES:
            return 400, {"errors": [{"message": "unknown cursor"}]}
        if cursor == RATE_LIMITED_CURSOR and not self.rate_limited:
            self.rate_limited = True
            return 429, {"errors": [{"message": "Throttled"}]}
        ids, has_next, end_cursor = PAGES[cursor]
        nodes = [dict(INVOICES[invoice_id]) for invoice_id in ids]
        if self.fixture == "partial" and cursor == "c3":
            nodes.append({"id": "INV-6", "client": "Delta", "status": "paid",
                          "total": "200000.00", "issuedAt": "2026-10-14"})
        return 200, {"data": {"invoices": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        }}}

    def _upsert(self, table, query, prefer, body):
        key = TABLES[table]
        if MERGE_DUPLICATES not in (prefer or ""):
            return 400, {"message": "Prefer must contain " + MERGE_DUPLICATES}
        if query.get("on_conflict") != [key]:
            return 400, {"message": "on_conflict must be " + key}
        if not isinstance(body, dict):
            return 400, {"message": "body must be one JSON object"}
        value = body.get(key)
        if not isinstance(value, str) or not value:
            return 400, {"message": "missing conflict key " + key}
        if table == "invoices" and "total_cents" in body:
            total_cents = body["total_cents"]
            if type(total_cents) is not int or not 0 <= total_cents < 10000000:
                return 400, {
                    "code": "23514",
                    "message": 'new row for relation "invoices" violates check '
                               'constraint "invoices_total_cents_check"',
                }
        row = dict(self.tables[table].get(value, {}))
        row.update(body)
        self.tables[table][value] = row
        if "return=representation" in prefer:
            return 201, [row]
        return 201, None


class MockHandler(BaseHTTPRequestHandler):
    """Thin HTTP adapter; ``state`` and ``log_path`` are set by ``make_handler``."""

    state = None
    log_path = None

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def _dispatch(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        length = int(self.headers.get("Content-Length") or 0)
        body = parse_body(self.rfile.read(length) if length > 0 else b"")
        prefer = self.headers.get("Prefer")
        status, payload = self.state.handle(self.command, parts.path, query,
                                            prefer, body)
        if self.log_path:
            entry = log_entry(self.command, parts.path, query, prefer, body, status)
            if self.log_path == "-":
                print("APC-REQ " + json.dumps(entry, sort_keys=True), flush=True)
            else:
                with open(self.log_path, "a", encoding="utf-8") as log:
                    log.write(json.dumps(entry, sort_keys=True) + "\n")
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def make_handler(state, log_path=None):
    return type("BoundMockHandler", (MockHandler,),
                {"state": state, "log_path": log_path})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log", default=None, help="JSON-lines request log path")
    parser.add_argument("--fixture", choices=("partial",))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = HTTPServer((HOST, args.port), make_handler(MockState(args.fixture), args.log))
    print("mock listening on http://%s:%d" % (HOST, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
