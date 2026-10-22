# Evidence provenance

This evidence comes from an internal execution record in a private repository. All invoice data is synthetic; both Jobber and Supabase are mocked. The fix was not re-run against live Supabase because the temporary project was deleted.

- **2026-10-22 UTC:** isolated default fixture run, 11:44:26–11:44:44, recorded in [default.receipt.json](default.receipt.json).
- **2026-10-22 UTC:** isolated partial fixture run, 11:44:49–11:45:04, recorded in [partial.receipt.json](partial.receipt.json).

Each fixture used two inner n8n runs against one shared mock process. Both wrapper exits and all four n8n exits were 0; both fixtures were idempotent.

## Pinned runtime and network isolation

- n8n **2.41.7**: `n8nio/n8n@sha256:bbf92723aa3eb7129fff2ca760b46aa1ff267779a462a37bc0a3fc8bf47bff8f`.
- Mock image: `python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe`.
- The mock container used `--network=none`; the n8n container used `--network=container:<mock>`, sharing the mock's no-network namespace. No external network was available.

## SHA-256 of files used

Paths are relative to the follow-up folder. Values were verified locally with `sha256sum`.

| File | SHA-256 |
| --- | --- |
| `workflow.json` | `b0ee197a5adecb470d34ecf481463df32d0dace6cd495d0ac9261600ad458792` |
| `allowlist.json` | `51d91c34bdbd581ba63c35857167aee2171b8449128c87c470c7f66213f5b76a` |
| `mock/mock_jobber_supabase_local.py` | `fcf929ead456f072b53c1803ae335322b4b7b7b5f3aa50de7bc2562211b5f6ef` |

## Sanitization and retained outputs

Only the two receipt files were sanitized: three lines in each (six lines total) had the private host run-directory prefix replaced with `RUN_DIR/`. A rule to replace any remaining local home directory with `~` found nothing else to replace. Nothing else changed in the receipts. The `/home/node` paths are internal to the n8n container.

All eight request-log and n8n-stdout files are byte-identical to the originals: `default.requests_1.jsonl`, `default.requests_2.jsonl`, `default.n8n_1.stdout`, `default.n8n_2.stdout`, and the four corresponding `partial` files.
