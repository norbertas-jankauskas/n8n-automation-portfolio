"""Lead CRM mock rules and HTTP adapter, driven without sockets."""

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "mock/mock_lead_crm_local.py"
SPEC = importlib.util.spec_from_file_location("mock_lead_crm_local", SCRIPT)
MOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOCK)
WRAPPER_SPEC = importlib.util.spec_from_file_location("crm_wrapper_reference", ROOT / "tests/reference/supabase_mock_helpers.py")
WRAPPER = importlib.util.module_from_spec(WRAPPER_SPEC)
WRAPPER_SPEC.loader.exec_module(WRAPPER)
ROW = {"Email": "ana@example.com", "Lead ID": "L1", "Name": "Ana", "Budget": 5000,
       "Source": "web", "Score": 60, "Tier": "hot"}
INVALID = (422, {"error": {"type": "INVALID_REQUEST_UNKNOWN",
                          "message": "Invalid request: parameter validation failed"}})


def body(*rows):
    return {"performUpsert": {"fieldsToMergeOn": ["Email"]},
            "records": [{"fields": row} for row in rows]}


def patch(state, payload):
    return state.handle("PATCH", MOCK.TABLE_PATH, {}, None, payload)


class MockStateTests(unittest.TestCase):
    def test_source_exactly_matches_project_ten_and_partial_adds_only_l7(self):
        workflow = json.loads((ROOT / "tests/reference/lead-router.workflow.json").read_text())
        code = next(n for n in workflow["nodes"] if n["name"] == "Raw Leads")["parameters"]["jsCode"]
        expected = [{"lead_id": lead_id, "raw": raw}
                    for lead_id, raw in re.findall(r"lead_id: '(L[1-6])', raw: '([^']*)'", code)]
        self.assertEqual(len(expected), 6)
        default = MOCK.MockState().handle("GET", "/leads", {}, None, None)
        self.assertEqual(default, (200, {"leads": expected}))
        extra = {"lead_id": "L7", "raw": '{"name":"Eve","email":"eve@example.com","budget":4000,"source":"fax"}'}
        self.assertEqual(MOCK.MockState("partial").handle("GET", "/leads", {}, None, None),
                         (200, {"leads": expected + [extra]}))

    def test_create_update_and_partial_merge_return_full_records(self):
        state = MOCK.MockState()
        record_id = "rec" + hashlib.sha256(ROW["Email"].encode()).hexdigest()[:14]
        record = {"id": record_id, "createdTime": "2026-10-24T00:00:00.000Z", "fields": ROW}
        self.assertEqual(patch(state, body(ROW)), (200, {
            "records": [record], "createdRecords": [record_id], "updatedRecords": []}))
        update = {"Email": ROW["Email"], "Score": 70}
        merged = dict(ROW, Score=70)
        self.assertEqual(patch(state, body(update)), (200, {
            "records": [dict(record, fields=merged)], "createdRecords": [], "updatedRecords": [record_id]}))
        self.assertEqual(state.snapshot(), {"leads": [merged]})
        self.assertEqual(patch(state, body(update))[1]["records"], [dict(record, fields=merged)])

    def test_list_and_snapshot_sorted_by_email(self):
        state = MOCK.MockState()
        rows = [{"Email": email} for email in ("z@example.com", "a@example.com", "m@example.com")]
        patch(state, body(*rows))
        self.assertEqual(state.snapshot(), {"leads": sorted(rows, key=lambda row: row["Email"])})
        status, payload = state.handle("GET", MOCK.TABLE_PATH, {}, None, None)
        self.assertEqual(status, 200)
        self.assertEqual([row["fields"] for row in payload["records"]], state.snapshot()["leads"])
        self.assertEqual(state.handle("GET", MOCK.TABLE_PATH, {}, None, None)[1], payload)

    def test_one_to_ten_records_and_mixed_create_update(self):
        state = MOCK.MockState()
        rows = [{"Email": str(i)} for i in range(10)]
        result = patch(state, body(*rows))
        self.assertEqual(result[0], 200)
        self.assertEqual(len(result[1]["createdRecords"]), 10)
        self.assertEqual(result[1]["updatedRecords"], [])
        result = patch(state, body({"Email": "0", "Name": "Updated"}, {"Email": "new"}))
        self.assertEqual(result[0], 200)
        self.assertEqual(len(result[1]["createdRecords"]), 1)
        self.assertEqual(len(result[1]["updatedRecords"]), 1)
        self.assertEqual(len(state.snapshot()["leads"]), 11)

    def test_invalid_envelopes_and_fields_are_exact_validation_error(self):
        invalid = [None, [], "text", {}, {"records": []}, body(), body(*[{"Email": str(i)} for i in range(11)]),
                   dict(body(ROW), typecast=True), dict(body(ROW), extra=True),
                   dict(body(ROW), performUpsert={"fieldsToMergeOn": ["Name"]}),
                   dict(body(ROW), performUpsert={"fieldsToMergeOn": ["Email"], "extra": True}),
                   dict(body(ROW), records={}), dict(body(ROW), records=[None]),
                   dict(body(ROW), records=[{"fields": ROW, "id": "recOther"}]),
                   dict(body(ROW), records=[{}]), dict(body(ROW), records=[{"fields": []}]),
                   body({"Email": "x", "Extra": 1}), body({}), body({"Email": ""}),
                   body({"Email": None}), body({"Email": False}), body({"Email": 1}), body({"Email": []})]
        state = MOCK.MockState()
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(patch(state, payload), INVALID)
                self.assertEqual(state.snapshot(), {"leads": []})

    def test_select_options_reject_unknown_values(self):
        for field in ("Source", "Tier"):
            for value in ("fax", "HOT", "", None, [], {}, True, 5):
                with self.subTest(field=field, value=value):
                    state = MOCK.MockState()
                    self.assertEqual(patch(state, body(dict(ROW, **{field: value}))), (422, {
                        "error": {"type": "INVALID_MULTIPLE_CHOICE_OPTIONS",
                                  "message": 'Insufficient permissions to create new select option ""' + str(value) + '""'}}))
                    self.assertEqual(state.snapshot(), {"leads": []})

    def test_all_select_options_are_accepted_without_typecast(self):
        for source in ("referral", "web", "ads"):
            for tier in ("hot", "warm", "cold"):
                self.assertEqual(patch(MOCK.MockState(), body(dict(ROW, Source=source, Tier=tier)))[0], 200)

    def test_budget_and_score_require_int_excluding_bool(self):
        for field in ("Budget", "Score"):
            for value in (True, False, 1.0, "1", None, [], {}):
                with self.subTest(field=field, value=value):
                    state = MOCK.MockState()
                    status, payload = patch(state, body(dict(ROW, **{field: value})))
                    self.assertEqual(status, 422)
                    self.assertEqual(payload["error"]["type"], "INVALID_VALUE_FOR_COLUMN")
                    self.assertEqual(state.snapshot(), {"leads": []})
            for value in (-1, 0, 999999999999):
                self.assertEqual(patch(MOCK.MockState(), body(dict(ROW, **{field: value})))[0], 200)

    def test_any_422_preserves_existing_records_and_entire_batch(self):
        state = MOCK.MockState()
        patch(state, body(ROW))
        before = state.snapshot()
        invalid_rows = [{"Email": "bad", "Source": "fax"}, {"Email": "bad", "Budget": True},
                        {"Email": "bad", "Tier": "other"}, {"Email": ""}]
        for bad in invalid_rows:
            self.assertEqual(patch(state, body({"Email": ROW["Email"], "Name": "Changed"},
                                              {"Email": "new"}, bad))[0], 422)
            self.assertEqual(state.snapshot(), before)

    def test_inputs_and_returned_values_do_not_alias_state(self):
        state = MOCK.MockState()
        request = body(copy.deepcopy(ROW))
        before = copy.deepcopy(request)
        status, result = patch(state, request)
        self.assertEqual(status, 200)
        self.assertEqual(request, before)
        request["records"][0]["fields"]["Name"] = "Changed"
        result["records"][0]["fields"]["Name"] = "Changed"
        state.snapshot()["leads"][0]["Name"] = "Changed"
        state.handle("GET", MOCK.TABLE_PATH, {}, None, None)[1]["records"][0]["fields"]["Name"] = "Changed"
        self.assertEqual(state.snapshot(), {"leads": [ROW]})
        source = state.handle("GET", "/leads", {}, None, None)[1]
        source["leads"][0]["raw"] = "changed"
        self.assertEqual(state.handle("GET", "/leads", {}, None, None)[1]["leads"], MOCK.LEADS)

    def test_unknown_paths_and_methods_are_404(self):
        state = MOCK.MockState()
        for method, path in (("POST", MOCK.TABLE_PATH), ("DELETE", MOCK.TABLE_PATH), ("PATCH", "/leads"),
                             ("GET", "/__state"), ("GET", MOCK.TABLE_PATH + "/"), ("GET", "/other")):
            self.assertEqual(state.handle(method, path, {}, None, None), (404, {"error": "not_found"}))


class WrapperTests(unittest.TestCase):
    def test_get_patch_stdout_log_matches_apc_req_format(self):
        state = MOCK.MockState()
        handler = MOCK.make_handler(state, "-")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch("builtins.print", wraps=print) as printer:
            response = WRAPPER.call_wrapper(handler, "GET", "/leads")
            self.assertIn("200", response.splitlines()[0])
            response = WRAPPER.call_wrapper(handler, "PATCH", MOCK.TABLE_PATH + "?a=b", body(ROW))
            self.assertIn("200", response.splitlines()[0])
            self.assertIn('"createdRecords"', response)
        expected = [MOCK.log_entry("GET", "/leads", {}, None, None, 200),
                    MOCK.log_entry("PATCH", MOCK.TABLE_PATH, {"a": ["b"]}, None, body(ROW), 200)]
        lines = ["APC-REQ " + json.dumps(entry, sort_keys=True) for entry in expected]
        self.assertEqual(output.getvalue(), "".join(line + "\n" for line in lines))
        self.assertEqual(printer.call_args_list, [mock.call(line, flush=True) for line in lines])

    def test_file_log_and_no_log_and_invalid_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.jsonl"
            handler = MOCK.make_handler(MOCK.MockState(), str(path))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                WRAPPER.call_wrapper(handler, "PATCH", MOCK.TABLE_PATH, "bad json")
                WRAPPER.call_wrapper(handler, "GET", MOCK.TABLE_PATH)
                WRAPPER.call_wrapper(MOCK.make_handler(MOCK.MockState()), "GET", "/leads")
            entries = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(entries[0], MOCK.log_entry("PATCH", MOCK.TABLE_PATH, {}, None, "bad json", 422))
            self.assertEqual(entries[1], MOCK.log_entry("GET", MOCK.TABLE_PATH, {}, None, None, 200))
            self.assertEqual(output.getvalue(), "")
        self.assertIsNone(MOCK.parse_body(b""))
        self.assertEqual(MOCK.parse_body(b"{bad"), "{bad")

    def test_post_uses_the_same_adapter_and_reports_unknown_method(self):
        handler = MOCK.make_handler(MOCK.MockState())
        response = WRAPPER.call_wrapper(handler, "POST", MOCK.TABLE_PATH, body(ROW))
        self.assertEqual(int(response.split()[1]), 404)
        self.assertIn('"not_found"', response)

    def test_cli_defaults_partial_bind_and_server_cleanup(self):
        self.assertEqual(vars(MOCK.parse_args([])), {"port": 5690, "log": None, "fixture": None})
        output = io.StringIO()
        with mock.patch.object(MOCK, "HTTPServer") as factory, contextlib.redirect_stdout(output):
            factory.return_value.serve_forever.side_effect = KeyboardInterrupt
            MOCK.main(["--port", "5999", "--log", "-", "--fixture", "partial"])
            self.assertEqual(factory.call_args.args[0], ("127.0.0.1", 5999))
            self.assertEqual(factory.call_args.args[1].state.fixture, "partial")
            self.assertEqual(factory.call_args.args[1].log_path, "-")
            factory.return_value.server_close.assert_called_once_with()
        self.assertEqual(output.getvalue(), "mock listening on http://127.0.0.1:5999\n")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MOCK.parse_args(["--fixture", "unknown"])

    def test_mock_is_stdlib_only_and_has_no_outbound_calls(self):
        source = SCRIPT.read_text()
        modules = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module.split(".")[0])
        self.assertEqual(modules, {"argparse", "copy", "hashlib", "json", "http", "urllib"})
        for banned in ("subprocess", "socket", "urlopen", "http.client", "requests", "0.0.0.0", "os.system"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
