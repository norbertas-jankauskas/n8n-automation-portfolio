import json
import re
import unittest
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / "workflow.json"
)
MANUAL_TRIGGER = "n8n-nodes-base.manualTrigger"
CODE = "n8n-nodes-base.code"
SWITCH = "n8n-nodes-base.switch"
ALLOWED_TYPES = {MANUAL_TRIGGER, CODE, SWITCH}
NODE_TYPES = {
    "Manual Trigger": MANUAL_TRIGGER,
    "Raw Leads": CODE,
    "Parse Payload": CODE,
    "Build Error Report": CODE,
    "Validate & Dedupe": CODE,
    "Score": CODE,
    "Route by Tier": SWITCH,
    "Collect Hot": CODE,
    "Collect Warm": CODE,
    "Collect Cold": CODE,
    "Collect Rejected": CODE,
}
TIERS = ("hot", "warm", "cold", "rejected")
EMAIL_REGEX = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
RAW_LEADS = [
    {
        "lead_id": "L1",
        "raw": '{"name":"Ana","email":"ANA@Example.com","budget":5000,"source":"web"}',
    },
    {
        "lead_id": "L2",
        "raw": '{"name":"Ben","email":"ben@example.com","budget":800,"source":"ads"}',
    },
    {
        "lead_id": "L3",
        "raw": '{"name":"Ana K","email":"ana@example.com","budget":6000,"source":"referral"}',
    },
    {
        "lead_id": "L4",
        "raw": '{"name":"Cid","email":"cid@example","budget":3000,"source":"web"}',
    },
    {"lead_id": "L5", "raw": "not json"},
    {
        "lead_id": "L6",
        "raw": '{"name":"Dee","email":"dee@example.com","budget":2500,"source":"referral"}',
    },
]


def parse_payloads(raw_leads):
    parsed, errors = [], []
    for lead in raw_leads:
        try:
            payload = json.loads(lead["raw"])
        except json.JSONDecodeError as error:
            errors.append({
                "lead_id": lead["lead_id"], "stage": "parse",
                "reason": "invalid_json", "error": str(error), "raw": lead["raw"],
            })
        else:
            parsed.append({"lead_id": lead["lead_id"], **payload})
    return parsed, errors


def validate_and_dedupe(leads):
    seen, results = set(), []
    for lead in leads:
        email = str(lead["email"]).strip().lower()
        reason = None
        if not re.search(EMAIL_REGEX, email):
            reason = "invalid_email"
        elif email in seen:
            reason = "duplicate"
        else:
            seen.add(email)
        result = {
            field: lead[field]
            for field in ("lead_id", "name", "budget", "source")
        }
        result.update(email=email, valid=reason is None, reason=reason)
        if not result["valid"]:
            result["tier"] = "rejected"
        results.append(result)
    return results


def score(leads):
    results = []
    for lead in leads:
        if lead.get("tier") == "rejected":
            results.append({**lead, "score": 0})
            continue
        budget_points = (
            50 if lead["budget"] >= 5000 else 30 if lead["budget"] >= 2000 else 10
        )
        source_points = {"referral": 30, "web": 10, "ads": 0}.get(lead["source"], 0)
        total = budget_points + source_points
        tier = "hot" if total >= 60 else "warm" if total >= 30 else "cold"
        results.append({
            **lead, "budget_points": budget_points, "source_points": source_points,
            "score": total, "tier": tier,
        })
    return results


def route(leads):
    routed = {tier: [] for tier in TIERS}
    for lead in leads:
        routed[lead["tier"]].append(lead)
    return routed


def collect(tier, leads):
    fields = ("lead_id", "email", "reason" if tier == "rejected" else "score")
    return {
        "tier": tier, "count": len(leads),
        "leads": [{field: lead[field] for field in fields} for lead in leads],
    }


class TestLeadRouterWorkflow(unittest.TestCase):
    # Inspect JS text and model the rules in Python; never execute JS or n8n.
    def setUp(self):
        self.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.nodes = {node["name"]: node for node in self.workflow["nodes"]}

    def test_json_parses_and_top_level_keys(self):
        self.assertIsInstance(self.workflow, dict)
        self.assertEqual(
            list(self.workflow), ["name", "id", "active", "nodes", "connections"]
        )
        self.assertEqual(self.workflow["name"], "APC sim — lead router")
        self.assertEqual(self.workflow["id"], "apcLeadRouter001")
        self.assertIs(self.workflow["active"], False)

    def test_exactly_eleven_nodes_with_expected_types_and_unique_ids(self):
        nodes = self.workflow["nodes"]
        self.assertEqual(len(nodes), 11)
        self.assertEqual([node["name"] for node in nodes], list(NODE_TYPES))
        self.assertEqual({node["type"] for node in nodes}, ALLOWED_TYPES)
        self.assertEqual(len({node["id"] for node in nodes}), len(nodes))
        for node in nodes:
            with self.subTest(node=node["name"]):
                self.assertRegex(node["id"], r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
                self.assertEqual(node["type"], NODE_TYPES[node["name"]])
                versions = {MANUAL_TRIGGER: 1, CODE: 2, SWITCH: 3.4}
                self.assertEqual(node["typeVersion"], versions[node["type"]])
                if node["type"] == MANUAL_TRIGGER:
                    self.assertEqual(node["parameters"], {})
                elif node["type"] == CODE:
                    parameters = node["parameters"]
                    self.assertEqual(set(parameters), {"mode", "language", "jsCode"})
                    self.assertEqual(parameters["language"], "javaScript")
                    self.assertIsInstance(parameters["jsCode"], str)
                    expected_mode = (
                        "runOnceForEachItem" if node["name"] == "Parse Payload"
                        else "runOnceForAllItems"
                    )
                    self.assertEqual(parameters["mode"], expected_mode)

    def test_parse_payload_uses_error_output_without_try(self):
        node = self.nodes["Parse Payload"]
        self.assertEqual(node["onError"], "continueErrorOutput")
        self.assertNotIn("onError", node["parameters"])
        self.assertEqual(node["parameters"]["mode"], "runOnceForEachItem")
        code = node["parameters"]["jsCode"]
        self.assertIn("const parsed = JSON.parse($json.raw);", code)
        self.assertIn("return { json: { lead_id: $json.lead_id, ...parsed } };", code)
        self.assertNotIn("try", code)

    def test_switch_parameters_are_exact(self):
        node = self.nodes["Route by Tier"]
        self.assertEqual(node["typeVersion"], 3.4)
        expected_rules = [{
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "",
                            "typeValidation": "strict", "version": 3},
                "conditions": [{
                    "id": "tier-is-" + tier,
                    "leftValue": "={{ $json.tier }}", "rightValue": tier,
                    "operator": {"type": "string", "operation": "equals"},
                }],
                "combinator": "and",
            },
            "renameOutput": True, "outputKey": tier,
        } for tier in TIERS]
        self.assertEqual(node["parameters"], {
            "rules": {"values": expected_rules}, "options": {},
        })

    def test_connections_are_exact_including_error_and_tier_outputs(self):
        def edge(target):
            return [{"node": target, "type": "main", "index": 0}]

        self.assertEqual(self.workflow["connections"], {
            "Manual Trigger": {"main": [edge("Raw Leads")]},
            "Raw Leads": {"main": [edge("Parse Payload")]},
            "Parse Payload": {
                "main": [edge("Validate & Dedupe"), edge("Build Error Report")],
            },
            "Validate & Dedupe": {"main": [edge("Score")]},
            "Score": {"main": [edge("Route by Tier")]},
            "Route by Tier": {
                "main": [edge("Collect " + tier.title()) for tier in TIERS],
            },
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

    def test_code_contains_exact_mock_payloads_and_email_regex(self):
        raw_code = self.nodes["Raw Leads"]["parameters"]["jsCode"]
        self.assertIn("MOCK lead source", raw_code)
        literals = re.findall(r"lead_id: '(L[1-6])', raw: '([^']*)'", raw_code)
        self.assertEqual(
            literals, [(lead["lead_id"], lead["raw"]) for lead in RAW_LEADS]
        )
        for lead in RAW_LEADS:
            self.assertIn(lead["raw"], raw_code)
        validation_code = self.nodes["Validate & Dedupe"]["parameters"]["jsCode"]
        self.assertIn("/" + EMAIL_REGEX + "/", validation_code)
        self.assertIn("String(lead.email).trim().toLowerCase()", validation_code)

    def test_python_model_matches_all_expected_results(self):
        parsed, errors = parse_payloads(RAW_LEADS)
        validated = validate_and_dedupe(parsed)
        scored = score(validated)
        routed = route(scored)
        collected = {tier: collect(tier, routed[tier]) for tier in TIERS}
        self.assertEqual(collected["hot"], {
            "tier": "hot", "count": 2, "leads": [
                {"lead_id": "L1", "email": "ana@example.com", "score": 60},
                {"lead_id": "L6", "email": "dee@example.com", "score": 60},
            ],
        })
        self.assertEqual(routed["warm"], [])
        self.assertEqual(collected["warm"], {"tier": "warm", "count": 0, "leads": []})
        self.assertEqual(collected["cold"], {
            "tier": "cold", "count": 1, "leads": [
                {"lead_id": "L2", "email": "ben@example.com", "score": 10},
            ],
        })
        self.assertEqual(collected["rejected"], {
            "tier": "rejected", "count": 2, "leads": [
                {"lead_id": "L3", "email": "ana@example.com", "reason": "duplicate"},
                {"lead_id": "L4", "email": "cid@example", "reason": "invalid_email"},
            ],
        })
        self.assertEqual([error["lead_id"] for error in errors], ["L5"])
        self.assertEqual(errors[0]["stage"], "parse")
        self.assertEqual(errors[0]["reason"], "invalid_json")
        self.assertEqual(errors[0]["raw"], "not json")
        self.assertIsInstance(errors[0]["error"], str)
        self.assertTrue(errors[0]["error"])
        by_id = {lead["lead_id"]: lead for lead in scored}
        self.assertEqual(by_id["L3"]["email"], by_id["L1"]["email"])
        for lead in scored:
            with self.subTest(lead=lead["lead_id"]):
                self.assertIs(type(lead["score"]), int)
                self.assertIs(lead["valid"], lead["tier"] != "rejected")
                if lead["tier"] == "rejected":
                    original = next(item for item in validated
                                    if item["lead_id"] == lead["lead_id"])
                    self.assertEqual(lead, {**original, "score": 0})
                else:
                    self.assertIsNone(lead["reason"])
                    self.assertEqual(lead["score"],
                                     lead["budget_points"] + lead["source_points"])

    def test_validation_normalizes_and_does_not_record_invalid_emails(self):
        base = {"name": "Test", "budget": 2000, "source": "web"}
        leads = [
            {**base, "lead_id": "A", "email": "  ANA@Example.com  "},
            {**base, "lead_id": "B", "email": "ana@example.com"},
            {**base, "lead_id": "C", "email": "cid@example"},
            {**base, "lead_id": "D", "email": "cid@example"},
        ]
        validated = validate_and_dedupe(leads)
        self.assertEqual([lead["reason"] for lead in validated],
                         [None, "duplicate", "invalid_email", "invalid_email"])
        self.assertEqual(validated[0]["email"], "ana@example.com")

    def test_score_budget_source_and_tier_boundaries(self):
        cases = [
            (1999, "ads", 10, 0, 10, "cold"),
            (2000, "ads", 30, 0, 30, "warm"),
            (4999, "web", 30, 10, 40, "warm"),
            (5000, "web", 50, 10, 60, "hot"),
            (2000, "referral", 30, 30, 60, "hot"),
            (5000, "other", 50, 0, 50, "warm"),
        ]
        for budget, source, budget_points, source_points, total, tier in cases:
            with self.subTest(budget=budget, source=source):
                result = score([{"budget": budget, "source": source}])[0]
                self.assertEqual(result, {
                    "budget": budget, "source": source,
                    "budget_points": budget_points, "source_points": source_points,
                    "score": total, "tier": tier,
                })
                self.assertIs(type(result["score"]), int)


if __name__ == "__main__":
    unittest.main()
