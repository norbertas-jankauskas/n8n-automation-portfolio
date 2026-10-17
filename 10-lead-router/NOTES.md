# APC sim — lead router

Offline lead-intake simulation: parse, normalize, deduplicate, score, and route six mock leads.

Input contract: `{lead_id, raw}`, where `raw` is a JSON string.
Parsed fields: `lead_id`, `name`, `email`, `budget`, `source`.
Validation trims and lowercases email; the first valid normalized email wins.
Validation adds `valid` and `reason` (null, `invalid_email`, or `duplicate`).
Scoring adds `budget_points`, `source_points`, `score`, and `tier` to valid leads.
Rejected leads retain their fields and receive `score: 0` and `tier: "rejected"`.
Each tier collector emits `{tier, count, leads}`; hot/warm/cold leads contain
`{lead_id, email, score}`, while rejected leads contain `{lead_id, email, reason}`.
An empty branch does not run its Collect node.

| Component | Rule | Points / tier |
| --- | --- | --- |
| Budget | >= 5000 | 50 |
| Budget | >= 2000 and < 5000 | 30 |
| Budget | Else | 10 |
| Source | referral / web / ads / other | 30 / 10 / 0 / 0 |
| Tier | score >= 60 / >= 30 / else | hot / warm / cold |

Parse Payload uses `onError: "continueErrorOutput"`: JSON parsing failures go
through main output 1 to Build Error Report, retaining `lead_id`, `raw`, and `error`.
The report is `{lead_id, stage: "parse", reason: "invalid_json", error, raw}`.
Invalid emails and duplicates reach the rejected collector through the Switch.

Expected: hot L1/L6 (60 each), no warm leads, cold L2 (10), rejected L3
(`duplicate`)/L4 (`invalid_email`), and one parse error report for L5.

Raw Leads is mock data. Switch typeVersion 3.4 and filter version 3 are taken
from the locally cached n8n 2.41.7 package. No credentials or network nodes exist.
The workflow is inactive; it was executed only in an isolated n8n 2.41.7 test instance with synthetic data (see `evidence/`).

Later real swap: replace Manual Trigger + Raw Leads with a Webhook trigger,
and replace Collect nodes with CRM / Google Sheets nodes. These integrations
need credentials and are NOT done here.

Run tests: `python3 -m unittest tests.test_n8n_lead_router`.
Tests inspect the workflow and model its rules in Python; they do not execute JS.
