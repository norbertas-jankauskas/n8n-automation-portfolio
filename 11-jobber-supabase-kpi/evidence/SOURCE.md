# Evidence source

- Source: internal execution record (private repository), posted 2026-10-13T20:50:59Z (UTC)
- n8n version: 2.41.7, run in an isolated test instance (its own user folder and ports, no credentials, workflow not activated)
- Executed workflow: `workflow.json` in this folder, git blob SHA `405402cf82f005671c8643eb894a5563df7bef5a` (sha256 `91fb48042d044a338d8e20e3c5e1e8838688b00b4419af138127edaa257d6de6`), byte-identical to the executed file
- `execution-output.json`: the two fenced JSON blocks from that record, combined under two keys: `state_after_run1_equals_run2` (mock Supabase state after run 1, identical after run 2) and `build_error_report` (identical in both runs). The values are unchanged; only the wrapping object and indentation were added. The mock API (`mock/mock_jobber_supabase.py`, blob `c3da7bcfc19ed56eb73c21d588ca8ccc2ec1f298`) ran on localhost.
- All data is synthetic (mock data). External services were mocked or not called.
