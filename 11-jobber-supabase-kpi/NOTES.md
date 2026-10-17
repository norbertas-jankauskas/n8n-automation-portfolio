# Jobber KPI reporting

Simulation of a common client request type: ingesting Jobber invoices and upserting weekly Supabase KPIs.
The workflow is inactive, has no credentials, and uses only 127.0.0.1:5690.

Input: `{data: {invoices: {nodes, pageInfo: {hasNextPage, endCursor}}}}`.
Each node contains `{id, client, status, total, issuedAt}`; `total` must be
a string matching `^\d+\.\d{2}$`, and `issuedAt` is a date (`YYYY-MM-DD`).
Integer string math converts totals to safe integer cents, without float parsing.
Invoice output: `{id, client, status, total_cents, issued_at, iso_week}`.
ISO weeks use UTC and the ISO week-year, formatted `YYYY-Www`.

Flow: Manual Trigger → Fetch Invoices → Flatten → Validate & Transform.
Success output 0 feeds both Upsert Invoice and Compute KPIs → Upsert KPI.
Validation error output 1 feeds Build Error Report: `{id, reason}`.
INV-4 fails with `invalid_total`; the four valid invoices reach both branches.

KPI rows: `{iso_week, revenue_paid_cents, invoices_paid, invoices_open,
avg_paid_cents}`. Only `paid` invoices contribute revenue and paid counts.
Every other status counts as open; invalid invoices contribute nothing.
Average is floor(paid revenue / paid count), or 0 when paid count is 0.

| ISO week | Paid revenue (cents) | Paid | Open | Average (cents) |
| --- | --- | --- | --- | --- |
| 2026-W41 | 165050 | 2 | 1 | 82525 |
| 2026-W42 | 99999 | 1 | 0 | 99999 |

Fetch uses HTTP Request 4.5 built-in body-cursor pagination, capped at 10 pages.
Null-safe expressions handle the first request and the injected 429 response.
Fetch and both upserts retry up to 3 attempts, waiting 1000 ms between attempts.
Transport failures stop execution after retries; validation errors use their branch.
Source-confirmed in n8n 2.41.7 `workflow-execute.js`: node-level retry re-runs
the whole Fetch Invoices node, restarting pagination at `after: null`.
Therefore its fresh-process cursor log is `null, c2→429, null, c2, c3` (5 calls).
The requested 4-call log `null, c2→429, c2, c3` requires page-level retry;
the offline tests model that separately and also check whole-node restart.
Both models produce 4 invoice upserts, 2 KPI upserts, and the same final state.

Idempotency: `on_conflict=id` / `on_conflict=iso_week` plus
`Prefer: resolution=merge-duplicates` replaces rows by their stable keys.
Repeating the same upserts leaves state unchanged. Weekly rows replace totals;
real reporting must fetch the complete reporting window to avoid partial totals.

Mock boundaries: one handler instance per process, in-memory tables, fixed pages,
one 429 on the first c2 request, no outbound calls or authentication.
`GET /__state` is a test aid. The thin stdlib HTTP wrapper binds only 127.0.0.1;
its CLI accepts `--port` (default 5690) and required `--log <path>` for JSON lines.
No server, n8n, JavaScript runtime, socket, or listener was started here.

Real swap: use Jobber OAuth credentials and its actual GraphQL schema, mapping
the mock top-level `after` into `variables.after` for standard GraphQL requests.
Replace mock URLs with Supabase URL and service key held in n8n credentials;
create unique conflict keys and replace Manual Trigger with a Schedule trigger.
OAuth, real schema mapping, deployment, and scheduled runs are outside this mock.

Offline tests: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`.
New tests import the mock by path and inspect JS text/model rules in Python;
they use no sockets, subprocesses, network, or persistent test files.
