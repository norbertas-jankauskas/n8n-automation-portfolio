"""Workflow structure plus a Python model of the run; no sockets, no subprocess."""

import datetime
import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "base-workflow.json"
SCRIPT = ROOT / "mock" / "mock_jobber_supabase_local.py"
SPEC = importlib.util.spec_from_file_location("mock_jobber_supabase_local", SCRIPT)
MOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOCK)

BASE = "http://127.0.0.1:5690"
MANUAL_TRIGGER = "n8n-nodes-base.manualTrigger"
CODE = "n8n-nodes-base.code"
HTTP = "n8n-nodes-base.httpRequest"
NODES = {
    "Manual Trigger": (MANUAL_TRIGGER, 1),
    "Fetch Invoices": (HTTP, 4.5),
    "Flatten": (CODE, 2),
    "Validate & Transform": (CODE, 2),
    "Build Error Report": (CODE, 2),
    "Upsert Invoice": (HTTP, 4.5),
    "Compute KPIs": (CODE, 2),
    "Upsert KPI": (HTTP, 4.5),
}
TOTAL_REGEX = r"^\d+\.\d{2}$"
EXPECTED_ROWS = [
    {"id": "INV-1", "client": "Acme", "status": "paid", "total_cents": 120000,
     "issued_at": "2026-10-05", "iso_week": "2026-W41"},
    {"id": "INV-2", "client": "Beta", "status": "paid", "total_cents": 45050,
     "issued_at": "2026-10-06", "iso_week": "2026-W41"},
    {"id": "INV-3", "client": "Acme", "status": "awaiting_payment",
     "total_cents": 30000, "issued_at": "2026-10-07", "iso_week": "2026-W41"},
    {"id": "INV-5", "client": "Beta", "status": "paid", "total_cents": 99999,
     "issued_at": "2026-10-13", "iso_week": "2026-W42"},
]
EXPECTED_KPIS = [
    {"iso_week": "2026-W41", "revenue_paid_cents": 165050, "invoices_paid": 2,
     "invoices_open": 1, "avg_paid_cents": 82525},
    {"iso_week": "2026-W42", "revenue_paid_cents": 99999, "invoices_paid": 1,
     "invoices_open": 0, "avg_paid_cents": 99999},
]
# n8n 2.41.7: a non-2xx page throws out of the paginated request, and
# retryOnFail re-runs the whole node, so pagination restarts at the first page.
EXPECTED_GRAPHQL_LOG = [(None, 200), ("c2", 429), (None, 200), ("c2", 200), ("c3", 200)]


def load_workflow():
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def nodes_by_name(workflow):
    return {node["name"]: node for node in workflow["nodes"]}


def total_to_cents(total):
    """Mirror Validate & Transform: regex check, then integer string math."""
    if not isinstance(total, str) or re.search(TOTAL_REGEX, total) is None:
        raise ValueError("invalid_total: total %s" % json.dumps(total))
    units, cents = total.split(".")
    return int(units + cents)


def iso_week(issued_at):
    """Mirror the JS: move to the week's Thursday, count weeks from Jan 1."""
    year, month, day = (int(part) for part in issued_at.split("-"))
    date = datetime.date(year, month, day)
    thursday = date + datetime.timedelta(days=4 - date.isoweekday())
    week = -(-((thursday - datetime.date(thursday.year, 1, 1)).days + 1) // 7)
    return "%d-W%02d" % (thursday.year, week)


def validate_and_transform(invoices):
    rows, errors = [], []
    for invoice in invoices:
        try:
            total_cents = total_to_cents(invoice["total"])
        except ValueError as error:
            errors.append({"stage": "validate", "id": invoice["id"],
                           "total": invoice["total"], "reason": str(error)})
            continue
        rows.append({"id": invoice["id"], "client": invoice["client"],
                     "status": invoice["status"], "total_cents": total_cents,
                     "issued_at": invoice["issuedAt"],
                     "iso_week": iso_week(invoice["issuedAt"])})
    return rows, errors


def compute_kpis(rows):
    weeks = {}
    for row in rows:
        week = weeks.setdefault(row["iso_week"], {
            "iso_week": row["iso_week"], "revenue_paid_cents": 0,
            "invoices_paid": 0, "invoices_open": 0})
        if row["status"] == "paid":
            week["revenue_paid_cents"] += row["total_cents"]
            week["invoices_paid"] += 1
        elif row["status"] == "awaiting_payment":
            week["invoices_open"] += 1
    for week in weeks.values():
        paid = week["invoices_paid"]
        week["avg_paid_cents"] = week["revenue_paid_cents"] // paid if paid else 0
    return [weeks[key] for key in sorted(weeks)]


class RecordingState:
    """Calls the pure mock handler and records a log like the HTTP wrapper."""

    def __init__(self):
        self.state = MOCK.MockState()
        self.log = []

    def call(self, method, path, query, prefer, body):
        status, payload = self.state.handle(method, path, query, prefer, body)
        self.log.append(MOCK.log_entry(method, path, query, prefer, body, status))
        return status, payload


def fetch_pages(server, workflow, max_tries):
    """Model Fetch Invoices: built-in pagination plus whole-node retryOnFail."""
    params = nodes_by_name(workflow)["Fetch Invoices"]["parameters"]
    template = json.loads(params["jsonBody"])
    max_requests = params["options"]["pagination"]["pagination"]["maxRequests"]
    for _ in range(max_tries):
        pages, cursor, failed = [], None, False
        while True:
            body = dict(template, after=cursor)
            status, payload = server.call("POST", "/graphql", {}, None, body)
            if not 200 <= status < 300:
                failed = True
                break
            pages.append(payload)
            info = payload["data"]["invoices"]["pageInfo"]
            if len(pages) >= max_requests or info["hasNextPage"] is not True:
                break
            cursor = info["endCursor"]
        if not failed:
            return pages
    raise RuntimeError("Fetch Invoices failed after retries")


def run_workflow(server, workflow):
    nodes = nodes_by_name(workflow)
    pages = fetch_pages(server, workflow, nodes["Fetch Invoices"]["maxTries"])
    invoices = [node for page in pages for node in page["data"]["invoices"]["nodes"]]
    rows, errors = validate_and_transform(invoices)
    kpis = compute_kpis(rows)
    upserts = (("Upsert Invoice", rows), ("Upsert KPI", kpis))
    for name, items in upserts:
        upsert_items(server, nodes[name], items)
    return rows, errors, kpis


def upsert_items(server, node, items):
    params = node["parameters"]
    path, _, query = params["url"][len(BASE):].partition("?")
    key, value = query.split("=")
    prefer = params["headerParameters"]["parameters"][0]["value"]
    fields = re.findall(r"(\w+): \$json\.(\w+)", params["jsonBody"])
    for item in items:
        body = {field: item[source] for field, source in fields}
        status, _ = server.call("POST", path, {key: [value]}, prefer, body)
        if status != 201:
            raise RuntimeError("upsert failed: %d" % status)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.nodes = nodes_by_name(self.workflow)

    def test_identity_inactive_and_no_credentials(self):
        self.assertEqual(self.workflow["id"], "apcJobberKpiL001")
        self.assertIs(self.workflow["active"], False)
        self.assertNotIn("credentials", WORKFLOW_PATH.read_text(encoding="utf-8"))
        for node in self.workflow["nodes"]:
            self.assertNotIn("credentials", node)
            self.assertEqual(node["parameters"].get("authentication", "none"), "none")

    def test_node_types_and_versions(self):
        self.assertEqual({name: (node["type"], node["typeVersion"])
                          for name, node in self.nodes.items()}, NODES)
        self.assertEqual(len(self.workflow["nodes"]), len(NODES))
        self.assertEqual(len({node["id"] for node in self.workflow["nodes"]}), len(NODES))

    def test_connections_including_dual_success_wiring(self):
        connections = {
            source: [[link["node"] for link in output] for output in value["main"]]
            for source, value in self.workflow["connections"].items()
        }
        self.assertEqual(connections, {
            "Manual Trigger": [["Fetch Invoices"]],
            "Fetch Invoices": [["Flatten"]],
            "Flatten": [["Validate & Transform"]],
            "Validate & Transform": [["Upsert Invoice", "Compute KPIs"],
                                     ["Build Error Report"]],
            "Compute KPIs": [["Upsert KPI"]],
        })
        for value in self.workflow["connections"].values():
            for output in value["main"]:
                for link in output:
                    self.assertEqual((link["type"], link["index"]), ("main", 0))

    def test_fetch_pagination_and_retry(self):
        fetch = self.nodes["Fetch Invoices"]
        params = fetch["parameters"]
        self.assertEqual((params["method"], params["url"]), ("POST", BASE + "/graphql"))
        self.assertEqual((params["sendBody"], params["contentType"], params["specifyBody"]),
                         (True, "json", "json"))
        body = json.loads(params["jsonBody"])
        self.assertIsNone(body["after"])
        self.assertIn("pageInfo { hasNextPage endCursor }", body["query"])
        pagination = params["options"]["pagination"]["pagination"]
        self.assertEqual(pagination["paginationMode"], "updateAParameterInEachRequest")
        self.assertEqual(pagination["parameters"]["parameters"], [{
            "type": "body", "name": "after",
            "value": "={{ $response.body?.data?.invoices?.pageInfo?.endCursor ?? null }}",
        }])
        self.assertEqual(pagination["paginationCompleteWhen"], "other")
        expression = pagination["completeExpression"]
        self.assertTrue(expression.startswith("={{") and expression.endswith("}}"))
        self.assertIn("pageInfo?.hasNextPage !== true", expression)
        self.assertEqual((pagination["limitPagesFetched"], pagination["maxRequests"]),
                         (True, 10))
        self.assertEqual((fetch["retryOnFail"], fetch["maxTries"], fetch["waitBetweenTries"]),
                         (True, 3, 1000))

    def test_upserts_post_with_prefer_and_retry(self):
        expected = {
            "Upsert Invoice": ("/rest/v1/invoices?on_conflict=id",
                               ["id", "client", "status", "total_cents", "issued_at",
                                "iso_week"]),
            "Upsert KPI": ("/rest/v1/kpi_weekly?on_conflict=iso_week",
                           ["iso_week", "revenue_paid_cents", "invoices_paid",
                            "invoices_open", "avg_paid_cents"]),
        }
        for name, (path, fields) in expected.items():
            with self.subTest(node=name):
                node = self.nodes[name]
                params = node["parameters"]
                self.assertEqual((params["method"], params["url"]), ("POST", BASE + path))
                self.assertEqual((params["sendHeaders"], params["specifyHeaders"]),
                                 (True, "keypair"))
                headers = params["headerParameters"]["parameters"]
                self.assertEqual(len(headers), 1)
                self.assertEqual(headers[0]["name"], "Prefer")
                self.assertIn("resolution=merge-duplicates", headers[0]["value"])
                self.assertEqual((params["sendBody"], params["contentType"],
                                  params["specifyBody"]), (True, "json", "json"))
                self.assertTrue(params["jsonBody"].startswith("={{ JSON.stringify({"))
                self.assertEqual(re.findall(r"(\w+): \$json\.\w+", params["jsonBody"]),
                                 fields)
                self.assertEqual((node["retryOnFail"], node["maxTries"]), (True, 3))

    def test_code_nodes_modes_and_error_output(self):
        modes = {name: node["parameters"]["mode"] for name, node in self.nodes.items()
                 if node["type"] == CODE}
        self.assertEqual(modes, {
            "Flatten": "runOnceForAllItems",
            "Validate & Transform": "runOnceForEachItem",
            "Build Error Report": "runOnceForAllItems",
            "Compute KPIs": "runOnceForAllItems",
        })
        self.assertEqual(self.nodes["Validate & Transform"]["onError"], "continueErrorOutput")
        for name, node in self.nodes.items():
            if name != "Validate & Transform":
                self.assertNotIn("onError", node)
        code = self.nodes["Validate & Transform"]["parameters"]["jsCode"]
        self.assertIn("/^\\d+\\.\\d{2}$/", code)
        self.assertIn("throw new Error(`invalid_total:", code)
        self.assertIn("parseInt(units + cents, 10)", code)
        self.assertNotIn("parseFloat", code)
        self.assertIn("-W${String(week).padStart(2, '0')}", code)
        kpis = self.nodes["Compute KPIs"]["parameters"]["jsCode"]
        self.assertIn("Math.floor(week.revenue_paid_cents / week.invoices_paid)", kpis)

    def test_urls_only_local_mock(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        urls = re.findall(r"https?://[^\s\"'?]+", text)
        self.assertEqual(len(urls), 3)
        for url in urls:
            self.assertTrue(url.startswith(BASE + "/"), url)
        for node in self.workflow["nodes"]:
            if node["type"] == HTTP:
                self.assertTrue(node["parameters"]["url"].startswith(BASE + "/"))


class ModelTests(unittest.TestCase):
    def test_total_and_week_helpers(self):
        self.assertEqual(total_to_cents("450.50"), 45050)
        self.assertEqual(total_to_cents("0.07"), 7)
        for bad in ("abc", "12.5", "12", "-1.00", "1.000", " 1.00", "1,00", 12.5, None):
            with self.subTest(total=bad):
                with self.assertRaises(ValueError):
                    total_to_cents(bad)
        start = datetime.date(2020, 12, 20)
        for offset in range(800):
            date = start + datetime.timedelta(days=offset)
            year, week, _ = date.isocalendar()
            self.assertEqual(iso_week(date.isoformat()), "%d-W%02d" % (year, week))

    def test_expected_rows_errors_and_kpis(self):
        invoices = [MOCK.INVOICES["INV-%d" % number] for number in range(1, 6)]
        rows, errors = validate_and_transform(invoices)
        self.assertEqual(rows, EXPECTED_ROWS)
        self.assertEqual([error["id"] for error in errors], ["INV-4"])
        self.assertTrue(errors[0]["reason"].startswith("invalid_total"))
        self.assertEqual(compute_kpis(rows), EXPECTED_KPIS)

    def test_avg_is_zero_without_paid_invoices(self):
        rows = [dict(EXPECTED_ROWS[2], iso_week="2026-W43")]
        self.assertEqual(compute_kpis(rows), [{
            "iso_week": "2026-W43", "revenue_paid_cents": 0, "invoices_paid": 0,
            "invoices_open": 1, "avg_paid_cents": 0}])

    def test_run_on_fresh_mock_produces_expected_log_and_state(self):
        server = RecordingState()
        rows, errors, kpis = run_workflow(server, load_workflow())
        self.assertEqual(rows, EXPECTED_ROWS)
        self.assertEqual([(e["id"], e["reason"].split(":")[0]) for e in errors],
                         [("INV-4", "invalid_total")])
        self.assertEqual(kpis, EXPECTED_KPIS)

        graphql = [(e["body"]["after"], e["status"]) for e in server.log
                   if e["path"] == "/graphql"]
        self.assertEqual(graphql, EXPECTED_GRAPHQL_LOG)
        invoice_log = [e for e in server.log if e["path"] == "/rest/v1/invoices"]
        self.assertEqual([(e["body"]["id"], e["status"]) for e in invoice_log],
                         [("INV-1", 201), ("INV-2", 201), ("INV-3", 201), ("INV-5", 201)])
        kpi_log = [e for e in server.log if e["path"] == "/rest/v1/kpi_weekly"]
        self.assertEqual([(e["body"]["iso_week"], e["status"]) for e in kpi_log],
                         [("2026-W41", 201), ("2026-W42", 201)])
        for entry in invoice_log + kpi_log:
            self.assertIn("resolution=merge-duplicates", entry["prefer"])
        self.assertEqual(len(server.log), 11)

        state = server.state.snapshot()
        self.assertEqual(state, {"invoices": EXPECTED_ROWS, "kpi_weekly": EXPECTED_KPIS})

        nodes = nodes_by_name(load_workflow())
        upsert_items(server, nodes["Upsert Invoice"], rows)
        upsert_items(server, nodes["Upsert KPI"], kpis)
        self.assertEqual(server.state.snapshot(), state)

    def test_whole_run_repeated_on_same_mock_is_idempotent(self):
        server = RecordingState()
        run_workflow(server, load_workflow())
        first = server.state.snapshot()
        before = len(server.log)
        run_workflow(server, load_workflow())
        self.assertEqual(server.state.snapshot(), first)
        graphql = [(e["body"]["after"], e["status"]) for e in server.log[before:]
                   if e["path"] == "/graphql"]
        self.assertEqual(graphql, [(None, 200), ("c2", 200), ("c3", 200)])


if __name__ == "__main__":
    unittest.main()
