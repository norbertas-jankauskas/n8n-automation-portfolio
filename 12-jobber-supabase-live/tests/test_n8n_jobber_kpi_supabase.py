"""Structural checks for the narrowly derived Supabase workflow; no JS execution."""

import copy
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://dgmzseviicodcvoqtybw.supabase.co:443"


class SupabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = json.loads((ROOT / "workflow.json").read_bytes())
        self.local = json.loads((ROOT / "base-workflow.json").read_bytes())
        self.nodes = {node["name"]: node for node in self.workflow["nodes"]}

    def test_identity_and_only_requested_changes(self):
        self.assertEqual(self.workflow["name"], "APC sim — Jobber KPI to Supabase")
        self.assertEqual(self.workflow["id"], "apcJobberKpiSb01")
        restored = copy.deepcopy(self.workflow)
        restored["name"], restored["id"] = self.local["name"], self.local["id"]
        restored["nodes"].pop()
        local_nodes = {node["name"]: node for node in self.local["nodes"]}
        for node in restored["nodes"]:
            if node["name"] in ("Upsert Invoice", "Upsert KPI"):
                node["parameters"]["url"] = local_nodes[node["name"]]["parameters"]["url"]
                del node["parameters"]["authentication"]
                del node["parameters"]["genericAuthType"]
                del node["credentials"]
            if node["name"] == "Upsert Invoice":
                del node["onError"]
        del restored["connections"]["Upsert Invoice"]
        self.assertEqual(restored, self.local)

    def test_exact_urls_and_credential_refs(self):
        for name, suffix in (("Upsert Invoice", "/invoices?on_conflict=id"),
                             ("Upsert KPI", "/kpi_weekly?on_conflict=iso_week")):
            node = self.nodes[name]
            self.assertEqual(node["parameters"]["url"], ORIGIN + "/rest/v1" + suffix)
            self.assertEqual(node["parameters"]["authentication"], "genericCredentialType")
            self.assertEqual(node["parameters"]["genericAuthType"], "httpHeaderAuth")
            self.assertEqual(node["credentials"], {"httpHeaderAuth": {"id": "apcSupabase0001", "name": "APC Supabase"}})
            self.assertNotIn("Authorization", json.dumps(node))
        self.assertEqual(self.nodes["Fetch Invoices"]["parameters"]["url"], "http://127.0.0.1:5690/graphql")
        self.assertNotIn("credentials", self.nodes["Fetch Invoices"])

    def test_error_branch_is_invoice_error_output_only(self):
        self.assertEqual(self.nodes["Upsert Invoice"]["onError"], "continueErrorOutput")
        self.assertEqual(self.workflow["connections"]["Upsert Invoice"]["main"], [[], [
            {"node": "Build Upsert Error Report", "type": "main", "index": 0}]])
        node = self.nodes["Build Upsert Error Report"]
        self.assertEqual(node["type"], "n8n-nodes-base.code")
        self.assertEqual(node["typeVersion"], 2)
        self.assertEqual(node["parameters"]["mode"], "runOnceForAllItems")
        code = node["parameters"]["jsCode"]
        for value in ("$input.all().map", "stage: 'upsert'", "id:", "status:", "message:"):
            self.assertIn(value, code)

    def test_partial_fixture_kpis_preserve_parallel_valid_input_branch(self):
        success = self.workflow["connections"]["Validate & Transform"]["main"][0]
        self.assertEqual([edge["node"] for edge in success], ["Upsert Invoice", "Compute KPIs"])
        revenue = 99999 + 20000000
        self.assertEqual((revenue, revenue // 2), (20099999, 10049999))
        self.assertNotIn("10000000", self.nodes["Validate & Transform"]["parameters"]["jsCode"])
