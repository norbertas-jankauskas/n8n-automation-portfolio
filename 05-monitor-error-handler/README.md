# Self-Healing n8n Monitor (Postgres + AI Diagnosis)

Catches failures across other n8n workflows via a shared Error Workflow, logs them to PostgreSQL, and uses Claude to diagnose root cause and recommend action. Falls back to a stub diagnosis if the AI call itself fails, so incidents are never silently dropped.

**Flow:** Error Trigger → insert execution record (Postgres) → Claude diagnosis (HTTP Request, tool_use forced JSON output) → on success: extract structured diagnosis; on failure: build fallback diagnosis → update execution category → insert incident record.

**Verified on a real production bug:** caught a malformed CoinGecko API URL in the Bitcoin Price Watcher that had been silently failing on every scheduled run for 16+ hours — detected, logged, and AI-diagnosed automatically before manual discovery.

**Nodes used:** Error Trigger, HTTP Request (Anthropic API, tool_use forcing), IF, Set, Postgres (Insert/Update).
