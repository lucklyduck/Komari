# Komari Home Assistant compatibility evidence

Last updated: 2026-09-07

This file is the release evidence ledger for `v0.3.0-rc.1` and the eventual stable `v0.3.0`.
It deliberately separates local automation from a real Home Assistant/Komari run. A row
marked **Pending** is not evidence of compatibility.

## Evidence levels

| Level | Meaning | Counts as real compatibility? |
| --- | --- | --- |
| Automated | Unit/integration tests and the sanitized local fake panels | No |
| Runtime | An authorized, real Home Assistant and Komari combination | Yes |
| HACS lifecycle | A fresh Home Assistant install/upgrade/uninstall through HACS | Yes |

## Current automated evidence

| Date | Environment | Scope | Result | Evidence level |
| --- | --- | --- | --- | --- |
| 2026-09-06 | WSL local test environment | Core, Ping, F16 models/API/coordinator/options/entities/repairs/diagnostics | Three consecutive runs passed: 244 tests, 95.22% coverage | Automated |
| 2026-09-06 | Three local fake RPC2 panels | Shared IDs, Ping and Metric branch isolation, empty/malformed/high-cardinality Metric responses, method-not-found, key rotation | Passed | Automated |
| 2026-09-07 | GitHub Actions on `6a33abc` | HACS, Hassfest, Ruff, Pyright and Pytest | [Validate passed](https://github.com/lucklyduck/Komari/actions/runs/34049449568) | Automated |

## RC publication

[`v0.3.0-rc.1`](https://github.com/lucklyduck/Komari/releases/tag/v0.3.0-rc.1) was published
as a GitHub Prerelease from the CI-verified `6a33abc` commit on 2026-09-07. It is a release
candidate, not evidence that the real matrix or HACS lifecycle checks below passed.

The fake panels are intentionally not a substitute for a real Komari server or a Home
Assistant UI lifecycle test. They use fixed synthetic node IDs, values and credentials only.

## Required real compatibility matrix

| ID | Home Assistant | Komari | Authentication | Expected optional capability | Status |
| --- | --- | --- | --- | --- | --- |
| R1 | 2026.8.2 | 1.0.7 | Private/API Key | Ping and Metrics may be absent; core must work | Pending |
| R2 | 2026.9.0 | 1.4.3 | Private/API Key | Validate available Ping/Metrics | Pending |
| R3 | 2026.9.0 | 1.4.3 | Anonymous public | Validate public-only capability handling | Pending |
| R4 | 2026.9.0 | Authorized capability-reduced instance | As supplied by the test instance | Missing Ping/Metrics must degrade cleanly | Pending |
| R5 | 2026.9.0 | Authorized full-Metrics instance | As supplied by the test instance | F16 selection, filtering and lifecycle | Pending |

Run this matrix only against an owned panel or one for which explicit authorization has been
granted. Do not record URLs, node UUIDs, API keys, tag values, labels or metric values here.

## Required scenario checklist

For every applicable row, record a result for:

- First setup, duplicate-URL prevention, reload, Home Assistant restart and Komari restart.
- Ten-minute network loss followed by automatic recovery.
- Invalid API Key, Reauth, and proof that only the affected Config Entry reloads.
- Agent offline/online, counter reset, node add/rename, three authoritative misses and same-ID
  recreation.
- Ping task add/delete/recovery and capability disappearance/recovery.
- F16 selection, exact tag filters, missing Definition/series recovery and high-cardinality
  protection.
- No duplicate Device or Entity, no cross-Entry influence and no leftover task/listener/Repair
  after deletion.

## HACS lifecycle checklist

These checks are separate from R1--R5 and remain **Pending** until run in fresh instances.

1. Install `v0.2.0-alpha.1` through a HACS custom repository and configure a private panel.
2. Upgrade to the eventual `v0.3.0-rc.N`; restart and verify Entry data/options, Device and
   Entity identities, history association and F16-default-off behavior.
3. Enable F16, restart and reload; confirm dynamic identities are stable.
4. Install the RC directly into another fresh Home Assistant instance.
5. Delete the Config Entry, uninstall through HACS and restart; verify no listener, task,
   Repair, Device or Entity residue remains.
6. After the stable tag, repeat a fresh install and `alpha.1 -> 0.3.0` upgrade smoke.

An Entry with saved F16 selections has no supported downgrade path to `0.2.0-alpha.1`.
Back up and clear Metric selections before attempting that downgrade.

## Per-run record template

Copy this block for each real run. Replace only the bracketed values with sanitized facts.

```text
Date: [YYYY-MM-DD]
Matrix ID: [R1--R5]
HA version: [version]
Komari version: [version]
Authentication: [private API Key | anonymous public | other authorized mode]
Capabilities: [counts or booleans only]
Scenarios exercised: [checklist item names]
Result: [pass | fail | blocked]
Evidence level: [Runtime | HACS lifecycle]
Sanitized evidence: [test/log/artifact reference without identifiers or values]
Notes: [compatibility behavior only]
```

## Explicit exclusion

The 24-hour soak is not run and is not a `v0.3.0` gate. This file must not be edited to claim
that it passed unless a separately authorized run supplies dated evidence.
