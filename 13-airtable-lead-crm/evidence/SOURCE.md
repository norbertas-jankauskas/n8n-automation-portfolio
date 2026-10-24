# Evidence provenance

This evidence comes from an internal execution record in a private repository. All lead data is synthetic and the lead source is mocked. The writes went to a real, temporary Airtable base that was deleted after the test.

- **2026-10-24 UTC:** three real Airtable runs (`run-1` and `run-2` with the default fixture; `run-3` with the partial fixture) and four table read-backs, recorded in `run-1.receipt.json` through `run-3.receipt.json` and `r0.json` through `r3.json`. See [SUMMARY.md](SUMMARY.md) for the recorded results.

## Pinned runtime

- n8n **2.41.7**: `n8nio/n8n@sha256:bbf92723aa3eb7129fff2ca760b46aa1ff267779a462a37bc0a3fc8bf47bff8f`.
- Mock/proxy image: `python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe`.

## SHA-256 of files used

Paths are relative to this project folder.

| File | SHA-256 |
| --- | --- |
| `workflow.json` | `e957998b0d0a924f6e4bcb9a3a01ad7ec9c61afda4ad60e7a457fde4029dfa11` |
| `allowlist.json` | `aa0a21df06a282a52e8eb784835ab9b5ec9b6d7f00e704f19211ed96ec226bd1` |
| `mock/mock_lead_crm_local.py` | `1732a758a126159c3de810cc67fbf2fbdd0dbb7832d06d712e273972592fe074` |
| `boundary/egress_proxy.py` | `365aa47aa866224fc295c4331d66d0a9c32317f0571599e6f292701b1fc325ca` |
| `tools/airtable_readback.py` | `99aa278193823befa14325273e84383b2693f041955a38c4d6e2de8d17ebeb9b` |

## Sanitization and independent verification

In the three receipt files and `SUMMARY.md` only, the private host run-directory prefix was replaced with `RUN_DIR/` and any local home directory with `~`, to avoid publishing local machine paths. In `SUMMARY.md` only, an internal task identifier in the title was replaced with "Execution evidence", an internal issue reference with "task contract", and the internal repository name with "private repository". Paths in the receipts under the n8n container user's home directory are container-internal paths.

The receipts' `.secret-…/cred.json` mount is the temporary credential directory, deleted after each run (`secret_dir_removed: true`). Its credential contents were never published here.

The `r0.json`–`r3.json`, proxy-events, and n8n-report-items files are byte-identical to the originals. The four read-backs are direct reads of the Airtable table by a separate trusted script, [`tools/airtable_readback.py`](../tools/airtable_readback.py), not by n8n. `r1.json`, `r2.json`, and `r3.json` share SHA-256 `db9cda810b80b402ba51241117128f045f887a6c7226cad052d9870f4e6eab37`; `r0.json` has SHA-256 `a23a029aa8f0225f2d4c467cdbb3379d521af093a5ba2423ab984d5350a4273a`.

Proxy counts measure **tunnels** (HTTP CONNECTs through the allowlisting proxy), not HTTP requests.

## Test helper provenance

`tests/reference/` holds helper copies from projects 10 and 12: the lead router workflow and its Python reference model, the Supabase mock and its test helpers, plus `jobber-kpi-accepted.workflow.json`. These copies are unmodified except for path expressions in the two Python helpers, adjusted for this project layout.
