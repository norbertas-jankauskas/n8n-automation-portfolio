# Drywall estimate

Simulation of a common client request type: an email-to-estimate proposal system for drywall using n8n, Claude, GoHighLevel, PDFs and JSON.

Phase 1 is an offline simulation with deterministic mock data. Currency is USD;
all pricing uses integer cents. Rates below are placeholders, not real prices.
A 4x8 sheet covers 32 sqft; allow 10% waste on walls plus ceiling area.
Sheets = ceil(area * 110 / 3200), computed as `(area * 110 + 3199) // 3200`
in Python and `Math.floor((area * 110 + 3199) / 3200)` in JavaScript.

| Component | Placeholder rate |
| --- | --- |
| Standard board per sheet | 1500 cents ($15.00) |
| Moisture-resistant board per sheet | 2200 cents ($22.00) |
| Level 3 labor per sqft | 150 cents ($1.50) |
| Level 4 labor per sqft | 180 cents ($1.80) |
| Level 5 labor per sqft | 230 cents ($2.30) |
| Ceiling surcharge per sqft | 40 cents ($0.40) |
| Overhead and margin | 20% of subtotal; round-half-up on cents |

Subtotal = materials + labor + ceiling surcharge. Margin uses
`(subtotal_cents * 20 + 50) // 100`; total = subtotal + margin.
E1 totals 463800 cents ($4638.00); E2 totals 145680 cents ($1456.80).
E3 lacks walls area, ceiling area and board type, so it produces only a review task.

Mock boundaries: Sample Emails = MOCK inbox; Extract Job [AI STUB] = deterministic
regex stand-in for a later Claude extraction call; Build GHL Proposal = MOCK
payload shape, not the real API. Nothing is sent; no services or credentials are used.
The If node typeVersion 2.3 comes from the locally cached n8n 2.41.7 package;
filter version 3 routes true at output 0 and false at output 1.
The workflow is inactive; it was executed only in an isolated n8n 2.41.7 test instance with synthetic data (see `evidence/`).

Later real swaps: Gmail/IMAP trigger, Claude extraction with structured JSON output,
PDF generation, and GoHighLevel API contacts plus estimates/proposals.
All need credentials and are NOT done here.

Run tests from the repository root: `python3 -m unittest tests.test_n8n_drywall`.
Tests inspect workflow structure and JS text, then simulate the rules in Python;
they do not execute JavaScript or n8n.
