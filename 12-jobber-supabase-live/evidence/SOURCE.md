# Evidence provenance

This evidence comes from an internal execution record in a private repository. All invoice data is synthetic and Jobber is mocked. The writes went to a real, temporary Supabase project that was deleted after the test.

- **2026-10-18 UTC:** egress isolation probe, recorded in [egress-probe.receipt.json](egress-probe.receipt.json).
- **2026-10-20 UTC:** four real Supabase runs and five table read-backs, recorded in `run-1.receipt.json` through `run-4.receipt.json` and `r0.json` through `r4.json`. See [SUMMARY.md](SUMMARY.md) for the recorded results.

## Pinned runtime

- n8n **2.41.7**: `n8nio/n8n@sha256:bbf92723aa3eb7129fff2ca760b46aa1ff267779a462a37bc0a3fc8bf47bff8f`.
- Mock/proxy image: `python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe`.

## SHA-256 of files used

Paths are relative to this project folder.

| File | SHA-256 |
| --- | --- |
| `workflow.json` | `3a8be3301cc9ff5d7a38e8f9ac5a668edb85c2a6ea2c091ab56df88f7466fae7` |
| `allowlist.json` | `176514bafd67012c53ab46b5c6472a175fd4fa0edf0a17f30d2e6b294d64d2d7` |
| `mock/mock_jobber_supabase_local.py` | `9d8d6ca6ccb514911f025e9c8455160b9ec47187e1d01cff6634facf38d11d7a` |
| `boundary/egress_proxy.py` | `365aa47aa866224fc295c4331d66d0a9c32317f0571599e6f292701b1fc325ca` |

## Sanitization and independent verification

In the five receipt files and `SUMMARY.md` only, the private host run-directory prefix was replaced with `RUN_DIR/` and the local home directory with `~`, to avoid publishing local machine paths. In `SUMMARY.md`, an internal task identifier in the title was also replaced with the neutral word "Test". The `/home/node` paths in receipts are the n8n container's internal paths.

The `r0.json`–`r4.json` files and all error-report and proxy-summary JSON files are byte-identical to the originals. The five read-backs are direct reads of the Supabase tables by a separate trusted script, not by n8n. Their SHA-256 values are listed in `SUMMARY.md`; `r1.json`, `r2.json`, and `r4.json` have identical hashes.
