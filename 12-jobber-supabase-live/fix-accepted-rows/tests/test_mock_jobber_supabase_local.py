"""Mock handler rules and the thin HTTP wrapper; no sockets, no subprocess."""

import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "mock" / "mock_jobber_supabase_local.py"
SPEC = importlib.util.spec_from_file_location("mock_jobber_supabase_local", SCRIPT)
MOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOCK)
PREFER = "resolution=merge-duplicates,return=representation"
INVOICE_ROW = {"id": "INV-1", "client": "Acme", "status": "paid",
               "total_cents": 120000, "issued_at": "2026-10-05",
               "iso_week": "2026-W41"}
KPI_ROW = {"iso_week": "2026-W41", "revenue_paid_cents": 165050,
           "invoices_paid": 2, "invoices_open": 1, "avg_paid_cents": 82525}


def graphql(state, body):
    return state.handle("POST", "/graphql", {}, None, body)


def upsert(state, table, key, row, prefer=PREFER):
    return state.handle("POST", "/rest/v1/" + table, {"on_conflict": [key]},
                        prefer, row)


class GraphqlTests(unittest.TestCase):
    def test_pages_follow_cursor_and_first_c2_is_rate_limited(self):
        state = MOCK.MockState()
        status, page = graphql(state, {"query": "q", "after": None})
        self.assertEqual(status, 200)
        invoices = page["data"]["invoices"]
        self.assertEqual([n["id"] for n in invoices["nodes"]], ["INV-1", "INV-2"])
        self.assertEqual(invoices["pageInfo"], {"hasNextPage": True, "endCursor": "c2"})
        self.assertEqual(invoices["nodes"][1], {
            "id": "INV-2", "client": "Beta", "status": "paid",
            "total": "450.50", "issuedAt": "2026-10-06"})

        self.assertEqual(graphql(state, {"after": "c2"})[0], 429)
        status, page = graphql(state, {"after": "c2"})
        self.assertEqual(status, 200)
        invoices = page["data"]["invoices"]
        self.assertEqual([n["id"] for n in invoices["nodes"]], ["INV-3", "INV-4"])
        self.assertEqual(invoices["nodes"][1]["total"], "abc")
        self.assertEqual(invoices["pageInfo"], {"hasNextPage": True, "endCursor": "c3"})
        self.assertEqual(graphql(state, {"after": "c2"})[0], 200)

        status, page = graphql(state, {"after": "c3"})
        self.assertEqual(status, 200)
        invoices = page["data"]["invoices"]
        self.assertEqual(invoices["nodes"], [{
            "id": "INV-5", "client": "Beta", "status": "paid",
            "total": "999.99", "issuedAt": "2026-10-13"}])
        self.assertFalse(invoices["pageInfo"]["hasNextPage"])

    def test_cursor_from_variables_and_missing_cursor(self):
        state = MOCK.MockState()
        self.assertEqual(graphql(state, {"query": "q"})[0], 200)
        self.assertEqual(graphql(state, {"variables": {"after": "c3"}})[0], 200)
        self.assertEqual(graphql(state, {"variables": {"after": "c2"}})[0], 429)

    def test_rate_limit_is_per_state(self):
        self.assertEqual(graphql(MOCK.MockState(), {"after": "c2"})[0], 429)
        self.assertEqual(graphql(MOCK.MockState(), {"after": "c2"})[0], 429)

    def test_unknown_cursor_and_bad_body_are_400(self):
        state = MOCK.MockState()
        for body in ({"after": "c9"}, {"after": ""}, {"after": ["c2"]},
                     {"variables": {"after": "zz"}}, "not json", None, [1]):
            with self.subTest(body=body):
                self.assertEqual(graphql(state, body)[0], 400)
        self.assertFalse(state.rate_limited)

    def test_wrong_method_and_unknown_path(self):
        state = MOCK.MockState()
        self.assertEqual(state.handle("GET", "/graphql", {}, None, None)[0], 405)
        self.assertEqual(state.handle("POST", "/nope", {}, None, None)[0], 404)
        self.assertEqual(state.handle("POST", "/rest/v1/other", {}, PREFER, {})[0], 404)
        self.assertEqual(state.handle("GET", "/rest/v1/invoices", {}, PREFER, None)[0], 405)
        self.assertEqual(state.handle("POST", "/__state", {}, None, None)[0], 405)


class UpsertTests(unittest.TestCase):
    def test_invoice_check_rejects_invalid_totals_without_storing(self):
        for total in (-1, 10000000, 20000000, 1.0, "120000", True, False,
                      None, [], {}):
            with self.subTest(total=total):
                state = MOCK.MockState()
                self.assertEqual(upsert(state, "invoices", "id",
                                        dict(INVOICE_ROW, total_cents=total)), (400, {
                    "code": "23514",
                    "message": 'new row for relation "invoices" violates check '
                               'constraint "invoices_total_cents_check"',
                }))
                self.assertEqual(state.snapshot(), {"invoices": [], "kpi_weekly": []})

    def test_invoice_check_accepts_integer_boundaries(self):
        for total in (0, 9999999):
            with self.subTest(total=total):
                state = MOCK.MockState()
                row = dict(INVOICE_ROW, total_cents=total)
                self.assertEqual(upsert(state, "invoices", "id", row), (201, [row]))
                self.assertEqual(state.snapshot()["invoices"], [row])

    def test_rejected_merge_preserves_existing_invoice(self):
        state = MOCK.MockState()
        upsert(state, "invoices", "id", INVOICE_ROW)
        before = state.snapshot()
        self.assertEqual(upsert(state, "invoices", "id", {
            "id": "INV-1", "status": "void", "total_cents": -1})[0], 400)
        self.assertEqual(state.snapshot(), before)

    def test_absent_total_cents_keeps_partial_merge_behavior(self):
        state = MOCK.MockState()
        upsert(state, "invoices", "id", INVOICE_ROW)
        self.assertEqual(upsert(state, "invoices", "id", {
            "id": "INV-1", "status": "void"}),
            (201, [dict(INVOICE_ROW, status="void")]))
        self.assertEqual(upsert(state, "invoices", "id", {"id": "INV-new"}),
                         (201, [{"id": "INV-new"}]))

    def test_invoice_check_does_not_apply_to_kpi_rows(self):
        state = MOCK.MockState()
        row = dict(KPI_ROW, total_cents=-1)
        self.assertEqual(upsert(state, "kpi_weekly", "iso_week", row), (201, [row]))

    def test_upsert_requires_merge_duplicates(self):
        state = MOCK.MockState()
        for prefer in (None, "", "return=representation", "resolution=ignore-duplicates"):
            with self.subTest(prefer=prefer):
                self.assertEqual(upsert(state, "invoices", "id", INVOICE_ROW, prefer)[0], 400)
        self.assertEqual(state.snapshot(), {"invoices": [], "kpi_weekly": []})

    def test_conflict_key_rules(self):
        state = MOCK.MockState()
        row = dict(INVOICE_ROW)
        del row["id"]
        self.assertEqual(upsert(state, "invoices", "id", row)[0], 400)
        self.assertEqual(upsert(state, "invoices", "id", dict(row, id=""))[0], 400)
        self.assertEqual(upsert(state, "invoices", "id", dict(row, id=7))[0], 400)
        self.assertEqual(upsert(state, "kpi_weekly", "iso_week", {"invoices_paid": 1})[0], 400)
        self.assertEqual(upsert(state, "invoices", "iso_week", INVOICE_ROW)[0], 400)
        self.assertEqual(state.handle("POST", "/rest/v1/invoices", {}, PREFER,
                                      INVOICE_ROW)[0], 400)
        self.assertEqual(upsert(state, "invoices", "id", [INVOICE_ROW])[0], 400)
        self.assertEqual(state.snapshot(), {"invoices": [], "kpi_weekly": []})

    def test_upsert_creates_merges_and_is_idempotent(self):
        state = MOCK.MockState()
        self.assertEqual(upsert(state, "invoices", "id", INVOICE_ROW), (201, [INVOICE_ROW]))
        self.assertEqual(upsert(state, "kpi_weekly", "iso_week", KPI_ROW), (201, [KPI_ROW]))
        first = state.snapshot()
        upsert(state, "invoices", "id", INVOICE_ROW)
        upsert(state, "kpi_weekly", "iso_week", KPI_ROW)
        self.assertEqual(state.snapshot(), first)
        self.assertEqual(first, {"invoices": [INVOICE_ROW], "kpi_weekly": [KPI_ROW]})

        status, payload = upsert(state, "invoices", "id", {"id": "INV-1", "status": "void"},
                                 prefer="resolution=merge-duplicates")
        self.assertEqual((status, payload), (201, None))
        self.assertEqual(state.snapshot()["invoices"], [dict(INVOICE_ROW, status="void")])

    def test_state_endpoint_returns_both_tables(self):
        state = MOCK.MockState()
        upsert(state, "invoices", "id", dict(INVOICE_ROW, id="INV-2"))
        upsert(state, "invoices", "id", INVOICE_ROW)
        status, payload = state.handle("GET", "/__state", {}, None, None)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in payload["invoices"]], ["INV-1", "INV-2"])
        self.assertEqual(payload["kpi_weekly"], [])


class FakeHeaders(dict):
    def get(self, name, default=None):
        for key, value in self.items():
            if key.lower() == name.lower():
                return value
        return default


def call_wrapper(handler_class, method, path, body=None, prefer=None):
    """Drive the http.server handler with in-memory streams instead of a socket."""
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    handler = handler_class.__new__(handler_class)
    handler.command = method
    handler.path = path
    handler.request_version = "HTTP/1.1"
    handler.requestline = method + " " + path + " HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    headers = FakeHeaders({"Content-Length": str(len(raw))})
    if prefer is not None:
        headers["Prefer"] = prefer
    handler.headers = headers
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    getattr(handler, "do_" + method)()
    return handler.wfile.getvalue().decode("utf-8")


class WrapperTests(unittest.TestCase):
    def test_stdout_log_is_one_sorted_flushed_line_per_request(self):
        output = io.StringIO()
        handler = MOCK.make_handler(MOCK.MockState(), "-")
        requests = [
            ("POST", "/graphql", {"after": None}, None, 200),
            ("POST", "/graphql", {"after": "c2"}, None, 429),
            ("POST", "/graphql", {"after": "c2"}, None, 200),
            ("POST", "/rest/v1/invoices?on_conflict=id", INVOICE_ROW, PREFER, 201),
            ("GET", "/__state", None, None, 200),
        ]
        expected = []
        with contextlib.redirect_stdout(output), mock.patch("builtins.print", wraps=print) as printer:
            for method, path, body, prefer, status in requests:
                response = call_wrapper(handler, method, path, body, prefer)
                self.assertEqual(int(response.split()[1]), status)
                query = {"on_conflict": ["id"]} if "?" in path else {}
                entry = MOCK.log_entry(method, path.split("?")[0], query, prefer, body, status)
                expected.append("APC-REQ " + json.dumps(entry, sort_keys=True))
            self.assertEqual(printer.call_args_list,
                             [mock.call(line, flush=True) for line in expected])
        self.assertEqual(output.getvalue(), "".join(line + "\n" for line in expected))

    def test_file_log_bytes_and_no_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.jsonl"
            path.write_bytes(b"existing line\n")
            handler = MOCK.make_handler(MOCK.MockState(), str(path))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                call_wrapper(handler, "POST", "/graphql", {"after": None})
            entry = MOCK.log_entry("POST", "/graphql", {}, None, {"after": None}, 200)
            self.assertEqual(path.read_bytes(), b"existing line\n" +
                             (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8"))
            self.assertEqual(output.getvalue(), "")

    def test_stdout_log_cli_and_startup_without_prefix(self):
        self.assertEqual(MOCK.parse_args(["--log", "-"]).log, "-")
        self.assertEqual(MOCK.parse_args(["--log", "requests.jsonl"]).log, "requests.jsonl")
        self.assertIsNone(MOCK.parse_args([]).log)
        output = io.StringIO()
        with mock.patch.object(MOCK, "HTTPServer") as factory, contextlib.redirect_stdout(output):
            factory.return_value.serve_forever.side_effect = KeyboardInterrupt
            MOCK.main(["--log", "-"])
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[0], ("127.0.0.1", 5690))
            self.assertEqual(factory.call_args.args[1].log_path, "-")
            factory.return_value.server_close.assert_called_once_with()
        self.assertEqual(output.getvalue(), "mock listening on http://127.0.0.1:5690\n")

    def test_no_log_emits_no_stdout(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            call_wrapper(MOCK.make_handler(MOCK.MockState()), "GET", "/__state")
        self.assertEqual(output.getvalue(), "")

    def test_wrapper_logs_one_json_line_per_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "requests.jsonl"
            handler = MOCK.make_handler(MOCK.MockState(), str(log_path))
            response = call_wrapper(handler, "POST", "/graphql", {"after": None})
            self.assertEqual(response.split()[1], "200")
            self.assertIn('"endCursor": "c2"', response)
            response = call_wrapper(handler, "POST", "/graphql", {"after": "c2"})
            self.assertEqual(response.split()[1], "429")
            response = call_wrapper(handler, "POST", "/rest/v1/invoices?on_conflict=id",
                                    INVOICE_ROW, PREFER)
            self.assertEqual(response.split()[1], "201")
            response = call_wrapper(handler, "GET", "/__state")
            self.assertIn('"INV-1"', response)
            entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0], {"method": "POST", "path": "/graphql", "query": {},
                                      "prefer": None, "body": {"after": None},
                                      "status": 200})
        self.assertEqual(entries[1]["status"], 429)
        self.assertEqual(entries[2], {"method": "POST", "path": "/rest/v1/invoices",
                                      "query": {"on_conflict": ["id"]}, "prefer": PREFER,
                                      "body": INVOICE_ROW, "status": 201})
        self.assertEqual(entries[3]["path"], "/__state")
        self.assertEqual(entries[3]["body"], None)

    def test_wrapper_without_log_and_with_invalid_json(self):
        handler = MOCK.make_handler(MOCK.MockState())
        handler_obj = handler.__new__(handler)
        self.assertIsNone(handler_obj.log_path)
        self.assertEqual(MOCK.parse_body(b"{bad"), "{bad")
        self.assertIsNone(MOCK.parse_body(b""))
        response = call_wrapper(handler, "POST", "/graphql", None)
        self.assertEqual(response.split()[1], "400")

    def test_cli_defaults_and_bind_host(self):
        args = MOCK.parse_args([])
        self.assertEqual((args.port, args.log), (5690, None))
        args = MOCK.parse_args(["--port", "5999", "--log", "/tmp/x.jsonl"])
        self.assertEqual((args.port, args.log), (5999, "/tmp/x.jsonl"))
        self.assertEqual(MOCK.HOST, "127.0.0.1")

    def test_source_is_stdlib_only_without_outbound_or_subprocess(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module.split(".")[0])
        self.assertEqual(modules, {"argparse", "json", "http", "urllib"})
        source = SCRIPT.read_text(encoding="utf-8")
        for banned in ("subprocess", "socket", "urlopen", "http.client", "requests",
                       "0.0.0.0", "os.system"):
            self.assertNotIn(banned, source)
        self.assertIn("HTTPServer((HOST, args.port)", source)


if __name__ == "__main__":
    unittest.main()


class PartialFixtureTests(unittest.TestCase):
    def test_partial_adds_only_inv_six_to_last_page(self):
        default, partial = MOCK.MockState(), MOCK.MockState("partial")
        self.assertEqual(default.handle("POST", "/graphql", {}, None, {}),
                         partial.handle("POST", "/graphql", {}, None, {}))
        self.assertEqual(default.handle("POST", "/graphql", {}, None, {"after": "c2"}),
                         partial.handle("POST", "/graphql", {}, None, {"after": "c2"}))
        _, expected = default.handle("POST", "/graphql", {}, None, {"after": "c3"})
        _, actual = partial.handle("POST", "/graphql", {}, None, {"after": "c3"})
        extra = {"id": "INV-6", "client": "Delta", "status": "paid",
                 "total": "200000.00", "issuedAt": "2026-10-14"}
        self.assertEqual(actual["data"]["invoices"]["nodes"], expected["data"]["invoices"]["nodes"] + [extra])
        self.assertEqual(actual["data"]["invoices"]["pageInfo"], expected["data"]["invoices"]["pageInfo"])
        self.assertEqual(int(extra["total"].replace(".", "")), 20000000)
        self.assertNotIn("INV-6", MOCK.INVOICES)

    def test_cli_fixture_is_optional_and_partial_only(self):
        self.assertIsNone(MOCK.parse_args([]).fixture)
        self.assertEqual(MOCK.parse_args(["--fixture", "partial", "--log", "-"]).fixture, "partial")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MOCK.parse_args(["--fixture", "other"])
