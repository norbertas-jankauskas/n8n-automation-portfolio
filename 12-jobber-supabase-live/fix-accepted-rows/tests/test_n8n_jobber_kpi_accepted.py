"""Accepted-row wiring and a Python reference model; no sockets or JS runtime."""

import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "workflow.json"
SPEC = importlib.util.spec_from_file_location(
    "local_kpi_reference", ROOT / "tests" / "test_n8n_jobber_kpi_local.py")
LOCAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOCAL)
PREFER = "resolution=merge-duplicates,return=representation"
CHECK_MESSAGE = ('new row for relation "invoices" violates check constraint '
                 '"invoices_total_cents_check"')


def load_workflow():
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def upsert_items(server, node, items):
    """Model per-item HTTP retries, success representations and the error output."""
    params = node["parameters"]
    path, _, query = params["url"][len(LOCAL.BASE):].partition("?")
    key, value = query.split("=")
    prefer = params["headerParameters"]["parameters"][0]["value"]
    fields = re.findall(r"(\w+): \$json\.(\w+)", params["jsonBody"])
    accepted, errors = [], []
    for item in items:
        body = {field: item[source] for field, source in fields}
        for _ in range(node["maxTries"]):
            status, payload = server.call("POST", path, {key: [value]}, prefer, body)
            if 200 <= status < 300:
                accepted.extend(payload)
                break
        else:
            if node.get("onError") != "continueErrorOutput":
                raise RuntimeError("upsert failed: %d" % status)
            errors.append({"stage": "upsert", "id": item["id"], "status": status,
                           "message": payload["message"]})
    return accepted, errors


def run_workflow(server, workflow):
    nodes = LOCAL.nodes_by_name(workflow)
    pages = LOCAL.fetch_pages(server, workflow, nodes["Fetch Invoices"]["maxTries"])
    invoices = [invoice for page in pages
                for invoice in page["data"]["invoices"]["nodes"]]
    rows, validation_errors = LOCAL.validate_and_transform(invoices)
    accepted, upsert_errors = upsert_items(server, nodes["Upsert Invoice"], rows)
    kpis = LOCAL.compute_kpis(accepted)
    upsert_items(server, nodes["Upsert KPI"], kpis)
    return accepted, validation_errors, upsert_errors, kpis


def recording_state(fixture=None):
    server = LOCAL.RecordingState()
    server.state = LOCAL.MOCK.MockState(fixture)
    return server


class AcceptedStructureTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.nodes = LOCAL.nodes_by_name(self.workflow)

    def test_only_requested_changes_from_local(self):
        local = LOCAL.load_workflow()
        self.assertEqual(self.workflow["name"], "APC sim — Jobber KPI from accepted rows")
        self.assertEqual(self.workflow["id"], "apcJobberKpiAcc1")
        restored = copy.deepcopy(self.workflow)
        restored["name"], restored["id"] = local["name"], local["id"]
        self.assertEqual(restored["nodes"].pop()["name"], "Build Upsert Error Report")
        next(node for node in restored["nodes"]
             if node["name"] == "Upsert Invoice").pop("onError")
        restored["connections"]["Validate & Transform"]["main"][0].append(
            {"node": "Compute KPIs", "type": "main", "index": 0})
        del restored["connections"]["Upsert Invoice"]
        self.assertEqual(restored, local)

    def test_compute_kpis_receives_only_invoice_success_output(self):
        incoming = [(source, output_index, edge["type"], edge["index"])
                    for source, connections in self.workflow["connections"].items()
                    for output_index, output in enumerate(connections["main"])
                    for edge in output if edge["node"] == "Compute KPIs"]
        self.assertEqual(incoming, [("Upsert Invoice", 0, "main", 0)])
        self.assertEqual(self.workflow["connections"]["Validate & Transform"]["main"], [
            [{"node": "Upsert Invoice", "type": "main", "index": 0}],
            [{"node": "Build Error Report", "type": "main", "index": 0}]])
        self.assertEqual(self.workflow["connections"]["Upsert Invoice"]["main"], [
            [{"node": "Compute KPIs", "type": "main", "index": 0}],
            [{"node": "Build Upsert Error Report", "type": "main", "index": 0}]])
        self.assertEqual(self.nodes["Upsert Invoice"]["onError"], "continueErrorOutput")
        headers = self.nodes["Upsert Invoice"]["parameters"]["headerParameters"]["parameters"]
        self.assertEqual(headers, [{"name": "Prefer", "value": PREFER}])

    def test_upsert_error_report_shape_and_item_linking(self):
        node = self.nodes["Build Upsert Error Report"]
        self.assertEqual((node["type"], node["typeVersion"]), ("n8n-nodes-base.code", 2))
        self.assertEqual(node["parameters"]["mode"], "runOnceForAllItems")
        self.assertEqual(node["parameters"]["language"], "javaScript")
        code = node["parameters"]["jsCode"]
        for fragment in ("$input.all().map", "stage: 'upsert'", "id:", "status:",
                         "message:", "itemMatching(index)", "error?.statusCode",
                         "error?.message", "statusInMessage"):
            self.assertIn(fragment, code)
        self.assertNotIn(node["name"], self.workflow["connections"])

    def test_allowlist_has_exact_types_and_only_mock_origin(self):
        policy = json.loads((ROOT / "allowlist.json").read_bytes())
        self.assertEqual(policy, {
            "node_types": {"n8n-nodes-base.code": [2],
                           "n8n-nodes-base.httpRequest": [4.5],
                           "n8n-nodes-base.manualTrigger": [1]},
            "origins": ["http://127.0.0.1:5690"], "allow_error_workflow": False})
        used = {}
        for node in self.workflow["nodes"]:
            used.setdefault(node["type"], set()).add(node["typeVersion"])
        self.assertEqual(used, {key: set(versions)
                               for key, versions in policy["node_types"].items()})


class AcceptedModelTests(unittest.TestCase):
    def test_default_rows_errors_kpis_and_state(self):
        server = recording_state()
        accepted, validation_errors, upsert_errors, kpis = run_workflow(server, load_workflow())
        self.assertEqual(accepted, LOCAL.EXPECTED_ROWS)
        self.assertEqual([(error["stage"], error["id"]) for error in validation_errors],
                         [("validate", "INV-4")])
        self.assertEqual(upsert_errors, [])
        self.assertEqual(kpis, LOCAL.EXPECTED_KPIS)
        self.assertEqual(server.state.snapshot(), {
            "invoices": LOCAL.EXPECTED_ROWS, "kpi_weekly": LOCAL.EXPECTED_KPIS})
        self.assertEqual([(entry["body"]["after"], entry["status"]) for entry in server.log
                          if entry["path"] == "/graphql"], LOCAL.EXPECTED_GRAPHQL_LOG)

    def test_partial_rejects_inv_six_without_inflating_kpis(self):
        server = recording_state("partial")
        accepted, validation_errors, upsert_errors, kpis = run_workflow(server, load_workflow())
        self.assertEqual(accepted, LOCAL.EXPECTED_ROWS)
        self.assertEqual([error["id"] for error in validation_errors], ["INV-4"])
        self.assertEqual(upsert_errors, [{"stage": "upsert", "id": "INV-6", "status": 400,
                                        "message": CHECK_MESSAGE}])
        self.assertEqual(kpis, LOCAL.EXPECTED_KPIS)
        self.assertEqual(server.state.snapshot(), {
            "invoices": LOCAL.EXPECTED_ROWS, "kpi_weekly": LOCAL.EXPECTED_KPIS})
        rejected = [entry for entry in server.log
                    if entry["path"] == "/rest/v1/invoices" and entry["status"] == 400]
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(entry["body"]["id"] == "INV-6" and
                            entry["body"]["total_cents"] == 20000000 for entry in rejected))

    def test_kpis_use_returned_representations(self):
        server = recording_state()
        original = server.call

        def return_adjusted_row(method, path, query, prefer, body):
            status, payload = original(method, path, query, prefer, body)
            if path == "/rest/v1/invoices" and status == 201 and body["id"] == "INV-1":
                payload = [dict(payload[0], total_cents=120001)]
            return status, payload

        with mock.patch.object(server, "call", side_effect=return_adjusted_row):
            accepted, _, _, kpis = run_workflow(server, load_workflow())
        self.assertEqual(accepted[0]["total_cents"], 120001)
        self.assertEqual(kpis, [dict(LOCAL.EXPECTED_KPIS[0], revenue_paid_cents=165051),
                                LOCAL.EXPECTED_KPIS[1]])

    def test_repeat_runs_are_idempotent_for_both_fixtures(self):
        for fixture in (None, "partial"):
            with self.subTest(fixture=fixture):
                server = recording_state(fixture)
                run_workflow(server, load_workflow())
                before = copy.deepcopy(server.state.snapshot())
                run_workflow(server, load_workflow())
                self.assertEqual(server.state.snapshot(), before)
                self.assertEqual(before, {"invoices": LOCAL.EXPECTED_ROWS,
                                          "kpi_weekly": LOCAL.EXPECTED_KPIS})

    def test_all_invoice_upserts_fail_so_no_kpi_rows_are_written(self):
        server = recording_state()
        original = server.call

        def reject_invoices(method, path, query, prefer, body):
            if path == "/rest/v1/invoices":
                return 400, {"code": "23514", "message": CHECK_MESSAGE}
            return original(method, path, query, prefer, body)

        with mock.patch.object(server, "call", side_effect=reject_invoices) as calls:
            accepted, _, errors, kpis = run_workflow(server, load_workflow())
        self.assertEqual(accepted, [])
        self.assertEqual(kpis, [])
        self.assertEqual([error["id"] for error in errors],
                         ["INV-1", "INV-2", "INV-3", "INV-5"])
        self.assertFalse(any(call.args[1] == "/rest/v1/kpi_weekly"
                             for call in calls.call_args_list))
        self.assertEqual(server.state.snapshot(), {"invoices": [], "kpi_weekly": []})


if __name__ == "__main__":
    unittest.main()
