from datetime import date
import importlib.util
import json
from pathlib import Path
import re
import unittest
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / "workflow.json"
SPEC = importlib.util.spec_from_file_location(
    "jobber_kpi_mock", ROOT / "mock" / "mock_jobber_supabase.py",
)
MOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOCK)
MANUAL_TRIGGER = "n8n-nodes-base.manualTrigger"
CODE = "n8n-nodes-base.code"
HTTP = "n8n-nodes-base.httpRequest"
NODE_TYPES = {
    "Manual Trigger": MANUAL_TRIGGER,
    "Fetch Invoices": HTTP,
    "Flatten": CODE,
    "Validate & Transform": CODE,
    "Build Error Report": CODE,
    "Upsert Invoice": HTTP,
    "Compute KPIs": CODE,
    "Upsert KPI": HTTP,
}
PREFER = {"Prefer": "resolution=merge-duplicates"}
TOTAL_PATTERN = r"^\d+\.\d{2}$"
MAX_SAFE_INTEGER = 9007199254740991
EXPECTED_INVOICES = [
    {"id": "INV-1", "client": "Acme", "status": "paid", "total_cents": 120000,
     "issued_at": "2026-10-05", "iso_week": "2026-W41"},
    {"id": "INV-2", "client": "Beta", "status": "paid", "total_cents": 45050,
     "issued_at": "2026-10-06", "iso_week": "2026-W41"},
    {"id": "INV-3", "client": "Acme", "status": "awaiting_payment", "total_cents": 30000,
     "issued_at": "2026-10-07", "iso_week": "2026-W41"},
    {"id": "INV-5", "client": "Beta", "status": "paid", "total_cents": 99999,
     "issued_at": "2026-10-13", "iso_week": "2026-W42"},
]
EXPECTED_KPIS = [
    {"iso_week": "2026-W41", "revenue_paid_cents": 165050, "invoices_paid": 2,
     "invoices_open": 1, "avg_paid_cents": 82525},
    {"iso_week": "2026-W42", "revenue_paid_cents": 99999, "invoices_paid": 1,
     "invoices_open": 0, "avg_paid_cents": 99999},
]


def fetch_pages(mock, query, restart_whole_node=False):
    """Model retries without sleeping; discard partial pages on node restart.

    Page retry models the requested four-call example. Whole-node retry models
    the supplied n8n source behavior and the workflow's actual retry settings.
    """
    for attempt in range(3):
        pages, cursor = [], None
        for page_number in range(10):
            body = {"query": query, "after": cursor}
            status, response = mock.handle("POST", "/graphql", body=body)
            if not restart_whole_node:
                for retry in range(2):
                    if status == 200:
                        break
                    status, response = mock.handle("POST", "/graphql", body=body)
            if status != 200:
                if restart_whole_node:
                    break
                raise ValueError("fetch_failed")
            pages.append(response)
            page_info = response["data"]["invoices"]["pageInfo"]
            if page_info["hasNextPage"] is False:
                return pages
            cursor = page_info["endCursor"]
        else:
            return pages
    raise ValueError("fetch_failed")


def flatten(pages):
    return [invoice for page in pages for invoice in page["data"]["invoices"]["nodes"]]


def validate(invoice):
    total = invoice.get("total")
    if not isinstance(total, str) or re.fullmatch(TOTAL_PATTERN, total, re.ASCII) is None:
        raise ValueError("invalid_total")
    whole, fraction = total.split(".")
    total_cents = int(whole) * 100 + int(fraction)
    if total_cents > MAX_SAFE_INTEGER:
        raise ValueError("invalid_total")
    iso_year, iso_week, _ = date.fromisoformat(invoice["issuedAt"]).isocalendar()
    return {
        "id": invoice["id"], "client": invoice["client"], "status": invoice["status"],
        "total_cents": total_cents, "issued_at": invoice["issuedAt"],
        "iso_week": f"{iso_year:04d}-W{iso_week:02d}",
    }


def transform(invoices):
    valid, errors = [], []
    for invoice in invoices:
        try:
            valid.append(validate(invoice))
        except ValueError as error:
            errors.append({"id": invoice["id"], "reason": str(error)})
    return valid, errors


def compute_kpis(invoices):
    weeks = {}
    for invoice in invoices:
        row = weeks.setdefault(invoice["iso_week"], {
            "iso_week": invoice["iso_week"], "revenue_paid_cents": 0,
            "invoices_paid": 0, "invoices_open": 0, "avg_paid_cents": 0,
        })
        if invoice["status"] == "paid":
            row["revenue_paid_cents"] += invoice["total_cents"]
            row["invoices_paid"] += 1
        else:
            row["invoices_open"] += 1
    for row in weeks.values():
        row["avg_paid_cents"] = (
            row["revenue_paid_cents"] // row["invoices_paid"]
            if row["invoices_paid"] else 0
        )
    return list(weeks.values())


class TestJobberKpiWorkflow(unittest.TestCase):
    # Inspect JavaScript and model its rules in Python; never run JS or n8n.
    def setUp(self):
        self.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.nodes = {node["name"]: node for node in self.workflow["nodes"]}

    def test_workflow_identity_inactive_and_top_level_keys(self):
        self.assertEqual(list(self.workflow), ["name", "id", "active", "nodes", "connections"])
        self.assertEqual(self.workflow["name"], "APC sim — Jobber KPI reporting")
        self.assertEqual(self.workflow["id"], "apcJobberKpi0001")
        self.assertIs(self.workflow["active"], False)
        self.assertNotIn("pinData", self.workflow)

    def test_node_types_versions_modes_ids_and_positions(self):
        nodes = self.workflow["nodes"]
        self.assertEqual(len(nodes), 8)
        self.assertEqual([node["name"] for node in nodes], list(NODE_TYPES))
        self.assertEqual(len({node["id"] for node in nodes}), len(nodes))
        self.assertEqual(len({tuple(node["position"]) for node in nodes}), len(nodes))
        for node in nodes:
            with self.subTest(node=node["name"]):
                self.assertEqual(node["type"], NODE_TYPES[node["name"]])
                versions = {MANUAL_TRIGGER: 1, CODE: 2, HTTP: 4.5}
                self.assertEqual(node["typeVersion"], versions[node["type"]])
                self.assertRegex(node["id"], r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
                self.assertEqual(len(node["position"]), 2)
                self.assertTrue(all(type(value) is int for value in node["position"]))
                if node["type"] == CODE:
                    expected = (
                        "runOnceForEachItem" if node["name"] == "Validate & Transform"
                        else "runOnceForAllItems"
                    )
                    self.assertEqual(node["parameters"]["mode"], expected)
                    self.assertEqual(node["parameters"]["language"], "javaScript")
                    self.assertIsInstance(node["parameters"]["jsCode"], str)

    def test_pagination_block_exactly_matches_verified_schema(self):
        parameters = self.nodes["Fetch Invoices"]["parameters"]
        self.assertEqual(parameters["options"], {"pagination": {"pagination": {
            "paginationMode": "updateAParameterInEachRequest",
            "parameters": {"parameters": [{
                "type": "body", "name": "after",
                "value": "={{ $response.body?.data?.invoices?.pageInfo?.endCursor ?? null }}",
            }]},
            "paginationCompleteWhen": "other",
            "completeExpression": "={{ $response.body?.data?.invoices?.pageInfo?.hasNextPage === false }}",
            "limitPagesFetched": True, "maxRequests": 10,
        }}})
        body = json.loads(parameters["jsonBody"])
        self.assertEqual(set(body), {"query", "after"})
        self.assertIsNone(body["after"])
        for field in ("query Invoices", "invoices(", "nodes", "id", "client", "status",
                      "total", "issuedAt", "pageInfo", "hasNextPage", "endCursor"):
            self.assertIn(field, body["query"])
        self.assertNotIn("neverError", json.dumps(parameters))

    def test_http_urls_post_json_and_no_credentials_anywhere(self):
        expected_urls = {
            "Fetch Invoices": "/graphql",
            "Upsert Invoice": "/rest/v1/invoices?on_conflict=id",
            "Upsert KPI": "/rest/v1/kpi_weekly?on_conflict=iso_week",
        }
        for name, suffix in expected_urls.items():
            parameters = self.nodes[name]["parameters"]
            self.assertEqual(parameters["url"], "http://127.0.0.1:5690" + suffix)
            self.assertEqual(parameters["method"], "POST")
            self.assertIs(parameters["sendBody"], True)
            self.assertEqual(parameters["contentType"], "json")
            self.assertEqual(parameters["specifyBody"], "json")
            self.assertIsInstance(parameters["jsonBody"], str)

        def check(value):
            if isinstance(value, dict):
                self.assertNotIn("credentials", value)
                for key, child in value.items():
                    if key == "url":
                        self.assertTrue(child.startswith("http://127.0.0.1:5690/"))
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(self.workflow)

    def test_retry_and_error_settings_are_at_node_level(self):
        for name in ("Fetch Invoices", "Upsert Invoice", "Upsert KPI"):
            node = self.nodes[name]
            self.assertIs(node["retryOnFail"], True)
            self.assertEqual(node["maxTries"], 3)
            self.assertEqual(node["waitBetweenTries"], 1000)
            for setting in ("retryOnFail", "maxTries", "waitBetweenTries", "onError"):
                self.assertNotIn(setting, node["parameters"])
            self.assertNotIn("onError", node)
        node = self.nodes["Validate & Transform"]
        self.assertEqual(node["onError"], "continueErrorOutput")
        self.assertNotIn("onError", node["parameters"])

    def test_upserts_have_exact_prefer_headers_and_row_projections(self):
        fields = {
            "Upsert Invoice": list(EXPECTED_INVOICES[0]),
            "Upsert KPI": list(EXPECTED_KPIS[0]),
        }
        for name, keys in fields.items():
            parameters = self.nodes[name]["parameters"]
            self.assertIs(parameters["sendHeaders"], True)
            self.assertEqual(parameters["specifyHeaders"], "keypair")
            self.assertEqual(parameters["headerParameters"], {"parameters": [
                {"name": "Prefer", "value": "resolution=merge-duplicates"},
            ]})
            expected = "={{ JSON.stringify({ " + ", ".join(
                key + ": $json." + key for key in keys
            ) + " }) }}"
            self.assertEqual(parameters["jsonBody"], expected)

    def test_exact_connections_including_dual_success_and_error_outputs(self):
        def edge(target):
            return {"node": target, "type": "main", "index": 0}
        self.assertEqual(self.workflow["connections"], {
            "Manual Trigger": {"main": [[edge("Fetch Invoices")]]},
            "Fetch Invoices": {"main": [[edge("Flatten")]]},
            "Flatten": {"main": [[edge("Validate & Transform")]]},
            "Validate & Transform": {"main": [
                [edge("Upsert Invoice"), edge("Compute KPIs")],
                [edge("Build Error Report")],
            ]},
            "Compute KPIs": {"main": [[edge("Upsert KPI")]]},
        })

    def test_code_expresses_flatten_validation_integer_math_and_iso_rules(self):
        flatten_code = self.nodes["Flatten"]["parameters"]["jsCode"]
        self.assertIn("$input.all().flatMap", flatten_code)
        self.assertIn("item.json.data.invoices.nodes.map", flatten_code)
        self.assertIn("pairedItem: { item: index }", flatten_code)
        code = self.nodes["Validate & Transform"]["parameters"]["jsCode"]
        for fragment in ("/" + TOTAL_PATTERN + "/", "typeof total === 'string'",
                         "match[0] !== total", "throw new Error('invalid_total')",
                         "BigInt(whole) * 100n + BigInt(fraction)", "Number(cents)",
                         "Number.MAX_SAFE_INTEGER", "T00:00:00Z", "date.getUTCDay() || 7",
                         "date.setUTCDate", "date.getUTCFullYear()", "Date.UTC(iso_year, 0, 1)",
                         "Math.ceil(((date - year_start) / 86400000 + 1) / 7)",
                         "'-W'", "padStart(2, '0')", "issued_at: invoice.issuedAt"):
            self.assertIn(fragment, code)
        self.assertNotIn("try", code)
        for node in self.workflow["nodes"]:
            if node["type"] == CODE:
                code = node["parameters"]["jsCode"]
                self.assertNotIn("parseFloat", code)
                self.assertNotIn("toFixed", code)

    def test_kpi_and_error_code_express_required_fields_and_rules(self):
        code = self.nodes["Compute KPIs"]["parameters"]["jsCode"]
        for fragment in ("$input.all()", "invoice.status === 'paid'",
                         "row.revenue_paid_cents += invoice.total_cents",
                         "row.invoices_paid += 1", "row.invoices_open += 1",
                         "row.invoices_paid === 0 ? 0",
                         "Math.floor(row.revenue_paid_cents / row.invoices_paid)"):
            self.assertIn(fragment, code)
        for field in EXPECTED_KPIS[0]:
            self.assertIn(field, code)
        report = self.nodes["Build Error Report"]["parameters"]["jsCode"]
        self.assertIn("json: { id, error }", report)
        self.assertIn("typeof error === 'string' ? error", report)
        self.assertIn("error?.message", report)
        self.assertNotIn("invalid_total", report)

    def apply_upserts(self, mock, invoices, kpis):
        for name, rows in (("Upsert Invoice", invoices), ("Upsert KPI", kpis)):
            parameters = self.nodes[name]["parameters"]
            url = urlsplit(parameters["url"])
            headers = {entry["name"]: entry["value"]
                       for entry in parameters["headerParameters"]["parameters"]}
            for row in rows:
                self.assertEqual(mock.handle("POST", url.path, parse_qs(url.query), headers, row),
                                 (201, row))

    def assert_pipeline(self, restart_whole_node):
        mock = MOCK.MockJobberSupabase()
        query = json.loads(self.nodes["Fetch Invoices"]["parameters"]["jsonBody"])["query"]
        pages = fetch_pages(mock, query, restart_whole_node)
        raw = flatten(pages)
        self.assertEqual([invoice["id"] for invoice in raw],
                         ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5"])
        invoices, errors = transform(raw)
        kpis = compute_kpis(invoices)
        self.assertEqual(errors, [{"id": "INV-4", "reason": "invalid_total"}])
        self.assertEqual(invoices, EXPECTED_INVOICES)
        self.assertEqual(kpis, EXPECTED_KPIS)
        self.apply_upserts(mock, invoices, kpis)
        cursors = (
            [None, "c2", None, "c2", "c3"] if restart_whole_node
            else [None, "c2", "c2", "c3"]
        )
        expected_log = [{
            "method": "POST", "path": "/graphql", "query": {}, "Prefer": "",
            "body": {"query": query, "after": cursor}, "status": 429 if index == 1 else 200,
        } for index, cursor in enumerate(cursors)]
        for table, key, rows in (("invoices", "id", EXPECTED_INVOICES),
                                 ("kpi_weekly", "iso_week", EXPECTED_KPIS)):
            expected_log.extend({
                "method": "POST", "path": "/rest/v1/" + table,
                "query": {"on_conflict": [key]}, "Prefer": PREFER["Prefer"],
                "body": row, "status": 201,
            } for row in rows)
        self.assertEqual(mock.request_log, expected_log)
        expected_state = {"invoices": EXPECTED_INVOICES, "kpi_weekly": EXPECTED_KPIS}
        self.assertEqual(mock.handle("GET", "/__state"), (200, expected_state))
        self.apply_upserts(mock, invoices, kpis)
        self.assertEqual(mock.handle("GET", "/__state"), (200, expected_state))
        for row in invoices + kpis:
            for field, value in row.items():
                if field.endswith("_cents") or field in {"invoices_paid", "invoices_open"}:
                    self.assertIs(type(value), int)

    def test_python_pipeline_requested_page_retry_log_and_idempotency(self):
        self.assert_pipeline(restart_whole_node=False)

    def test_python_pipeline_source_confirmed_whole_node_retry_and_idempotency(self):
        self.assert_pipeline(restart_whole_node=True)

    def test_invalid_totals_exact_decimal_strings_only(self):
        base = {"id": "A", "client": "Test", "status": "paid", "issuedAt": "2026-10-05"}
        for total in ("abc", "1", "1.0", "1.000", "-1.00", "+1.00", " 1.00",
                      "1.00 ", "1.00\n", "1e2", "NaN", "١.00", None, 1200, 1.00):
            with self.subTest(total=total):
                self.assertEqual(transform([{**base, "total": total}]),
                                 ([], [{"id": "A", "reason": "invalid_total"}]))
        for total, cents in (("0.00", 0), ("0.01", 1), ("0.99", 99), ("1.00", 100),
                             ("001.01", 101), ("90071992547409.91", MAX_SAFE_INTEGER)):
            with self.subTest(total=total):
                self.assertEqual(validate({**base, "total": total})["total_cents"], cents)
        with self.assertRaisesRegex(ValueError, "^invalid_total$"):
            validate({**base, "total": "90071992547409.92"})

    def test_iso_week_year_boundaries_and_padding(self):
        base = {"id": "A", "client": "Test", "status": "paid", "total": "1.00"}
        for issued_at, week in (("2021-01-01", "2020-W53"), ("2021-01-04", "2021-W01"),
                                ("2024-12-30", "2025-W01"), ("2026-10-05", "2026-W41"),
                                ("2026-10-13", "2026-W42")):
            with self.subTest(issued_at=issued_at):
                self.assertEqual(validate({**base, "issuedAt": issued_at})["iso_week"], week)

    def test_kpi_floor_zero_paid_and_all_other_statuses_open(self):
        invoices = [
            {"iso_week": "A", "status": "paid", "total_cents": 1},
            {"iso_week": "A", "status": "paid", "total_cents": 2},
            {"iso_week": "A", "status": "void", "total_cents": 999},
            {"iso_week": "B", "status": "awaiting_payment", "total_cents": 500},
            {"iso_week": "B", "status": "draft", "total_cents": 500},
        ]
        self.assertEqual(compute_kpis(invoices), [
            {"iso_week": "A", "revenue_paid_cents": 3, "invoices_paid": 2,
             "invoices_open": 1, "avg_paid_cents": 1},
            {"iso_week": "B", "revenue_paid_cents": 0, "invoices_paid": 0,
             "invoices_open": 2, "avg_paid_cents": 0},
        ])
        self.assertEqual(flatten([]), [])
        self.assertEqual(transform([]), ([], []))
        self.assertEqual(compute_kpis([]), [])

    def test_documentation_limit_and_retry_discrepancy_are_explicit(self):
        document = (ROOT / "NOTES.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(document.splitlines()), 60)
        for text in ("whole Fetch Invoices node", "(5 calls)", "4-call log", "Jobber OAuth",
                     "service key held in n8n credentials", "Schedule trigger"):
            self.assertIn(text, document)


if __name__ == "__main__":
    unittest.main()
