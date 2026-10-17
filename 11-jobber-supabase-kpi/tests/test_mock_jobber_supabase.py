import ast
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import mock_open, patch


MOCK_PATH = (
    Path(__file__).resolve().parent.parent / "mock" / "mock_jobber_supabase.py"
)
SPEC = importlib.util.spec_from_file_location("mock_jobber_supabase", MOCK_PATH)
MOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOCK)
PREFER = {"Prefer": "resolution=merge-duplicates"}


class TestMockJobberSupabase(unittest.TestCase):
    def setUp(self):
        self.mock = MOCK.MockJobberSupabase()

    def fetch(self, cursor, nested=False):
        body = {"variables": {"after": cursor}} if nested else {"after": cursor}
        return self.mock.handle("POST", "/graphql", body=body)

    def upsert(self, table, key, body, headers=None):
        return self.mock.handle(
            "POST", "/rest/v1/" + table, {"on_conflict": [key]},
            PREFER if headers is None else headers, body,
        )

    def test_exact_pages_and_one_rate_limit(self):
        status, page = self.fetch(None)
        self.assertEqual(status, 200)
        self.assertEqual(page, {"data": {"invoices": {
            "nodes": [
                {"id": "INV-1", "client": "Acme", "status": "paid",
                 "total": "1200.00", "issuedAt": "2026-10-05"},
                {"id": "INV-2", "client": "Beta", "status": "paid",
                 "total": "450.50", "issuedAt": "2026-10-06"},
            ],
            "pageInfo": {"hasNextPage": True, "endCursor": "c2"},
        }}})
        self.assertEqual(self.fetch("c2"), (429, {"error": "rate_limited"}))
        status, page = self.fetch("c2")
        self.assertEqual(status, 200)
        self.assertEqual(page, {"data": {"invoices": {
            "nodes": [
                {"id": "INV-3", "client": "Acme", "status": "awaiting_payment",
                 "total": "300.00", "issuedAt": "2026-10-07"},
                {"id": "INV-4", "client": "Gamma", "status": "paid",
                 "total": "abc", "issuedAt": "2026-10-08"},
            ],
            "pageInfo": {"hasNextPage": True, "endCursor": "c3"},
        }}})
        self.assertEqual(self.fetch("c2"), (status, page))
        self.assertEqual(self.fetch("c3"), (200, {"data": {"invoices": {
            "nodes": [{"id": "INV-5", "client": "Beta", "status": "paid",
                       "total": "999.99", "issuedAt": "2026-10-13"}],
            "pageInfo": {"hasNextPage": False, "endCursor": "c3"},
        }}}))

    def test_both_cursor_locations_share_throttle_state(self):
        self.assertEqual(self.fetch(None), self.fetch(None, nested=True))
        self.assertEqual(self.fetch("c2", nested=True)[0], 429)
        self.assertEqual(self.fetch("c2")[0], 200)
        self.assertEqual(self.fetch("c3"), self.fetch("c3", nested=True))
        # Restarting page one does not reset the process's injected failure.
        self.fetch(None)
        self.assertEqual(self.fetch("c2", nested=True)[0], 200)

    def test_fresh_process_model_has_independent_state(self):
        self.assertEqual(self.fetch("c2")[0], 429)
        self.assertEqual(self.fetch("c2")[0], 200)
        fresh = MOCK.MockJobberSupabase()
        self.assertEqual(fresh.handle("POST", "/graphql", body={"after": "c2"})[0], 429)
        self.assertEqual(fresh.handle("GET", "/__state"),
                         (200, {"invoices": [], "kpi_weekly": []}))

    def test_top_level_cursor_takes_precedence_including_null(self):
        status, page = self.mock.handle("POST", "/graphql", body={
            "after": None, "variables": {"after": "c3"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(page["data"]["invoices"]["nodes"][0]["id"], "INV-1")
        self.assertEqual(self.mock.handle("POST", "/graphql", body={}), self.fetch(None))

    def test_unknown_cursors_and_non_object_bodies_return_400(self):
        for cursor in ("unknown", "", 2, [], {}):
            with self.subTest(cursor=cursor):
                self.assertEqual(self.fetch(cursor), (400, {"error": "unknown_cursor"}))
        for body in (None, [], "not json", 123):
            for path in ("/graphql", "/rest/v1/invoices", "/rest/v1/kpi_weekly"):
                with self.subTest(body=body, path=path):
                    self.assertEqual(self.mock.handle("POST", path, body=body)[0], 400)

    def test_upserts_replace_instead_of_append_for_both_tables(self):
        for table, key in (("invoices", "id"), ("kpi_weekly", "iso_week")):
            with self.subTest(table=table):
                original = {key: "row-one", "old": True}
                replacement = {key: "row-one", "new": 42}
                second = {key: "row-two", "new": 99}
                self.assertEqual(self.upsert(table, key, original), (201, original))
                self.assertEqual(self.upsert(table, key, replacement), (201, replacement))
                self.assertEqual(self.upsert(table, key, second), (201, second))
                state = self.mock.handle("GET", "/__state")[1][table]
                self.assertEqual(state, [replacement, second])
                self.upsert(table, key, replacement)
                self.assertEqual(self.mock.handle("GET", "/__state")[1][table], state)

    def test_prefer_is_required_contains_directive_and_is_case_insensitive(self):
        for table, key in (("invoices", "id"), ("kpi_weekly", "iso_week")):
            for headers in ({}, {"Prefer": "return=representation"}, {"Prefer": None}):
                with self.subTest(table=table, headers=headers):
                    self.assertEqual(self.upsert(table, key, {key: "A"}, headers)[0], 400)
            headers = {"pReFeR": "return=representation, resolution=merge-duplicates"}
            self.assertEqual(self.upsert(table, key, {key: "A"}, headers)[0], 201)

    def test_conflict_key_and_query_are_required(self):
        for table, key in (("invoices", "id"), ("kpi_weekly", "iso_week")):
            for body in ({}, {key: None}, {key: ""}, {key: []}):
                with self.subTest(table=table, body=body):
                    self.assertEqual(self.upsert(table, key, body)[0], 400)
            for query in ({}, {"on_conflict": ["wrong"]},
                          {"on_conflict": []}, {"on_conflict": [key, key]}):
                with self.subTest(table=table, query=query):
                    self.assertEqual(self.mock.handle(
                        "POST", "/rest/v1/" + table, query, PREFER, {key: "A"},
                    )[0], 400)
        self.assertEqual(self.mock.handle("GET", "/__state")[1],
                         {"invoices": [], "kpi_weekly": []})

    def test_log_records_each_request_and_copies_payloads(self):
        body = {"id": "A", "client": "Acme"}
        query = {"on_conflict": "id"}
        status, response = self.mock.handle(
            "POST", "/rest/v1/invoices", query, PREFER, body,
        )
        expected = {"method": "POST", "path": "/rest/v1/invoices",
                    "query": deepcopy(query), "Prefer": PREFER["Prefer"],
                    "body": deepcopy(body), "status": 201}
        self.assertEqual(status, 201)
        body["client"] = "changed input"
        query["on_conflict"] = "changed query"
        response["client"] = "changed response"
        self.assertEqual(self.mock.request_log, [expected])
        state = self.mock.handle("GET", "/__state")[1]
        state["invoices"][0]["client"] = "changed state response"
        self.assertEqual(self.mock.handle("GET", "/__state")[1]["invoices"],
                         [expected["body"]])
        self.assertEqual(self.fetch("c2")[0], 429)
        self.assertEqual(self.mock.request_log[-1]["status"], 429)

    def test_unknown_routes_and_methods(self):
        for method, path in (("POST", "/unknown"), ("GET", "/graphql"),
                             ("PUT", "/rest/v1/invoices")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.mock.handle(method, path, body={})[0], 404)

    def test_http_adapter_with_buffers_and_mocked_log_only(self):
        # Bypass BaseHTTPRequestHandler initialization: no socket or server.
        handler_class = MOCK.make_http_handler(self.mock, Path("unused-log.jsonl"))
        for payload, expected_status in ((b'{"after": null}', 200), (b'bad json', 400)):
            with self.subTest(payload=payload):
                handler = object.__new__(handler_class)
                handler.path = "/graphql?example=yes"
                handler.command = "POST"
                handler.headers = {"Content-Length": str(len(payload))}
                handler.rfile = io.BytesIO(payload)
                handler.wfile = io.BytesIO()
                responses, headers = [], {}
                handler.send_response = responses.append
                handler.send_header = lambda name, value: headers.update({name: value})
                handler.end_headers = lambda: None
                log = mock_open()
                with patch.object(Path, "open", log):
                    handler.do_POST()
                self.assertEqual(responses, [expected_status])
                written = log().write.call_args.args[0]
                self.assertEqual(len(written.splitlines()), 1)
                self.assertEqual(json.loads(written), self.mock.request_log[-1])
                self.assertEqual(json.loads(written)["query"], {"example": ["yes"]})
                self.assertEqual(int(headers["Content-Length"]), len(handler.wfile.getvalue()))
                self.assertIsInstance(json.loads(handler.wfile.getvalue()), dict)

    def test_cli_boundary_is_static_and_import_does_not_start_server(self):
        source = MOCK_PATH.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('HTTPServer(("127.0.0.1", args.port)', source)
        self.assertIn('parser.add_argument("--port", type=int, default=5690)', source)
        self.assertIn('parser.add_argument("--log", type=Path, required=True)', source)
        self.assertIn('if __name__ == "__main__":', source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("create_connection", source)


if __name__ == "__main__":
    unittest.main()
