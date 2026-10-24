# n8n Automation Portfolio

Self-taught n8n automation projects. Code was written by AI coding agents (Claude, ChatGPT) under my direction — I own the logic, testing, and debugging.

## Projects

1. **[Bitcoin Price Watcher](./01-bitcoin-price-watcher)** — crypto price monitoring, conditional logic, Telegram alerts, results logged to Google Sheets.
2. **[RSS Digest](./02-rss-digest)** — aggregates multiple RSS sources, filters duplicates, uses Claude to select and summarize the most relevant articles, sends a formatted email.
3. **[Gmail Tracker](./03-gmail-tracker)** — classifies incoming Gmail messages with AI (receipt / job application / unrecognized), extracts structured fields, logs to separate Google Sheets tabs with duplicate protection.
4. **[Support Agent](./04-support-agent)** — multi-tool AI agent with a vector knowledge base (Qdrant + Voyage AI embeddings), web search fallback (Tavily), and escalation to a human when confidence is low.
5. **[Monitor / Error Handler](./05-monitor-error-handler)** — catches workflow failures, logs them to PostgreSQL, and uses Claude to diagnose causes, with a fallback diagnosis if the AI call fails.
6. **[Observability Layer](./06-observability-layer)** — syncs workflow executions to Langfuse traces and spans, uses an asynchronous Claude judge to check routing, and records incidents in PostgreSQL.
7. **[Workflow Forge](./07-workflow-forge)** — meta-automation: generates other n8n workflows from natural-language task descriptions using Claude, with an automated retry/patch loop, operational hardening (auth, rate limiting), and no published implementation (see note in the project folder).
8. **[Order Processing](./08-order-processing)** — validates orders, calculates integer-cent totals, and builds spreadsheet-row, notification, or error payloads.
9. **[Drywall Estimate](./09-drywall-estimate)** — extracts job details from sample emails, applies placeholder pricing, and builds mock proposal or review-task payloads.
10. **[Lead Router](./10-lead-router)** — parses, normalizes, deduplicates, scores, and routes sample leads, with a separate report for parsing failures.
11. **[Jobber to Supabase Weekly KPIs](./11-jobber-supabase-kpi)** — fetches mock invoices with pagination and retries, separates invalid records, and upserts invoice rows and weekly KPIs to a local mock API.
12. **[Jobber to Supabase — live write](./12-jobber-supabase-live)** — writes synthetic invoice rows and weekly KPIs to real Supabase through a single-host egress proxy, with idempotency and constraint-failure evidence. A follow-up fix computes KPIs only from rows the database accepted (mock-verified).
13. **[Airtable lead CRM — live write](./13-airtable-lead-crm)** — parses, deduplicates and scores synthetic leads, then upserts them by email into a real Airtable base through a single-host egress proxy, with idempotency and rejected-record evidence.

**Projects 8–13:** Simulation of a common client request type. All data is synthetic, and external services are mocked. Executed in an isolated n8n 2.41.7 instance; evidence is in `evidence/`. Projects 12 and 13 are the exceptions: they wrote to a real temporary Supabase project and a real temporary Airtable base, each through a single-host egress proxy.

Projects 8–11 were built through my AI Project Control (APC) pipeline (planning → AI coding agent → independent review → isolated execution), which is why their workflow names start with "APC sim".

## Testing approach

Offline structural tests inspect workflow JSON and JavaScript text, while a Python reference implementation of the same logic is compared against expected results. The tests use only the standard library and do not execute JavaScript or n8n. Each workflow was also executed in an isolated n8n 2.41.7 instance, with output saved in its `evidence/` folder and provenance recorded in `evidence/SOURCE.md`.

## Security note

All credentials are exported as internal n8n references only (n8n never exports real API keys). Personal identifiers (Google Sheets document IDs, webhook IDs, n8n instance ID) have been replaced with placeholders before publishing.
