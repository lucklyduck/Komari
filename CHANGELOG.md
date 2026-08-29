# Changelog

All notable changes to Komari for Home Assistant are documented in this file.

The project follows Semantic Versioning while it remains a custom integration. Versions
before `1.0.0` may still introduce breaking changes, which will be called out explicitly in
the release notes.

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

[0.2.0-alpha.1]: https://github.com/lucklyduck/Komari/compare/v0.1.0-alpha.1...v0.2.0-alpha.1
[0.1.0-alpha.1]: https://github.com/lucklyduck/Komari/releases/tag/v0.1.0-alpha.1
