# 06 — AI Agent Observability Layer

An observability and reliability layer built on top of Project 4 (CoinGecko Support Agent), using self-hosted Langfuse for tracing and an async LLM judge to catch routing failures in production traffic — closing the loop between "the agent works" and "we can prove it, and catch it when it doesn't."

## Architecture

```
Schedule Trigger (15 min)
  → List new n8n executions (Project 4)
  → Fetch execution detail
  → Parse into Langfuse trace + span events
      ├─→ Push to Langfuse (self-hosted, Docker Compose)
      └─→ Extract Judge Input
            → Claude Sonnet 5 (forced tool_use, structured verdict)
            → Parse Judge Result
            → IF: routing_correct == false OR confidence < 0.7
                  → Insert incident (Postgres, shared schema with Project 5)
```

The judge runs asynchronously on the existing 15-minute sync cycle rather than inline with live user queries — zero added latency or cost on the user-facing path, while still catching routing mismatches on real, unlabeled production traffic (not just a static eval set).

## Case study: catching a real routing bug

**Eval run.** A 12-question dataset (`project-4-tool-routing-eval`) covering three expected routing categories (KB search / web search / direct answer) was run against Project 4 and checked against Langfuse traces: 11/12 correct.

**The failure.** Q3 — *"What happened to crypto markets today?"* — the agent called the knowledge-base tool, got a tangentially related match, and stopped there instead of escalating to web search. It told the user it couldn't help, when live market data was one tool call away.

**Root cause.** Project 4's system prompt had a single tool-priority rule: *"web search only if the knowledge base doesn't contain a clear answer."* That conflates two different things — questions about **how to use the API** (the knowledge base's real domain) and questions about **current real-world state** (which no knowledge base can ever answer, regardless of what it returns).

**The fix.** Added one explicit rule distinguishing the two:

> Time-sensitive or current-event questions ("today," "right now," "is X down," "recent news") describe real-world state, not API usage — the knowledge base cannot answer these even if it returns related documentation. For these questions, use Web Search directly, and do not treat a knowledge-base match on a related topic as a sufficient answer.

**Verification.** Re-ran the same question post-fix: the agent skipped the knowledge base entirely, called web search, and returned accurate current data with a source citation. Confirmed with before/after Langfuse traces.

**Closing the loop.** The routing judge built in this project encodes the same distinction the fix relies on, and runs against every real execution going forward — not just the 12-question eval set. It was validated against a second real misrouting (asking for Bitcoin's current price with the fix temporarily reverted), which produced a real Langfuse trace, a real judge verdict, and a real row written to the shared `incidents` table — end-to-end, no mocked data.

## Nodes used

Schedule Trigger, HTTP Request (n8n API — list/fetch executions), Code (JavaScript — event parsing, judge input extraction, judge result parsing), HTTP Request (Anthropic API, forced `tool_use`), IF, Postgres (Insert), Loop Over Items, HTTP Request (Langfuse ingestion API).

## Stack

- **Langfuse** — self-hosted via Docker Compose (Postgres, ClickHouse, Redis, MinIO)
- **Postgres** — shared `incidents` table with Project 5's self-healing monitor, unifying both projects' reliability data under one schema
- **Claude Sonnet 5** — routing judge, structured output via forced tool use
