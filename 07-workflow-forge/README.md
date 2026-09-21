# Workflow Forge — Case Study

*n8n meta-automation: a workflow that builds other n8n workflows from natural-language task descriptions, using Claude as the generation backend, with an automated retry/patch loop for self-correction.*

## Overview

Workflow Forge is Project 7 of an n8n automation portfolio. A client (or another automation) sends a task description to a webhook; the system generates a complete, working n8n workflow — nodes, connections, credentials wiring — without a human building it by hand. If the generated workflow fails validation, creation, or activation, it automatically patches and retries (up to 5 attempts, with a hard safety cap), and only escalates to a human if it genuinely can't succeed.

This demonstrates the kind of infrastructure-level automation that goes beyond single-purpose workflows: building a system that builds systems.

## Architecture

- **Intake**: a single webhook receives a task description and expected output.
- **Generation loop**: Claude generates a full n8n workflow JSON against a constrained node library (only real, available node types/credentials), with explicit rules (must start with a webhook trigger, valid connections, credential references by name, etc.).
- **Validation, creation, activation**: the generated JSON is created via the n8n API and activated. Failures at any stage (bad JSON, missing fields, activation errors) feed back into the loop as a "patch this" instruction with the specific failure reason, up to 5 attempts.
- **Absolute hard cap**: an independent, execution-scoped counter forces a stop at 10 total generation calls regardless of any other logic — a defense-in-depth safety net so a logic bug elsewhere can never cause a runaway loop.
- **Escalation**: if all retries are exhausted, the system reports failure with the specific last error rather than looping silently.

## Operational Hardening

Three things were added after the core logic worked, to make this production-safe rather than just a demo:

1. **Webhook authentication** — n8n's native header-auth on the intake webhook. Unauthorized requests are rejected before any execution starts, at zero cost. Verified: no header returns 403, correct header returns 200.
2. **Rate limiting** — a sliding-window check (max 3 requests per minute, max 10 per hour) using n8n workflow static data keyed by execution ID, run before any Claude call. Bounds worst-case API spend even under a retry-storm or leaked secret. Verified live: requests 1 through 3 pass, request 4 blocked with 429.
3. **Minimal-payload PUT discipline** — every patch to the orchestrator re-fetches the workflow fresh via GET immediately before writing, sending only the required fields. This specific discipline is what prevented a stale-snapshot regression from recurring after it was first fixed.

## Bugs Found and Fixed During Stress-Testing

- Runaway loop, twice (the second a regression of the first fix). Root cause: a named-node reference silently returned a frozen value from a node that only runs once, instead of the current loop iteration's state. Fix: conditional state reference plus an independent execution-scoped hard-cap counter as defense-in-depth.
- Google Sheets writes silently corrupted — wrong data landed in the sheet. Root cause: the node library's write example used a mapping mode that needs an internal schema array not demonstrated, so n8n silently fell back to auto-mapping raw input instead of erroring. Fix: rewrote the library's write pattern to a Set node feeding a Google Sheets node in auto-map mode, fixed at the systemic library level so all future generations benefit.
- Unhelpful error messages surfaced to the retry loop. Root cause: n8n's error object puts a generic wrapper message in one field and the specific cause in another; the code checked the generic one first. Fix: flipped the priority.
- Workflow creation and activation failures were dead ends, never retried. Root cause: only generation failures fed the retry loop; creation/activation errors went straight to failure even though Claude could plausibly self-correct them. Fix: added a path that feeds these failures back into the retry loop too.
- Concurrent requests could corrupt each other's iteration counters. Root cause: the hard-cap counter was a single shared field reset by every execution. Fix: keyed the counter by the execution's own ID, verified with real simultaneous concurrent requests.
- Generated workflow called a dead external API (DNS lookup failure). Root cause: the node library had no guidance on which public demo APIs are actually reachable, so Claude guessed a plausible one that had since been discontinued. Fix: added a list of verified-reachable public APIs with an explicit instruction not to invent unlisted URLs — a systemic fix, not a one-off patch.

Additional tests run without finding new bugs: malformed input (missing task field), an explicitly impossible task (sending a fax), a multi-credential task (external API fetch plus Google Sheets write), and a complex branching workflow (fetch, conditional branch, typed response).

## Real-World Test: Lead Qualification and Logging

To validate the system on a realistic freelance-automation request rather than a synthetic test, Workflow Forge was asked to build a webhook-triggered lead qualifier: receives name, email, message, and budget; declines immediately if budget is missing or under 100; otherwise fetches a random quote, logs the lead to a Google Sheet, and responds with an accepting message.

Result: generated successfully on the first attempt. Live testing found one real bug — the generated workflow called a quote API that turned out to be dead, confirmed via direct DNS lookup. This was not a Workflow Forge defect; the generated logic, branching, and Google Sheets integration were all correct. Patched the specific workflow and the systemic library note in the same pass, then re-verified both branches: the decline case returned the correct rejection with no sheet write, and the accept case fetched a quote from the replacement API, logged the lead to the sheet with all fields correct, and returned a correctly formatted response.

## Takeaways

- The system reliably builds and self-corrects real n8n workflows from plain-language task descriptions, including realistic client-style requests, not just synthetic tests.
- Every bug found during stress-testing was fixed at the systemic level — the node library or the orchestrator logic itself — rather than patched per-instance, so the fix benefits every future generation.
- Operational hardening (auth, rate limiting, cost caps) was treated as a first-class requirement, not an afterthought, before calling the project done.
- The dead-API incident is a useful demonstration in itself: it shows the difference between the AI producing something wrong versus the AI building something correct that depends on an external service that failed, and shows the review process catching and permanently fixing that class of issue.

---

**Note:** unlike Projects 1–6, the implementation (n8n workflow export, prompt engineering, node-library curation) is not published here — it's a system that could itself become a service offering, not just a single-purpose demo. Happy to walk through the working system live on a call.
