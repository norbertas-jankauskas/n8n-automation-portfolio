"""Offline lead source and Airtable upsert mock.

Pure request handling lives in ``MockState.handle``; the thin HTTP adapter
binds 127.0.0.1 only and writes only the optional JSON-lines request log.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit


HOST = "127.0.0.1"
DEFAULT_PORT = 5690
TABLE_PATH = "/v0/appHNWJpExA6nWZks/tbly57I1Q5JJUyMlP"
FIELDS = frozenset(("Email", "Lead ID", "Name", "Budget", "Source", "Score", "Tier"))
CREATED_TIME = "2026-10-24T00:00:00.000Z"
LEADS = [{'lead_id': 'L1',
  'raw': '{"name":"Ana","email":"ANA@Example.com","budget":5000,"source":"web"}'},
 {'lead_id': 'L2',
  'raw': '{"name":"Ben","email":"ben@example.com","budget":800,"source":"ads"}'},
 {'lead_id': 'L3',
  'raw': '{"name":"Ana K","email":"ana@example.com","budget":6000,"source":"referral"}'},
 {'lead_id': 'L4', 'raw': '{"name":"Cid","email":"cid@example","budget":3000,"source":"web"}'},
 {'lead_id': 'L5', 'raw': 'not json'},
 {'lead_id': 'L6',
  'raw': '{"name":"Dee","email":"dee@example.com","budget":2500,"source":"referral"}'}]
PARTIAL_LEAD = {"lead_id": "L7", "raw":
                '{"name":"Eve","email":"eve@example.com","budget":4000,"source":"fax"}'}


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
    """In-memory Airtable records, keyed by the Email merge field."""

    def __init__(self, fixture=None):
        self.fixture = fixture
        self.records = {}

    def handle(self, method, path, query, prefer, body):
        """Return (status, JSON payload) for one decoded request."""
        if method == "GET" and path == "/leads":
            leads = LEADS + ([PARTIAL_LEAD] if self.fixture == "partial" else [])
            return 200, {"leads": deepcopy(leads)}
        if path == TABLE_PATH:
            if method == "GET":
                return 200, {"records": [deepcopy(self.records[email])
                                         for email in sorted(self.records)]}
            if method == "PATCH":
                return self._upsert(body)
        return 404, {"error": "not_found"}

    def snapshot(self):
        return {"leads": [dict(self.records[email]["fields"])
                          for email in sorted(self.records)]}

    def _upsert(self, body):
        invalid = (422, {"error": {"type": "INVALID_REQUEST_UNKNOWN",
                    "message": "Invalid request: parameter validation failed"}})
        if (not isinstance(body, dict) or set(body) != {"performUpsert", "records"}
                or body["performUpsert"] != {"fieldsToMergeOn": ["Email"]}
                or not isinstance(body["records"], list) or not 1 <= len(body["records"]) <= 10):
            return invalid
        # Validate the entire batch before modifying any record.
        for record in body["records"]:
            if (not isinstance(record, dict) or set(record) != {"fields"}
                    or not isinstance(record["fields"], dict)
                    or not set(record["fields"]) <= FIELDS
                    or not isinstance(record["fields"].get("Email"), str)
                    or not record["fields"]["Email"]):
                return invalid
            fields = record["fields"]
            for name, options in (("Source", ("referral", "web", "ads")),
                                  ("Tier", ("hot", "warm", "cold"))):
                if name in fields and fields[name] not in options:
                    return 422, {"error": {"type": "INVALID_MULTIPLE_CHOICE_OPTIONS",
                        "message": 'Insufficient permissions to create new select option ""'
                                   + str(fields[name]) + '""'}}
            for name in ("Budget", "Score"):
                if name in fields and type(fields[name]) is not int:
                    return 422, {"error": {"type": "INVALID_VALUE_FOR_COLUMN",
                                "message": "Invalid value for column " + name}}
        records, created, updated = [], [], []
        for record in body["records"]:
            fields = record["fields"]
            email = fields["Email"]
            if email in self.records:
                row = deepcopy(self.records[email])
                updated.append(row["id"])
                row["fields"].update(fields)
            else:
                row = {"id": "rec" + hashlib.sha256(email.encode()).hexdigest()[:14],
                       "createdTime": CREATED_TIME, "fields": dict(fields)}
                created.append(row["id"])
            self.records[email] = deepcopy(row)
            records.append(row)
        return 200, {"records": records, "createdRecords": created, "updatedRecords": updated}


class MockHandler(BaseHTTPRequestHandler):
    """Thin HTTP adapter; ``state`` and ``log_path`` are set by ``make_handler``."""

    state = None
    log_path = None

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PATCH(self):
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
