# Changelog

All notable changes to Komari for Home Assistant are documented in this file.

The project follows Semantic Versioning while it remains a custom integration. Versions
before `1.0.0` may still introduce breaking changes, which will be called out explicitly in
the release notes.

## [0.3.0-rc.1] - 2026-09-07

### Added

- F16 dynamic Metric discovery, bounded four-hour `last` queries, localized names, exact
  tag filters, node-scoped entities, repairs and redacted diagnostics.
- Local fake-panel fixtures and smoke controls for empty, malformed, high-cardinality,
  method-missing, branch-failure and authentication Metric responses.
- A compatibility evidence record that separates automated checks from pending real-panel
  and HACS lifecycle validation.

### Fixed

- Reauthentication tests now await only the affected Entry reload and leave no reload task.
- Confirmed-removed nodes and Ping tasks can recreate their original entity identities when
  the same remote ID returns.
- Temporary Ping capability or inventory loss no longer clears saved task/statistic choices.
- The legacy temperature value `0` is consistently treated as Komari's absent-temperature
  sentinel; only finite values in `(0, 200]` create temperature entities.

### Upgrade notes

- Dynamic Metrics are opt-in and disabled by default. Do not downgrade an Entry with saved
  dynamic-Metric choices to `0.2.0-alpha.1` without first backing up and clearing them.
- `v0.3.0` has not been released yet. Real compatibility and HACS lifecycle evidence remains
  a release requirement; the 24-hour soak is explicitly outside this release gate.

## [0.2.0-alpha.1] - 2026-08-30

### Added

- Per-panel polling, Ping, expiry and traffic options with independent reload behavior.
- Expiry, renewal, traffic-limit and reset-sensitive traffic usage entities.
- Selectable Ping tasks with latest, loss, min, max, average, percentile and standard
  deviation statistics.
- Authoritative node and Ping-task lifecycle tracking with entry-scoped registry cleanup.
- Reauthentication, URL/TLS reconfiguration, redacted diagnostics and repair issues.
- Three-panel fake-server acceptance coverage for shared remote IDs and partial failures.
- Local Komari icon and logo assets.
- HACS repository metadata plus HACS, Hassfest, Ruff, Pyright and Pytest validation.

### Changed

- Raised the tested Home Assistant baseline to `2026.8.2` and Python to `3.14.x`.
- Aligned Device Registry handling with the Home Assistant 2026.8 APIs.
- Expanded the user and developer documentation for installation, configuration and
  release validation.

### Fixed

- Unsupported GPU and invalid temperature data no longer create misleading entities.
- Multi-panel devices and entities remain isolated when panels reuse node or task IDs.

## [0.1.0-alpha.1] - 2026-08-26

- Initial multi-panel Home Assistant custom-integration MVP.

[0.3.0-rc.1]: https://github.com/lucklyduck/Komari/releases/tag/v0.3.0-rc.1
[0.2.0-alpha.1]: https://github.com/lucklyduck/Komari/compare/v0.1.0-alpha.1...v0.2.0-alpha.1
[0.1.0-alpha.1]: https://github.com/lucklyduck/Komari/releases/tag/v0.1.0-alpha.1
