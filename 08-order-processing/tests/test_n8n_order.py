import json
import re
import unittest
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / "workflow.json"
)
MANUAL_TRIGGER = "n8n-nodes-base.manualTrigger"
CODE = "n8n-nodes-base.code"
ALLOWED_TYPES = {MANUAL_TRIGGER, CODE}
CHAIN = ["Manual Trigger", "Sample Orders", "Validate Order", "Build Outputs"]
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SAMPLE_A = {
    "order_id": "A-1001",
    "created_at": "2026-10-09T10:00:00Z",
    "customer_name": "Ona Test",
    "customer_email": "ona@example.com",
    "items": [
        {"sku": "MUG", "qty": 2, "unit_price_cents": 1250},
        {"sku": "PEN", "qty": 1, "unit_price_cents": 500},
    ],
}
SAMPLE_B = {
    "order_id": "B-1002",
    "created_at": "2026-10-09T11:00:00Z",
    "customer_name": "Bo Test",
    "customer_email": "",
    "items": [{"sku": "MUG", "qty": 0, "unit_price_cents": 1250}],
}


def validate(order):
    reasons = []
    for field in ("order_id", "created_at", "customer_name", "customer_email"):
        value = order.get(field)
        if not isinstance(value, str) or not value.strip():
            reasons.append("missing " + field)
    email = order.get("customer_email")
    if not isinstance(email, str) or EMAIL_PATTERN.search(email) is None:
        reasons.append("invalid customer_email")
    items = order.get("items")
    if not isinstance(items, list) or not items:
        reasons.append("missing items")
    else:
        for i, item in enumerate(items):
            line = item if isinstance(item, dict) else {}
            qty = line.get("qty")
            price = line.get("unit_price_cents")
            if type(qty) is not int or qty <= 0:
                reasons.append(f"items[{i}].qty must be a positive integer")
            if type(price) is not int or price < 0:
                reasons.append(
                    f"items[{i}].unit_price_cents must be a non-negative integer"
                )
    return not reasons, reasons


def build_outputs(order):
    valid, reasons = validate(order)
    if not valid:
        return {"error": {"order_id": order.get("order_id"), "reasons": reasons}}
    items_count = len(order["items"])
    total_cents = sum(item["qty"] * item["unit_price_cents"] for item in order["items"])
    total_eur = str((total_cents - total_cents % 100) // 100) + "." + str(
        total_cents % 100
    ).zfill(2)
    return {
        "sheet_row": {
            "order_id": order["order_id"],
            "created_at": order["created_at"],
            "customer_name": order["customer_name"],
            "customer_email": order["customer_email"],
            "items_count": items_count,
            "total_cents": total_cents,
            "total_eur": total_eur,
        },
        "notification": {
            "to": "ops@example.com",
            "subject": "New order " + order["order_id"],
            "body": (
                f"Order {order['order_id']} from {order['customer_name']}: "
                f"{items_count} line items, total {total_eur} EUR."
            ),
        },
    }


class TestOrderProcessingWorkflow(unittest.TestCase):
    def setUp(self):
        self.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_json_parses_and_top_level_keys(self):
        self.assertIsInstance(self.workflow, dict)
        self.assertEqual(
            list(self.workflow), ["name", "id", "active", "nodes", "connections"]
        )

    def test_workflow_name_and_id(self):
        self.assertEqual(self.workflow["name"], "APC sim — order processing")
        self.assertEqual(self.workflow["id"], "apcOrderProc0001")
        self.assertRegex(self.workflow["id"], r"\A[A-Za-z0-9]{16}\Z")

    def test_workflow_is_inactive(self):
        self.assertIs(self.workflow["active"], False)

    def test_exactly_four_nodes_with_expected_types(self):
        nodes = self.workflow["nodes"]
        self.assertEqual(len(nodes), 4)
        self.assertEqual([node["name"] for node in nodes], CHAIN)
        self.assertEqual({node["type"] for node in nodes}, ALLOWED_TYPES)
        self.assertEqual(sum(node["type"] == MANUAL_TRIGGER for node in nodes), 1)
        for i, node in enumerate(nodes):
            with self.subTest(node=node["name"]):
                self.assertEqual(node["type"], MANUAL_TRIGGER if i == 0 else CODE)
                self.assertEqual(node["typeVersion"], 1 if i == 0 else 2)
                if i == 0:
                    self.assertEqual(node["parameters"], {})
                else:
                    self.assertEqual(set(node["parameters"]), {"language", "jsCode"})
                    self.assertEqual(node["parameters"]["language"], "javaScript")
                    self.assertIsInstance(node["parameters"]["jsCode"], str)
                    self.assertIn("$input.all()", node["parameters"]["jsCode"])

    def test_connections_are_exactly_the_expected_chain(self):
        expected = {
            source: {"main": [[{"node": target, "type": "main", "index": 0}]]}
            for source, target in zip(CHAIN, CHAIN[1:])
        }
        self.assertEqual(self.workflow["connections"], expected)

    def test_nodes_have_no_credentials_and_workflow_has_no_pin_data(self):
        self.assertNotIn("pinData", self.workflow)
        for node in self.workflow["nodes"]:
            with self.subTest(node=node["name"]):
                self.assertNotIn("credentials", node)

    def test_code_contains_mock_values_and_avoids_float_formatting(self):
        code_by_name = {
            node["name"]: node["parameters"]["jsCode"]
            for node in self.workflow["nodes"]
            if node["type"] == CODE
        }
        for value in ("A-1001", "B-1002", "ona@example.com"):
            self.assertIn(value, code_by_name["Sample Orders"])
        self.assertIn("ops@example.com", code_by_name["Build Outputs"])
        self.assertIn("Number.isInteger", code_by_name["Validate Order"])
        for name, js_code in code_by_name.items():
            with self.subTest(node=name):
                self.assertNotIn("toFixed", js_code)
                self.assertNotIn("parseFloat", js_code)

    def test_sample_a_is_valid_and_builds_expected_outputs(self):
        valid, reasons = validate(SAMPLE_A)
        self.assertIs(valid, True)
        self.assertEqual(reasons, [])
        output = build_outputs(SAMPLE_A)
        self.assertEqual(set(output), {"sheet_row", "notification"})
        self.assertEqual(
            output["sheet_row"],
            {
                "order_id": "A-1001",
                "created_at": "2026-10-09T10:00:00Z",
                "customer_name": "Ona Test",
                "customer_email": "ona@example.com",
                "items_count": 2,
                "total_cents": 3000,
                "total_eur": "30.00",
            },
        )
        self.assertIs(type(output["sheet_row"]["total_cents"]), int)
        self.assertEqual(output["notification"]["to"], "ops@example.com")
        self.assertEqual(output["notification"]["subject"], "New order A-1001")
        self.assertIn("30.00 EUR", output["notification"]["body"])

    def test_sample_b_is_invalid_and_only_builds_an_error(self):
        valid, reasons = validate(SAMPLE_B)
        self.assertIs(valid, False)
        self.assertIn("missing customer_email", reasons)
        self.assertIn("invalid customer_email", reasons)
        self.assertIn("items[0].qty must be a positive integer", reasons)
        self.assertEqual(
            build_outputs(SAMPLE_B),
            {"error": {"order_id": "B-1002", "reasons": reasons}},
        )

    def test_required_fields_and_items(self):
        for field in ("order_id", "created_at", "customer_name", "customer_email"):
            for value in (None, "", " ", 123):
                with self.subTest(field=field, value=value):
                    valid, reasons = validate({**SAMPLE_A, field: value})
                    self.assertIs(valid, False)
                    self.assertIn("missing " + field, reasons)
            order = {key: value for key, value in SAMPLE_A.items() if key != field}
            self.assertIn("missing " + field, validate(order)[1])
        for items in (None, [], {}, "items"):
            with self.subTest(items=items):
                valid, reasons = validate({**SAMPLE_A, "items": items})
                self.assertIs(valid, False)
                self.assertIn("missing items", reasons)
        self.assertIn("missing items", validate({})[1])

    def test_invalid_email_pattern(self):
        for email in ("ona", "ona@example", "ona @example.com", "ona@@example.com"):
            with self.subTest(email=email):
                valid, reasons = validate({**SAMPLE_A, "customer_email": email})
                self.assertIs(valid, False)
                self.assertEqual(reasons, ["invalid customer_email"])

    def test_line_items_require_integer_quantity_and_price(self):
        for field, values, reason in (
            ("qty", (0, -1, True, "2", None), "must be a positive integer"),
            (
                "unit_price_cents",
                (-1, True, "1250", None),
                "must be a non-negative integer",
            ),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    line = {**SAMPLE_A["items"][0], field: value}
                    valid, reasons = validate({**SAMPLE_A, "items": [line]})
                    self.assertIs(valid, False)
                    self.assertIn(f"items[0].{field} {reason}", reasons)
        valid, reasons = validate({**SAMPLE_A, "items": [None]})
        self.assertIs(valid, False)
        self.assertEqual(len(reasons), 2)

    def test_integer_cents_formatting_boundaries(self):
        for cents, euros in (
            (0, "0.00"), (1, "0.01"), (99, "0.99"), (100, "1.00"), (101, "1.01")
        ):
            with self.subTest(cents=cents):
                order = {
                    **SAMPLE_A,
                    "items": [{"sku": "MUG", "qty": 1, "unit_price_cents": cents}],
                }
                self.assertEqual(validate(order), (True, []))
                output = build_outputs(order)
                self.assertIs(type(output["sheet_row"]["total_cents"]), int)
                self.assertEqual(output["sheet_row"]["total_eur"], euros)


if __name__ == "__main__":
    unittest.main()
