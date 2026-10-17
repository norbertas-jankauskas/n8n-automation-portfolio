import json
import re
import unittest
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / "workflow.json"
)
MANUAL_TRIGGER = "n8n-nodes-base.manualTrigger"
CODE = "n8n-nodes-base.code"
IF = "n8n-nodes-base.if"
ALLOWED_TYPES = {MANUAL_TRIGGER, CODE, IF}
NODE_TYPES = {
    "Manual Trigger": MANUAL_TRIGGER,
    "Sample Emails": CODE,
    "Extract Job [AI STUB]": CODE,
    "Is Complete?": IF,
    "Price Estimate": CODE,
    "Build GHL Proposal": CODE,
    "Build Review Task": CODE,
}
JOB_FIELDS = ("walls_sqft", "ceiling_sqft", "board", "finish_level")
PRICE_FIELDS = (
    "area_sqft", "sheets", "board_cents_per_sheet", "labor_cents_per_sqft",
    "materials_cents", "labor_cents", "ceiling_surcharge_cents",
    "subtotal_cents", "margin_cents", "total_cents",
)
EMAILS = [
    {
        "id": "E1",
        "from": "dana@example.com",
        "subject": "Quote request: basement finish",
        "body": (
            "Hi, walls 1200 sqft, ceiling 400 sqft, standard board, "
            "level 4 finish. Thanks."
        ),
    },
    {
        "id": "E2",
        "from": "lee@example.com",
        "subject": "Bathroom drywall",
        "body": (
            "Need walls 300 sqft, ceiling 80 sqft, moisture resistant board, level 5."
        ),
    },
    {
        "id": "E3",
        "from": "sam@example.com",
        "subject": "Garage",
        "body": "Can you quote my garage? level 4",
    },
]


def extract(email):
    body = email["body"]
    walls = re.search(r"walls\s+(\d+)\s*sq\s*ft", body, re.IGNORECASE)
    ceiling = re.search(r"ceiling\s+(\d+)\s*sq\s*ft", body, re.IGNORECASE)
    finish = re.search(r"level\s+([345])\b", body, re.IGNORECASE)
    board = None
    if re.search(r"standard board", body, re.IGNORECASE):
        board = "standard"
    elif re.search(r"moisture[- ]resistant board", body, re.IGNORECASE):
        board = "moisture"
    job = {
        "id": email["id"],
        "from": email["from"],
        "subject": email["subject"],
        "walls_sqft": int(walls.group(1), 10) if walls else None,
        "ceiling_sqft": int(ceiling.group(1), 10) if ceiling else None,
        "board": board,
        "finish_level": int(finish.group(1), 10) if finish else None,
    }
    missing = [field for field in JOB_FIELDS if job[field] is None]
    return {**job, "missing": missing, "complete": not missing}


def price(job):
    area_sqft = job["walls_sqft"] + job["ceiling_sqft"]
    sheets = (area_sqft * 110 + 3199) // 3200
    board_cents_per_sheet = 1500 if job["board"] == "standard" else 2200
    labor_cents_per_sqft = {3: 150, 4: 180, 5: 230}[job["finish_level"]]
    materials_cents = sheets * board_cents_per_sheet
    labor_cents = area_sqft * labor_cents_per_sqft
    ceiling_surcharge_cents = 40 * job["ceiling_sqft"]
    subtotal_cents = materials_cents + labor_cents + ceiling_surcharge_cents
    margin_cents = (subtotal_cents * 20 + 50) // 100
    return {
        **job,
        "area_sqft": area_sqft,
        "sheets": sheets,
        "board_cents_per_sheet": board_cents_per_sheet,
        "labor_cents_per_sqft": labor_cents_per_sqft,
        "materials_cents": materials_cents,
        "labor_cents": labor_cents,
        "ceiling_surcharge_cents": ceiling_surcharge_cents,
        "subtotal_cents": subtotal_cents,
        "margin_cents": margin_cents,
        "total_cents": subtotal_cents + margin_cents,
    }


def build_proposal(job):
    c = job["total_cents"]
    usd = str((c - c % 100) // 100) + "." + str(c % 100).zfill(2)
    return {
        "ghl_proposal": {
            "contact": {"email": job["from"]},
            "estimate": {
                "name": "Drywall estimate " + job["id"] + " — " + job["subject"],
                "currency": "USD",
                "line_items": [
                    {
                        "name": "Drywall board (" + job["board"] + ")",
                        "qty": job["sheets"],
                        "unit_price_cents": job["board_cents_per_sheet"],
                    },
                    {
                        "name": f"Labor, level {job['finish_level']} finish (sqft)",
                        "qty": job["area_sqft"],
                        "unit_price_cents": job["labor_cents_per_sqft"],
                    },
                    {
                        "name": "Ceiling surcharge (sqft)",
                        "qty": job["ceiling_sqft"],
                        "unit_price_cents": 40,
                    },
                    {
                        "name": "Overhead and margin (20%)",
                        "qty": 1,
                        "unit_price_cents": job["margin_cents"],
                    },
                ],
                "total_cents": c,
                "total_usd": usd,
            },
        },
    }


def build_review(job):
    return {
        "review_task": {
            "email_id": job["id"],
            "from": job["from"],
            "subject": job["subject"],
            "missing": job["missing"],
            "title": "Review needed: " + job["subject"],
            "note": (
                "Missing " + ", ".join(job["missing"])
                + "; ask the customer before pricing."
            ),
        },
    }


def route(emails):
    proposals, reviews = [], []
    for email in emails:
        job = extract(email)
        if job["complete"]:
            proposals.append(build_proposal(price(job)))
        else:
            reviews.append(build_review(job))
    return proposals, reviews


class TestDrywallEstimateWorkflow(unittest.TestCase):
    # These tests inspect JS text and model the rules in Python; they do not run JS.
    def setUp(self):
        self.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_json_parses_and_top_level_keys(self):
        self.assertIsInstance(self.workflow, dict)
        self.assertEqual(
            list(self.workflow), ["name", "id", "active", "nodes", "connections"]
        )
        self.assertEqual(self.workflow["name"], "APC sim — drywall estimate")
        self.assertEqual(self.workflow["id"], "apcDrywallEst001")
        self.assertRegex(self.workflow["id"], r"\A[A-Za-z0-9]{16}\Z")
        self.assertIs(self.workflow["active"], False)

    def test_exactly_seven_nodes_with_expected_types_and_unique_ids(self):
        nodes = self.workflow["nodes"]
        self.assertEqual(len(nodes), 7)
        self.assertEqual([node["name"] for node in nodes], list(NODE_TYPES))
        self.assertEqual({node["type"] for node in nodes}, ALLOWED_TYPES)
        self.assertEqual(len({node["id"] for node in nodes}), len(nodes))
        for node in nodes:
            with self.subTest(node=node["name"]):
                self.assertRegex(node["id"], r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
                self.assertEqual(node["type"], NODE_TYPES[node["name"]])
                expected_version = {MANUAL_TRIGGER: 1, CODE: 2, IF: 2.3}
                self.assertEqual(node["typeVersion"], expected_version[node["type"]])
                if node["type"] == MANUAL_TRIGGER:
                    self.assertEqual(node["parameters"], {})
                elif node["type"] == CODE:
                    self.assertEqual(set(node["parameters"]), {"language", "jsCode"})
                    self.assertEqual(node["parameters"]["language"], "javaScript")
                    self.assertIn("$input.all()", node["parameters"]["jsCode"])
                    self.assertEqual(node["parameters"].get("mode", "runOnceForAllItems"),
                                     "runOnceForAllItems")

    def test_if_parameters_are_exact(self):
        node = next(n for n in self.workflow["nodes"] if n["name"] == "Is Complete?")
        self.assertEqual(node["typeVersion"], 2.3)
        self.assertEqual(node["parameters"], {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "",
                            "typeValidation": "strict", "version": 3},
                "conditions": [{
                    "id": "complete-is-true",
                    "leftValue": "={{ $json.complete }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "true",
                                 "singleValue": True},
                }],
                "combinator": "and",
            },
            "options": {},
        })

    def test_connections_are_exact_including_true_and_false_outputs(self):
        def edge(target):
            return [{"node": target, "type": "main", "index": 0}]

        self.assertEqual(self.workflow["connections"], {
            "Manual Trigger": {"main": [edge("Sample Emails")]},
            "Sample Emails": {"main": [edge("Extract Job [AI STUB]")]},
            "Extract Job [AI STUB]": {"main": [edge("Is Complete?")]},
            "Is Complete?": {"main": [edge("Price Estimate"), edge("Build Review Task")]},
            "Price Estimate": {"main": [edge("Build GHL Proposal")]},
        })

    def test_no_credentials_anywhere_or_pin_data(self):
        self.assertNotIn("pinData", self.workflow)

        def check(value):
            if isinstance(value, dict):
                self.assertNotIn("credentials", value)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(self.workflow)

    def test_code_contains_sample_values_rules_and_mock_labels(self):
        code = {
            node["name"]: node["parameters"]["jsCode"]
            for node in self.workflow["nodes"] if node["type"] == CODE
        }
        for email in EMAILS:
            for value in email.values():
                self.assertIn(value, code["Sample Emails"])
        self.assertIn("MOCK email inbox", code["Sample Emails"])
        self.assertIn("STAND-IN", code["Extract Job [AI STUB]"])
        self.assertIn("Claude", code["Extract Job [AI STUB]"])
        for pattern in (
            r"/walls\s+(\d+)\s*sq\s*ft/i",
            r"/ceiling\s+(\d+)\s*sq\s*ft/i",
            r"/level\s+([345])\b/i",
            r"/standard board/i", r"/moisture[- ]resistant board/i",
        ):
            self.assertIn(pattern, code["Extract Job [AI STUB]"])
        self.assertIn("parseInt(walls[1], 10)", code["Extract Job [AI STUB]"])
        self.assertIn("parseInt(ceiling[1], 10)", code["Extract Job [AI STUB]"])
        self.assertIn("parseInt(finish[1], 10)", code["Extract Job [AI STUB]"])
        for number in (3200, 110, 1500, 2200, 150, 180, 230, 40):
            self.assertRegex(code["Price Estimate"], rf"\b{number}\b")
        self.assertIn("Math.floor((area_sqft * 110 + 3199) / 3200)", code["Price Estimate"])
        self.assertIn("Math.floor((subtotal_cents * 20 + 50) / 100)", code["Price Estimate"])
        self.assertIn("MOCK GoHighLevel payload shape", code["Build GHL Proposal"])
        self.assertIn("USD", code["Build GHL Proposal"])
        self.assertRegex(code["Build GHL Proposal"], r"\b40\b")
        for name, js_code in code.items():
            with self.subTest(node=name):
                for forbidden in ("toFixed", "parseFloat", "Math.round", "Math.ceil"):
                    self.assertNotIn(forbidden, js_code)

    def test_complete_jobs_have_expected_extraction_and_integer_prices(self):
        for email, fields, expected in (
            (EMAILS[0], (1200, 400, "standard", 4),
             (1600, 55, 1500, 180, 82500, 288000, 16000, 386500, 77300, 463800)),
            (EMAILS[1], (300, 80, "moisture", 5),
             (380, 14, 2200, 230, 30800, 87400, 3200, 121400, 24280, 145680)),
        ):
            with self.subTest(email=email["id"]):
                job = extract(email)
                self.assertEqual(job, {
                    **{field: email[field] for field in ("id", "from", "subject")},
                    **dict(zip(JOB_FIELDS, fields)), "missing": [], "complete": True,
                })
                self.assertIs(job["complete"], True)
                priced = price(job)
                self.assertEqual({field: priced[field] for field in job}, job)
                self.assertEqual(tuple(priced[field] for field in PRICE_FIELDS), expected)
                for field in ("walls_sqft", "ceiling_sqft", "finish_level") + PRICE_FIELDS:
                    self.assertIs(type(priced[field]), int)

    def test_proposal_shapes_currency_integer_lines_and_totals(self):
        proposals, reviews = route(EMAILS)
        self.assertEqual(len(proposals), 2)
        self.assertEqual(len(reviews), 1)
        for email, proposal, total, usd in zip(
            EMAILS, proposals, (463800, 145680), ("4638.00", "1456.80")
        ):
            with self.subTest(email=email["id"]):
                self.assertEqual(set(proposal), {"ghl_proposal"})
                payload = proposal["ghl_proposal"]
                self.assertEqual(set(payload), {"contact", "estimate"})
                self.assertEqual(payload["contact"], {"email": email["from"]})
                self.assertTrue(payload["contact"]["email"].endswith("@example.com"))
                estimate = payload["estimate"]
                self.assertEqual(set(estimate), {
                    "name", "currency", "line_items", "total_cents", "total_usd"
                })
                self.assertEqual(estimate["name"],
                                 "Drywall estimate " + email["id"] + " — " + email["subject"])
                self.assertEqual(estimate["currency"], "USD")
                self.assertIs(type(estimate["total_cents"]), int)
                self.assertEqual(estimate["total_cents"], total)
                self.assertEqual(estimate["total_usd"], usd)
                lines = estimate["line_items"]
                self.assertEqual(len(lines), 4)
                job = price(extract(email))
                self.assertEqual([line["name"] for line in lines], [
                    "Drywall board (" + job["board"] + ")",
                    f"Labor, level {job['finish_level']} finish (sqft)",
                    "Ceiling surcharge (sqft)", "Overhead and margin (20%)",
                ])
                self.assertEqual([line["qty"] for line in lines],
                                 [job["sheets"], job["area_sqft"], job["ceiling_sqft"], 1])
                self.assertEqual([line["unit_price_cents"] for line in lines],
                                 [job["board_cents_per_sheet"], job["labor_cents_per_sqft"],
                                  40, job["margin_cents"]])
                for line in lines:
                    self.assertEqual(set(line), {"name", "qty", "unit_price_cents"})
                    self.assertIs(type(line["qty"]), int)
                    self.assertIs(type(line["unit_price_cents"]), int)
                self.assertEqual(sum(line["qty"] * line["unit_price_cents"] for line in lines),
                                 total)

    def test_garage_goes_only_to_review_with_exact_missing_fields(self):
        job = extract(EMAILS[2])
        self.assertIs(job["complete"], False)
        self.assertEqual(job["finish_level"], 4)
        self.assertIs(type(job["finish_level"]), int)
        self.assertEqual(job["missing"], ["walls_sqft", "ceiling_sqft", "board"])
        for field in job["missing"]:
            self.assertIsNone(job[field])
        expected = {"review_task": {
            "email_id": "E3", "from": "sam@example.com", "subject": "Garage",
            "missing": ["walls_sqft", "ceiling_sqft", "board"],
            "title": "Review needed: Garage",
            "note": "Missing walls_sqft, ceiling_sqft, board; ask the customer before pricing.",
        }}
        self.assertEqual(build_review(job), expected)
        self.assertEqual(route([EMAILS[2]]), ([], [expected]))
        self.assertEqual(route(EMAILS)[1], [expected])

    def test_extraction_case_spacing_hyphen_and_missing_finish(self):
        job = extract({**EMAILS[0], "body":
                       "WALLS 012 SQ FT, CEILING 0 SQFT, MOISTURE-RESISTANT BOARD, LEVEL 3"})
        self.assertEqual([job[field] for field in JOB_FIELDS], [12, 0, "moisture", 3])
        self.assertIs(job["complete"], True)
        for body in ("", "walls 1 sqft ceiling 2 sqft standard board level 6",
                     "walls 1 sqft ceiling 2 sqft standard board level 40"):
            with self.subTest(body=body):
                job = extract({**EMAILS[0], "body": body})
                self.assertIsNone(job["finish_level"])
                self.assertIs(job["complete"], False)
                self.assertEqual(job["missing"], list(JOB_FIELDS) if not body else ["finish_level"])

    def test_sheet_boundaries_and_level_three_rate(self):
        for area, sheets in ((0, 0), (1, 1), (29, 1), (30, 2), (320, 11), (321, 12)):
            with self.subTest(area=area):
                job = price({**extract(EMAILS[0]), "walls_sqft": area,
                             "ceiling_sqft": 0, "finish_level": 3})
                self.assertEqual(job["sheets"], sheets)
                self.assertEqual(job["labor_cents_per_sqft"], 150)
                self.assertEqual(job["labor_cents"], area * 150)

    def test_integer_cents_formatting_boundaries(self):
        job = price(extract(EMAILS[0]))
        for cents, usd in ((0, "0.00"), (1, "0.01"), (99, "0.99"),
                           (100, "1.00"), (101, "1.01")):
            with self.subTest(cents=cents):
                estimate = build_proposal({**job, "total_cents": cents})["ghl_proposal"]["estimate"]
                self.assertIs(type(estimate["total_cents"]), int)
                self.assertEqual(estimate["total_usd"], usd)


if __name__ == "__main__":
    unittest.main()
