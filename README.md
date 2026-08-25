# Komari for Home Assistant

This repository contains an in-development Home Assistant custom integration for
monitoring one or more Komari panels. Each Home Assistant config entry represents
one panel; the panel is exposed as a service device and every Komari node is
exposed as a child device.

The current implementation target is the P1 MVP described in:

- [Integration design](KOMARI_HOME_ASSISTANT_INTEGRATION_PLAN.md)
- [Implementation plan](KOMARI_HOME_ASSISTANT_IMPLEMENTATION_PLAN.md)

## Development environment

The authoritative checkout remains on Windows. Git commands are run with Windows
Git, while Python, Home Assistant, and tests run inside `Ubuntu-22.04` on WSL2.
The virtual environment and Home Assistant runtime data are stored in the WSL
filesystem, not under `/mnt/c`.

From WSL, enter this repository and bootstrap the locked environment:

```bash
cd /mnt/c/Users/Elecat/Documents/Code/Python/HomeAssistance
bash script/bootstrap
```

Quality checks:

```bash
bash script/lint
bash script/type-check
bash script/test
```

Start a development Home Assistant instance:

```bash
bash script/develop
```

The development UI is normally available at <http://localhost:8123>. If WSL
localhost forwarding is unavailable, use the address reported by
`hostname -I`.

## Current scope

P1 covers RPC2 communication, UI configuration, independent multi-panel config
entries, panel and node devices, and core node metrics. Expiry/traffic limits,
Ping tasks, dynamic metrics, full options, diagnostics, repairs, and HACS release
metadata are planned for later phases.
