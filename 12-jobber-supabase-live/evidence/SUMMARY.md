# Test evidence summary

Real Supabase run (project `dgmzseviicodcvoqtybw`, synthetic data only) inside the egress boundary.
Baseline `a52777b838e5e16238ca575031498dd4116c7858`. Executor: LOCAL Claude Code. Key never printed.

## Runs

| Run | run_id | fixture | UTC start | UTC end | exit | leak_check | secret_dir_removed | egress (allow/deny/close) | only target | Jobber sequence |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `b6d6462f5261` | default | 2026-10-20T14:22:12.701212Z | 2026-10-20T14:22:27.026694Z | 0 | PASS | True | 6/0/6 | True | null->200, c2->429, null->200, c2->200, c3->200 |
| 2 | `dd8be62ad921` | default | 2026-10-20T14:23:11.819243Z | 2026-10-20T14:23:23.217760Z | 0 | PASS | True | 6/0/6 | True | null->200, c2->429, null->200, c2->200, c3->200 |
| 3 | `191701f255cb` | partial | 2026-10-20T14:23:37.895747Z | 2026-10-20T14:23:49.362066Z | 0 | PASS | True | 7/0/7 | True | null->200, c2->429, null->200, c2->200, c3->200 |
| 4 | `d82f0363986c` | default | 2026-10-20T14:24:08.847684Z | 2026-10-20T14:24:20.265423Z | 0 | PASS | True | 6/0/6 | True | null->200, c2->429, null->200, c2->200, c3->200 |

## Read-backs

| File | sha256 |
|---|---|
| r0.json | `f8f656ff20c03f1588439c0ab2f39c46f15a24370e65f96302e9e992a1bf7b65` |
| r1.json | `1b9816a5eea89cc33febf643ba9d5aa4f39a1521f6f7c6a400adea8453bb9e5d` |
| r2.json | `1b9816a5eea89cc33febf643ba9d5aa4f39a1521f6f7c6a400adea8453bb9e5d` |
| r3.json | `7f6b30d6cad574185fa161bd8e21c904617d453ed3e98b5477ac423b3639ed79` |
| r4.json | `1b9816a5eea89cc33febf643ba9d5aa4f39a1521f6f7c6a400adea8453bb9e5d` |

## Acceptance

| Item | Result |
|---|---|
| r0 empty before runs | ✅ |
| All runs: exit 0 | ✅ |
| All runs: leak_check PASS, secret_dir_removed true | ✅ |
| All runs: egress_allowed_only_target true, ≥1 allow, no allow to other hosts, 0 deny | ✅ |
| All runs: Jobber sequence null→200, c2→429, null→200, c2→200, c3→200 | ✅ |
| All runs: Build Error Report = INV-4 only (invalid_total) | ✅ |
| No apc- containers/networks after every run | ✅ |
| r1 invoices and kpi_weekly exactly as specified | ✅ |
| r2 == r1 byte-identical | ✅ |
| Run 3: Build Upsert Error Report = INV-6 only, HTTP 400 (23514 CHECK) | ✅ |
| r3 invoices == r1; W41 unchanged; W42 = 20099999/2/0/10049999 (documented limitation) | ✅ |
| r4 == r1 byte-identical | ✅ |
| Key-prefix grep over evidence/ prints nothing | ✅ |
