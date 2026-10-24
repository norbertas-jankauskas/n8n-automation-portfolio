# Execution evidence — Airtable lead CRM real-token run (portfolio 13)

Runner: LOCAL Claude Code. UTC 2026-10-24T12:35:41Z → 2026-10-24T12:37:30Z. Baseline private repository main @ f5b3db0a8dfb58720fcd037dbf8a434ceed77ef8.
Base appHNWJpExA6nWZks / table tbly57I1Q5JJUyMlP. Token used only via --secret-file; never printed.

| Run | Fixture | UTC start → end | wrapper exit | step exits | leak_check | secret_dir_removed | egress | mock requests | Summarize CRM | Upsert errors |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | default | 2026-10-24T12:36:18.809925Z → 2026-10-24T12:36:30.921558Z | 0 | all 14 = 0 | PASS | true | api.airtable.com:443: allow 3 close 3 | GET /leads 200 | created 3 / updated 0 | none |
| 2 | default | 2026-10-24T12:36:36.800693Z → 2026-10-24T12:36:48.607046Z | 0 | all 14 = 0 | PASS | true | api.airtable.com:443: allow 3 close 3 | GET /leads 200 | created 0 / updated 3 | none |
| 3 | partial | 2026-10-24T12:36:54.629528Z → 2026-10-24T12:37:06.740589Z | 0 | all 14 = 0 | PASS | true | api.airtable.com:443: allow 4 close 4 | GET /leads 200 | created 0 / updated 3 | L7 eve@example.com 422 |

| Read-back | sha256 | leads |
|---|---|---|
| r0 | a23a029aa8f0225f2d4c467cdbb3379d521af093a5ba2423ab984d5350a4273a | 0 |
| r1 | db9cda810b80b402ba51241117128f045f887a6c7226cad052d9870f4e6eab37 | 3 |
| r2 | db9cda810b80b402ba51241117128f045f887a6c7226cad052d9870f4e6eab37 | 3 |
| r3 | db9cda810b80b402ba51241117128f045f887a6c7226cad052d9870f4e6eab37 | 3 |

## Acceptance (task contract)
| Item | Result |
|---|---|
| Every run: wrapper exit 0, leak_check PASS, secret_dir_removed true | PASS |
| Every run: only api.airtable.com:443 allowed (≥1 allow), no other allow/deny | PASS |
| Every run: mock = GET /leads 200 only | PASS |
| Every run: Build Error Report = L5; Collect Rejected = L3 duplicate + L4 invalid_email | PASS |
| r0 = {"leads": []} | PASS |
| r1 JSON-equal to expected (L1 Ana hot, L2 Ben cold, L6 Dee hot) | PASS |
| Summarize run 1 = created 3 / updated 0, hot 2 cold 1, ana/ben/dee | PASS |
| r2 byte-identical to r1; Summarize run 2 = created 0 / updated 3 | PASS |
| Run 3: Upsert Error Report = L7 eve@example.com 422 INVALID_MULTIPLE_CHOICE_OPTIONS; r3 byte-identical to r1; Summarize created 0 / updated 3 | PASS |
| No apc- containers/networks after any run | PASS |
