"""Workflow structure and a Python reference model against the pure CRM mock."""

import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest import mock
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
MODULES = {}
for name, path in (("router", "tests/reference/lead_router_model.py"),
                   ("crm", "mock/mock_lead_crm_local.py")):
    spec = importlib.util.spec_from_file_location("crm_reference_" + name, ROOT / path)
    MODULES[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MODULES[name])
ROUTER, CRM = MODULES["router"], MODULES["crm"]
COPIED = ("Parse Payload", "Build Error Report", "Validate & Dedupe", "Score",
          "Collect Rejected")
EXPECTED = [
    {"Email": "ana@example.com", "Lead ID": "L1", "Name": "Ana", "Budget": 5000,
     "Source": "web", "Score": 60, "Tier": "hot"},
    {"Email": "ben@example.com", "Lead ID": "L2", "Name": "Ben", "Budget": 800,
     "Source": "ads", "Score": 10, "Tier": "cold"},
    {"Email": "dee@example.com", "Lead ID": "L6", "Name": "Dee", "Budget": 2500,
     "Source": "referral", "Score": 60, "Tier": "hot"},
]
EMAILS = [row["Email"] for row in EXPECTED]
BODY = "={{ JSON.stringify({ performUpsert: { fieldsToMergeOn: ['Email'] }, records: [ { fields: { 'Email': $json.email, 'Lead ID': $json.lead_id, 'Name': $json.name, 'Budget': $json.budget, 'Source': $json.source, 'Score': $json.score, 'Tier': $json.tier } } ] }) }}"


def load_workflow(variant="local"):
    return json.loads((ROOT / {"local": "base-workflow.json", "airtable": "workflow.json"}[variant]).read_text())


def nodes_by_name(workflow):
    return {node["name"]: node for node in workflow["nodes"]}


def summarize(responses):
    """Model the summary strictly from returned response representations."""
    result = {"created": 0, "updated": 0, "by_tier": {}, "emails": []}
    for response in responses:
        result["created"] += len(response["createdRecords"])
        result["updated"] += len(response["updatedRecords"])
        for record in response["records"]:
            fields = record["fields"]
            tier = fields["Tier"]
            result["by_tier"][tier] = result["by_tier"].get(tier, 0) + 1
            result["emails"].append(fields["Email"])
    result["emails"].sort()
    return result


def run_workflow(state, workflow=None):
    """Model parsing, routing, the literal field expression, retries and reports."""
    nodes = nodes_by_name(workflow or load_workflow())
    status, payload = state.handle("GET", urlsplit(nodes["Fetch Leads"]["parameters"]["url"]).path, {}, None, None)
    if status != 200:
        raise RuntimeError("fetch failed")
    parsed, parse_errors = ROUTER.parse_payloads(payload["leads"])
    scored = ROUTER.score(ROUTER.validate_and_dedupe(parsed))
    rejected, accepted = [], []
    for lead in scored:
        if lead["tier"] == "rejected":
            rejected.append(lead)
        else:
            accepted.append(lead)
    upsert = nodes["Upsert Lead"]
    mapping = re.findall(r"'([^']+)': \$json\.(\w+)", upsert["parameters"]["jsonBody"])
    responses, upsert_errors = [], []
    for lead in accepted:
        fields = {field: lead[source] for field, source in mapping}
        body = {"performUpsert": {"fieldsToMergeOn": ["Email"]}, "records": [{"fields": fields}]}
        for _ in range(upsert["maxTries"]):
            status, response = state.handle("PATCH", urlsplit(upsert["parameters"]["url"]).path,
                                            {}, None, body)
            if status == 200:
                responses.append(response)
                break
        else:
            if upsert.get("onError") != "continueErrorOutput":
                raise RuntimeError("upsert failed")
            error = response["error"]
            upsert_errors.append({"stage": "upsert", "lead_id": lead["lead_id"], "email": lead["email"],
                                  "status": status, "message": error["type"] + ": " + error["message"]})
    return summarize(responses), parse_errors, ROUTER.collect("rejected", rejected), upsert_errors


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.nodes = nodes_by_name(self.workflow)

    def test_name_id_inactive_and_exact_nodes_types_versions(self):
        self.assertEqual(list(self.workflow), ["name", "id", "active", "nodes", "connections"])
        self.assertEqual(self.workflow["name"], "APC sim — lead router to CRM (local)")
        self.assertEqual(self.workflow["id"], "apcLeadCrmLocal1")
        self.assertIs(self.workflow["active"], False)
        expected = {"Manual Trigger": ("manualTrigger", 1), "Fetch Leads": ("httpRequest", 4.5),
                    "Split Leads": ("code", 2), **{name: ("code", 2) for name in COPIED[:-1]},
                    "Accept or Reject": ("switch", 3.4), "Collect Rejected": ("code", 2),
                    "Upsert Lead": ("httpRequest", 4.5), "Summarize CRM": ("code", 2),
                    "Build Upsert Error Report": ("code", 2)}
        self.assertEqual(list(self.nodes), list(expected))
        self.assertEqual(len(self.workflow["nodes"]), 12)
        self.assertEqual(len({node["id"] for node in self.nodes.values()}), 12)
        for name, (kind, version) in expected.items():
            self.assertEqual((self.nodes[name]["type"], self.nodes[name]["typeVersion"]),
                             ("n8n-nodes-base." + kind, version))
        self.assertEqual(self.nodes["Manual Trigger"]["parameters"], {})

    def test_copied_nodes_preserve_all_required_properties(self):
        original = nodes_by_name(json.loads((ROOT / "tests/reference/lead-router.workflow.json").read_text()))
        for variant in ("local", "airtable"):
            actual = nodes_by_name(load_workflow(variant))
            for name in COPIED:
                for key in ("type", "typeVersion", "parameters", "onError"):
                    with self.subTest(variant=variant, node=name, key=key):
                        self.assertEqual(actual[name].get(key), original[name].get(key))

    def test_accept_or_reject_preserves_router_rejected_rule(self):
        original = nodes_by_name(json.loads((ROOT / "tests/reference/lead-router.workflow.json").read_text()))
        rejected = next(rule for rule in original["Route by Tier"]["parameters"]["rules"]["values"]
                        if rule["outputKey"] == "rejected")
        for variant in ("local", "airtable"):
            with self.subTest(variant=variant):
                node = nodes_by_name(load_workflow(variant))["Accept or Reject"]
                self.assertEqual(node["id"], "accept-or-reject")
                self.assertEqual((node["type"], node["typeVersion"]), ("n8n-nodes-base.switch", 3.4))
                self.assertEqual(node["parameters"]["rules"], {"values": [rejected]})

    def test_accept_or_reject_has_extra_fallback_output(self):
        for variant in ("local", "airtable"):
            with self.subTest(variant=variant):
                node = nodes_by_name(load_workflow(variant))["Accept or Reject"]
                self.assertEqual(set(node["parameters"]), {"rules", "options"})
                self.assertEqual(node["parameters"]["options"], {"fallbackOutput": "extra"})

    def test_upsert_has_exactly_one_incoming_edge_from_fallback(self):
        for variant in ("local", "airtable"):
            with self.subTest(variant=variant):
                incoming = [(source, kind, output, edge)
                            for source, connections in load_workflow(variant)["connections"].items()
                            for kind, outputs in connections.items()
                            for output, edges in enumerate(outputs)
                            for edge in edges if edge["node"] == "Upsert Lead"]
                self.assertEqual(len(incoming), 1)
                self.assertEqual(incoming, [("Accept or Reject", "main", 1,
                                             {"node": "Upsert Lead", "type": "main", "index": 0})])

    def test_fetch_split_and_upsert_parameters_and_retries(self):
        fetch = self.nodes["Fetch Leads"]
        self.assertEqual(fetch["parameters"], {"method": "GET", "url": "http://127.0.0.1:5690/leads", "options": {}})
        split = self.nodes["Split Leads"]["parameters"]
        self.assertEqual(split, {"mode": "runOnceForAllItems", "language": "javaScript",
            "jsCode": 'return $input.first().json.leads.map((lead) => ({ json: { lead_id: lead.lead_id, raw: lead.raw } }));'})
        upsert = self.nodes["Upsert Lead"]
        self.assertEqual(upsert["parameters"], {"method": "PATCH", "url": "http://127.0.0.1:5690" + CRM.TABLE_PATH,
                         "sendBody": True, "contentType": "json", "specifyBody": "json", "jsonBody": BODY, "options": {}})
        self.assertEqual(upsert["onError"], "continueErrorOutput")
        for node in (fetch, upsert):
            self.assertIs(node["retryOnFail"], True)
            self.assertEqual(node["maxTries"], 3)
            self.assertEqual(node["waitBetweenTries"], 1000)
        self.assertNotIn("typecast", json.dumps(self.workflow))
        self.assertNotIn("credentials", json.dumps(self.workflow))

    def test_exact_connections(self):
        def edge(name):
            return [{"node": name, "type": "main", "index": 0}]
        expected = {
            "Manual Trigger": {"main": [edge("Fetch Leads")]},
            "Fetch Leads": {"main": [edge("Split Leads")]},
            "Split Leads": {"main": [edge("Parse Payload")]},
            "Parse Payload": {"main": [edge("Validate & Dedupe"), edge("Build Error Report")]},
            "Validate & Dedupe": {"main": [edge("Score")]},
            "Score": {"main": [edge("Accept or Reject")]},
            "Accept or Reject": {"main": [edge("Collect Rejected"), edge("Upsert Lead")]},
            "Upsert Lead": {"main": [edge("Summarize CRM"), edge("Build Upsert Error Report")]},
        }
        self.assertEqual(self.workflow["connections"], expected)

    def test_variants_differ_only_in_stated_keys(self):
        airtable = load_workflow("airtable")
        self.assertEqual(airtable["name"], "APC sim — lead router to Airtable CRM")
        self.assertEqual(airtable["id"], "apcLeadCrmAirt1")
        actual = nodes_by_name(airtable)["Upsert Lead"]
        self.assertEqual(actual["parameters"]["url"], "https://api.airtable.com:443" + CRM.TABLE_PATH)
        self.assertEqual(actual["parameters"].pop("authentication"), "genericCredentialType")
        self.assertEqual(actual["parameters"].pop("genericAuthType"), "httpHeaderAuth")
        self.assertEqual(actual.pop("credentials"), {"httpHeaderAuth": {"id": "apcAirtable0001", "name": "APC Airtable"}})
        actual["parameters"]["url"] = self.nodes["Upsert Lead"]["parameters"]["url"]
        airtable.update(name=self.workflow["name"], id=self.workflow["id"])
        self.assertEqual(airtable, self.workflow)

    def test_exact_allowlists(self):
        for variant in ("airtable",):
            policy = json.loads((ROOT / "allowlist.json").read_text())
            expected = {"node_types": {"n8n-nodes-base.code": [2], "n8n-nodes-base.httpRequest": [4.5],
                        "n8n-nodes-base.manualTrigger": [1], "n8n-nodes-base.switch": [3.4]},
                        "origins": ["http://127.0.0.1:5690"], "allow_error_workflow": False}
            if variant == "airtable":
                expected["origins"].append("https://api.airtable.com:443")
                expected["egress"] = {"origin": "https://api.airtable.com:443", "credential": {
                    "type": "httpHeaderAuth", "id": "apcAirtable0001", "name": "APC Airtable"}}
            self.assertEqual(policy, expected)

    def test_summary_uses_only_returned_records_and_counts_response_ids(self):
        node = self.nodes["Summarize CRM"]
        self.assertEqual(node["parameters"]["mode"], "runOnceForAllItems")
        self.assertEqual(node["parameters"]["language"], "javaScript")
        code = node["parameters"]["jsCode"]
        for fragment in ("$input.all()", "response.createdRecords.length", "response.updatedRecords.length",
                         "response.records", "fields.Tier", "fields.Email", "summary.emails.sort()",
                         "return [{ json: summary }]"):
            self.assertIn(fragment, code)
        self.assertNotIn("$('Score')", code)
        self.assertNotIn("$json.email", code)

    def test_error_report_follows_accepted_workflow_and_score_item_link(self):
        node = self.nodes["Build Upsert Error Report"]
        original = nodes_by_name(json.loads((ROOT / "tests/reference/jobber-kpi-accepted.workflow.json").read_text()))[node["name"]]
        expected = copy.deepcopy(original["parameters"])
        expected["jsCode"] = expected["jsCode"].replace('item.json.id', 'item.json.lead_id').replace(
            "$('Validate & Transform')", "$('Score')").replace(
            '    id: item.json.lead_id ?? original.id,',
            '    lead_id: item.json.lead_id ?? original.lead_id,\n    email: item.json.email ?? original.email,')
        self.assertEqual(node["parameters"], expected)
        for fragment in ("$('Score').itemMatching(index)", "stage: 'upsert'", "lead_id:", "email:",
                         "status:", "message:", "statusInMessage"):
            self.assertIn(fragment, node["parameters"]["jsCode"])


class ModelTests(unittest.TestCase):
    def assert_default(self, state, result, created, updated):
        summary, errors, rejected, upsert_errors = result
        self.assertEqual(state.snapshot(), {"leads": EXPECTED})
        self.assertEqual(summary, {"created": created, "updated": updated,
                                  "by_tier": {"hot": 2, "cold": 1}, "emails": EMAILS})
        self.assertEqual([row["lead_id"] for row in errors], ["L5"])
        self.assertEqual(errors[0]["stage"], "parse")
        self.assertEqual(errors[0]["reason"], "invalid_json")
        self.assertEqual(errors[0]["raw"], "not json")
        self.assertEqual(rejected, {"tier": "rejected", "count": 2, "leads": [
            {"lead_id": "L3", "email": "ana@example.com", "reason": "duplicate"},
            {"lead_id": "L4", "email": "cid@example", "reason": "invalid_email"}]})
        return upsert_errors

    def test_default_first_and_second_run(self):
        state = CRM.MockState()
        self.assertEqual(self.assert_default(state, run_workflow(state), 3, 0), [])
        self.assertEqual(self.assert_default(state, run_workflow(state), 0, 3), [])

    def test_partial_422_reports_only_l7_and_excludes_it_from_summary(self):
        state = CRM.MockState("partial")
        original = state.handle
        with mock.patch.object(state, "handle", wraps=original) as calls:
            errors = self.assert_default(state, run_workflow(state), 3, 0)
        self.assertEqual(errors, [{"stage": "upsert", "lead_id": "L7", "email": "eve@example.com", "status": 422,
            "message": 'INVALID_MULTIPLE_CHOICE_OPTIONS: Insufficient permissions to create new select option ""fax""'}])
        attempts = [call for call in calls.call_args_list if call.args[0] == "PATCH"
                    and call.args[4]["records"][0]["fields"]["Email"] == "eve@example.com"]
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.assert_default(state, run_workflow(state), 0, 3), errors)

    def test_airtable_variant_has_same_reference_results(self):
        state = CRM.MockState()
        self.assertEqual(self.assert_default(state, run_workflow(state, load_workflow("airtable")), 3, 0), [])

    def test_summary_follows_changed_returned_fields_and_id_counts(self):
        state = CRM.MockState()
        original = state.handle

        def adjusted(method, path, query, prefer, body):
            status, payload = original(method, path, query, prefer, body)
            if method == "PATCH" and body["records"][0]["fields"]["Email"] == "ana@example.com":
                payload["records"][0]["fields"].update(Email="zzz@example.com", Tier="warm")
                payload["updatedRecords"] = payload.pop("createdRecords")
                payload["createdRecords"] = []
            return status, payload

        with mock.patch.object(state, "handle", side_effect=adjusted):
            result = run_workflow(state)
        self.assertEqual(result[0], {"created": 2, "updated": 1, "by_tier": {"warm": 1, "hot": 1, "cold": 1},
                                     "emails": ["ben@example.com", "dee@example.com", "zzz@example.com"]})
        self.assertEqual(state.snapshot(), {"leads": EXPECTED})

    def test_no_successes_produce_no_summary_counts(self):
        state = CRM.MockState()
        original = state.handle

        def rejected(method, path, query, prefer, body):
            if method == "PATCH":
                return 422, {"error": {"type": "INVALID_VALUE_FOR_COLUMN", "message": "Rejected"}}
            return original(method, path, query, prefer, body)

        with mock.patch.object(state, "handle", side_effect=rejected):
            summary, _, _, errors = run_workflow(state)
        self.assertEqual(summary, {"created": 0, "updated": 0, "by_tier": {}, "emails": []})
        self.assertEqual([error["lead_id"] for error in errors], ["L1", "L2", "L6"])
        self.assertEqual(state.snapshot(), {"leads": []})


if __name__ == "__main__":
    unittest.main()
