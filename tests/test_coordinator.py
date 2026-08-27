"""Tests for per-entry coordination and device topology."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest
from custom_components.komari.const import (
    CONF_PING_INTERVAL,
    CONF_PING_TASK_IDS,
    DEFAULT_SCAN_INTERVAL,
    METHOD_COMMON_RECORDS,
    METHOD_LATEST_STATUS,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_RECORDS,
    METHOD_PUBLIC_PING_TASKS,
)
from custom_components.komari.coordinator import (
    BRANCH_CAPABILITIES,
    BRANCH_PING_STATS,
    BRANCH_PING_TASKS,
    KomariDataUpdateCoordinator,
    KomariSnapshot,
)
from custom_components.komari.device import (
    get_node_device,
    panel_identifier,
    register_devices,
    remove_node_device,
)
from custom_components.komari.exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariMethodNotFoundError,
    KomariPermissionError,
    KomariProtocolError,
)
from custom_components.komari.models import (
    KomariBootstrapData,
    KomariNode,
    KomariNodeStatus,
    KomariPanelInfo,
    KomariPingSeriesKey,
    KomariPingStats,
    KomariPingTask,
    parse_nodes,
    parse_ping_metric_stats,
    parse_ping_tasks,
    parse_statuses,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.komari.api import KomariApiClient
    from custom_components.komari.data import KomariConfigEntry
    from homeassistant.core import HomeAssistant


def _nodes(*names: str) -> Mapping[str, KomariNode]:
    """Create sanitized node data with deterministic UUIDs."""
    return parse_nodes(
        [
            {
                "uuid": f"node-{index}",
                "name": name,
                "cpu_name": "Virtual CPU",
                "version": "1.0.0",
            }
            for index, name in enumerate(names, start=1)
        ]
    )


def _statuses(*online_states: bool) -> Mapping[str, KomariNodeStatus]:
    """Create sanitized latest status records."""
    return parse_statuses(
        [
            {
                "client": f"node-{index}",
                "online": online,
                "time": "2026-08-26T00:00:00Z",
                "cpu": 0,
            }
            for index, online in enumerate(online_states, start=1)
        ]
    )


def _bootstrap(
    *,
    panel_name: str = "Panel A",
    nodes: Mapping[str, KomariNode] | None = None,
    capabilities: frozenset[str] | None = None,
) -> KomariBootstrapData:
    """Create initial data for one fake panel."""
    panel_nodes = nodes or _nodes("Node A")
    return KomariBootstrapData(
        panel=KomariPanelInfo(
            name=panel_name,
            version="1.2.3",
            build_hash="sanitized-build",
            rpc_version="2.0",
            private=False,
        ),
        nodes=panel_nodes,
        statuses=_statuses(*([True] * len(panel_nodes))),
        capabilities=capabilities
        or frozenset({METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES}),
    )


class FakeKomariClient:
    """Controllable async client used without network access."""

    def __init__(self, bootstrap: KomariBootstrapData) -> None:
        """Initialize fake responses."""
        self.bootstrap = bootstrap
        self.nodes = bootstrap.nodes
        self.statuses = bootstrap.statuses
        self.bootstrap_error: Exception | None = None
        self.nodes_error: Exception | None = None
        self.statuses_error: Exception | None = None
        self.ping_tasks: Mapping[str, KomariPingTask] = parse_ping_tasks(
            [
                {
                    "id": 101,
                    "name": "Fixture task",
                    "clients": ["node-1"],
                    "type": "http",
                    "interval": 30,
                }
            ]
        )
        self.ping_stats: Mapping[KomariPingSeriesKey, KomariPingStats] = (
            parse_ping_metric_stats(
                {
                    "stats": [
                        {
                            "entity_id": "node-1",
                            "task_id": 101,
                            "latest": 12,
                            "loss": 0,
                            "total": 10,
                            "valid": 10,
                        }
                    ]
                }
            )
        )
        self.metadata = (bootstrap.panel, bootstrap.capabilities)
        self.ping_tasks_error: Exception | None = None
        self.ping_stats_error: Exception | None = None
        self.metadata_error: Exception | None = None
        self.bootstrap_calls = 0
        self.nodes_calls = 0
        self.statuses_calls = 0
        self.ping_tasks_calls = 0
        self.ping_stats_calls = 0
        self.metadata_calls = 0
        self.last_ping_request: dict[str, object] | None = None

    async def async_get_bootstrap(self) -> KomariBootstrapData:
        """Return or fail the bootstrap request."""
        self.bootstrap_calls += 1
        if self.bootstrap_error is not None:
            raise self.bootstrap_error
        return self.bootstrap

    async def async_get_nodes(
        self, capabilities: frozenset[str]
    ) -> Mapping[str, KomariNode]:
        """Return or fail an inventory request."""
        assert METHOD_PUBLIC_NODES in capabilities
        self.nodes_calls += 1
        if self.nodes_error is not None:
            raise self.nodes_error
        return self.nodes

    async def async_get_latest_status(self) -> Mapping[str, KomariNodeStatus]:
        """Return or fail a status request."""
        self.statuses_calls += 1
        if self.statuses_error is not None:
            raise self.statuses_error
        return self.statuses

    async def async_get_ping_tasks(
        self, capabilities: frozenset[str]
    ) -> Mapping[str, KomariPingTask]:
        """Return or fail public Ping task discovery."""
        assert METHOD_PUBLIC_PING_TASKS in capabilities
        self.ping_tasks_calls += 1
        if self.ping_tasks_error is not None:
            raise self.ping_tasks_error
        return self.ping_tasks

    async def async_get_ping_stats(
        self,
        capabilities: frozenset[str],
        node_uuids: tuple[str, ...],
        task_ids: tuple[str, ...],
        *,
        hours: int,
        max_points: int,
    ) -> Mapping[KomariPingSeriesKey, KomariPingStats]:
        """Return or fail one batch Ping statistics request."""
        assert capabilities & {
            METHOD_PING_METRIC_STATS,
            METHOD_PUBLIC_PING_RECORDS,
            METHOD_COMMON_RECORDS,
        }
        self.ping_stats_calls += 1
        self.last_ping_request = {
            "node_uuids": node_uuids,
            "task_ids": task_ids,
            "hours": hours,
            "max_points": max_points,
        }
        if self.ping_stats_error is not None:
            raise self.ping_stats_error
        return self.ping_stats

    async def async_get_metadata(
        self,
    ) -> tuple[KomariPanelInfo, frozenset[str]]:
        """Return or fail capability metadata refresh."""
        self.metadata_calls += 1
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata


def _coordinator(
    hass: HomeAssistant,
    client: FakeKomariClient,
    *,
    title: str = "Panel A",
    options: dict[str, object] | None = None,
) -> KomariDataUpdateCoordinator:
    """Create a coordinator without loading entity platforms."""
    entry = MockConfigEntry(
        domain="komari",
        title=title,
        data={CONF_URL: "https://example.test", CONF_VERIFY_SSL: True},
        options=options or {},
    )
    return KomariDataUpdateCoordinator(
        hass,
        cast("KomariConfigEntry", entry),
        cast("KomariApiClient", client),
    )


async def test_coordinator_bootstrap_and_status_cadence(
    hass: HomeAssistant,
) -> None:
    """Bootstrap runs once and normal cycles request only current status."""
    client = FakeKomariClient(_bootstrap())
    coordinator = _coordinator(hass, client)

    first = await coordinator._async_update_data()
    client.statuses = _statuses(False)
    second = await coordinator._async_update_data()

    assert coordinator.update_interval == DEFAULT_SCAN_INTERVAL
    assert first.statuses["node-1"].online is True
    assert second.statuses["node-1"].online is False
    assert client.bootstrap_calls == 1
    assert client.statuses_calls == 1
    assert client.nodes_calls == 0


async def test_coordinator_refreshes_due_inventory(hass: HomeAssistant) -> None:
    """A due inventory refresh replaces metadata and discovers new nodes."""
    client = FakeKomariClient(_bootstrap())
    coordinator = _coordinator(hass, client)
    await coordinator._async_update_data()

    client.nodes = _nodes("Node A", "Node B")
    client.statuses = _statuses(True, True)
    coordinator._last_inventory_attempt = -1_000_000
    updated = await coordinator._async_update_data()

    assert set(updated.nodes) == {"node-1", "node-2"}
    assert client.nodes_calls == 1


async def test_node_requires_three_successful_inventory_misses(
    hass: HomeAssistant,
) -> None:
    """A node remains visible until three authoritative list omissions."""
    initial_nodes = _nodes("Node A", "Node B")
    client = FakeKomariClient(_bootstrap(nodes=initial_nodes))
    coordinator = _coordinator(hass, client)
    await coordinator._async_update_data()
    client.nodes = _nodes("Node A")

    for expected_misses in (1, 2):
        coordinator._last_inventory_attempt = -1_000_000
        retained = await coordinator._async_update_data()
        assert "node-2" in retained.nodes
        assert coordinator.node_missing_counts == {"node-2": expected_misses}

    coordinator._last_inventory_attempt = -1_000_000
    removed = await coordinator._async_update_data()
    assert set(removed.nodes) == {"node-1"}
    assert coordinator.node_missing_counts == {}


async def test_node_reappearance_resets_count_and_failure_does_not_increment(
    hass: HomeAssistant,
) -> None:
    """Only a successful authoritative list can change absence counters."""
    initial_nodes = _nodes("Node A", "Node B")
    client = FakeKomariClient(_bootstrap(nodes=initial_nodes))
    coordinator = _coordinator(hass, client)
    await coordinator._async_update_data()

    client.nodes = _nodes("Node A")
    coordinator._last_inventory_attempt = -1_000_000
    await coordinator._async_update_data()
    assert coordinator.node_missing_counts == {"node-2": 1}

    client.nodes_error = KomariConnectionError("sanitized")
    coordinator._last_inventory_attempt = -1_000_000
    failed = await coordinator._async_update_data()
    assert "node-2" in failed.nodes
    assert coordinator.node_missing_counts == {"node-2": 1}

    client.nodes_error = None
    client.nodes = initial_nodes
    coordinator._last_inventory_attempt = -1_000_000
    returned = await coordinator._async_update_data()
    assert "node-2" in returned.nodes
    assert coordinator.node_missing_counts == {}


async def test_inventory_failure_keeps_old_nodes_and_new_status(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inventory failure is non-fatal and does not expose exception details."""
    client = FakeKomariClient(_bootstrap())
    coordinator = _coordinator(hass, client)
    initial = await coordinator._async_update_data()

    client.statuses = _statuses(False)
    client.nodes_error = KomariConnectionError(
        "secret-token at 192.0.2.10 must never be logged"
    )
    coordinator._last_inventory_attempt = -1_000_000
    with caplog.at_level(logging.WARNING):
        updated = await coordinator._async_update_data()

    assert updated.nodes is initial.nodes
    assert updated.statuses["node-1"].online is False
    assert "keeping prior inventory" in caplog.text
    assert "secret-token" not in caplog.text
    assert "192.0.2.10" not in caplog.text


async def test_status_failure_fails_only_this_coordinator(
    hass: HomeAssistant,
) -> None:
    """A status failure is a coordinator update failure."""
    client = FakeKomariClient(_bootstrap())
    coordinator = _coordinator(hass, client)
    await coordinator._async_update_data()
    client.statuses_error = KomariConnectionError("offline")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.parametrize(
    "error",
    [KomariAuthenticationError("invalid"), KomariPermissionError(-1, "denied")],
)
async def test_bootstrap_auth_failure_requests_reauth(
    hass: HomeAssistant, error: Exception
) -> None:
    """Authentication failures use Home Assistant's reauth signal."""
    client = FakeKomariClient(_bootstrap())
    client.bootstrap_error = error

    with pytest.raises(ConfigEntryAuthFailed):
        await _coordinator(hass, client)._async_update_data()


async def test_status_and_inventory_auth_failures_request_reauth(
    hass: HomeAssistant,
) -> None:
    """Authentication errors from either recurring endpoint trigger reauth."""
    status_client = FakeKomariClient(_bootstrap())
    status_coordinator = _coordinator(hass, status_client)
    await status_coordinator._async_update_data()
    status_client.statuses_error = KomariAuthenticationError("invalid")
    with pytest.raises(ConfigEntryAuthFailed):
        await status_coordinator._async_update_data()

    inventory_client = FakeKomariClient(_bootstrap())
    inventory_coordinator = _coordinator(hass, inventory_client)
    await inventory_coordinator._async_update_data()
    inventory_client.nodes_error = KomariPermissionError(-1, "denied")
    inventory_coordinator._last_inventory_attempt = -1_000_000
    with pytest.raises(ConfigEntryAuthFailed):
        await inventory_coordinator._async_update_data()


def test_inventory_due_without_prior_attempt(hass: HomeAssistant) -> None:
    """A coordinator without an inventory timestamp treats it as due."""
    coordinator = _coordinator(hass, FakeKomariClient(_bootstrap()))
    assert coordinator._inventory_is_due(0) is True


async def test_multicadence_bootstrap_batches_selected_ping_stats(
    hass: HomeAssistant,
) -> None:
    """Initial optional branches load once and preserve the locked request shape."""
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            METHOD_PING_METRIC_STATS,
        }
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: ["101"], CONF_PING_INTERVAL: 60},
    )

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert tuple(first.ping_tasks) == ("101",)
    assert len(first.ping_stats) == 1
    assert client.ping_tasks_calls == 1
    assert client.ping_stats_calls == 1
    assert client.last_ping_request == {
        "node_uuids": ("node-1",),
        "task_ids": ("101",),
        "hours": 1,
        "max_points": 120,
    }
    assert second.statuses == client.statuses
    assert coordinator.branch_states[BRANCH_PING_TASKS].fetched_at is not None
    assert coordinator.branch_states[BRANCH_PING_STATS].fetched_at is not None
    assert coordinator.ping_stats_available is True


async def test_ping_task_requires_three_successful_task_list_misses(
    hass: HomeAssistant,
) -> None:
    """Task disappearance uses its own authoritative three-miss counter."""
    capabilities = frozenset(
        {METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES, METHOD_PUBLIC_PING_TASKS}
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    client.ping_tasks = parse_ping_tasks(
        [
            {"id": 101, "name": "Task one"},
            {"id": 202, "name": "Task two"},
        ]
    )
    coordinator = _coordinator(hass, client)
    initial = await coordinator._async_update_data()
    assert set(initial.ping_tasks) == {"101", "202"}

    client.ping_tasks = parse_ping_tasks([{"id": 101, "name": "Task one"}])
    for expected_misses in (1, 2):
        coordinator._last_ping_tasks_attempt = -1_000_000
        retained = await coordinator._async_update_data()
        assert "202" in retained.ping_tasks
        assert coordinator.ping_task_missing_counts == {"202": expected_misses}

    coordinator._last_ping_tasks_attempt = -1_000_000
    removed = await coordinator._async_update_data()
    assert set(removed.ping_tasks) == {"101"}
    assert coordinator.ping_task_missing_counts == {}


async def test_ping_task_method_failure_retains_task_without_counting(
    hass: HomeAssistant,
) -> None:
    """Method discovery failure is not an authoritative empty task list."""
    capabilities = frozenset(
        {METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES, METHOD_PUBLIC_PING_TASKS}
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    coordinator = _coordinator(hass, client)
    initial = await coordinator._async_update_data()
    client.ping_tasks_error = KomariMethodNotFoundError(-32601, "unavailable")
    coordinator._last_ping_tasks_attempt = -1_000_000

    retained = await coordinator._async_update_data()

    assert retained.ping_tasks == initial.ping_tasks
    assert coordinator.ping_task_missing_counts == {}
    assert METHOD_PUBLIC_PING_TASKS not in retained.capabilities


@pytest.mark.parametrize(
    ("nodes", "task_ids", "fallback_method"),
    [
        (_nodes("Node A", "Node B"), ["101"], METHOD_PUBLIC_PING_RECORDS),
        (
            _nodes("Node A", "Node B"),
            ["101", "202"],
            METHOD_COMMON_RECORDS,
        ),
    ],
)
async def test_coordinator_loads_capability_confirmed_ping_fallbacks(
    hass: HomeAssistant,
    nodes: Mapping[str, KomariNode],
    task_ids: list[str],
    fallback_method: str,
) -> None:
    """Selected Ping data loads through one compatible records capability."""
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            fallback_method,
        }
    )
    client = FakeKomariClient(_bootstrap(nodes=nodes, capabilities=capabilities))
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: task_ids},
    )

    snapshot = await coordinator._async_update_data()

    assert client.ping_stats_calls == 1
    assert len(snapshot.ping_stats) == 1
    assert coordinator.ping_stats_available is True
    assert snapshot.branch_states[BRANCH_PING_STATS].error_category is None


async def test_coordinator_rejects_ping_fallback_that_requires_fanout(
    hass: HomeAssistant,
) -> None:
    """Public records alone does not trigger per-node or per-task requests."""
    nodes = _nodes("Node A", "Node B")
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            METHOD_PUBLIC_PING_RECORDS,
        }
    )
    client = FakeKomariClient(_bootstrap(nodes=nodes, capabilities=capabilities))
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: ["101", "202"]},
    )

    snapshot = await coordinator._async_update_data()

    assert client.ping_stats_calls == 0
    assert snapshot.ping_stats == {}
    assert coordinator.ping_stats_available is False
    assert snapshot.branch_states[BRANCH_PING_STATS].error_category == "unsupported"


async def test_coordinator_filters_unselected_and_invisible_ping_series(
    hass: HomeAssistant,
) -> None:
    """The coordinator enforces visible-node and selected-task intersections."""
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            METHOD_PING_METRIC_STATS,
        }
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    client.ping_stats = parse_ping_metric_stats(
        {
            "stats": [
                {
                    "entity_id": "node-1",
                    "task_id": 101,
                    "latest": 10,
                    "loss": 0,
                },
                {
                    "entity_id": "hidden-node",
                    "task_id": 101,
                    "latest": 20,
                    "loss": 0,
                },
                {
                    "entity_id": "node-1",
                    "task_id": 999,
                    "latest": 30,
                    "loss": 0,
                },
            ]
        }
    )
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: ["101"]},
    )

    snapshot = await coordinator._async_update_data()

    assert tuple(snapshot.ping_stats) == (
        KomariPingSeriesKey(node_uuid="node-1", task_id="101"),
    )


async def test_ping_method_not_found_switches_capability_on_next_poll(
    hass: HomeAssistant,
) -> None:
    """Only the failed preferred capability is removed before later fallback."""
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            METHOD_PING_METRIC_STATS,
            METHOD_PUBLIC_PING_RECORDS,
        }
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    client.ping_stats_error = KomariMethodNotFoundError(-32601, "unavailable")
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: ["101"]},
    )

    first = await coordinator._async_update_data()
    assert METHOD_PING_METRIC_STATS not in first.capabilities
    assert METHOD_PUBLIC_PING_RECORDS in first.capabilities

    client.ping_stats_error = None
    coordinator._last_ping_stats_attempt = -1_000_000
    second = await coordinator._async_update_data()

    assert client.ping_stats_calls == 2
    assert second.ping_stats == client.ping_stats
    assert coordinator.ping_stats_available is True


async def test_ping_failure_isolated_with_two_poll_ttl_and_recovery(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two failed Ping polls expire only the Ping branch and later recover."""
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            METHOD_PING_METRIC_STATS,
        }
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: ["101"]},
    )
    initial = await coordinator._async_update_data()
    client.ping_stats_error = KomariConnectionError("secret endpoint detail")

    with caplog.at_level(logging.DEBUG):
        for expected_available in (True, False):
            coordinator._last_ping_stats_attempt = -1_000_000
            updated = await coordinator._async_update_data()
            assert updated.ping_stats is initial.ping_stats
            assert coordinator.ping_stats_available is expected_available
            assert updated.statuses == client.statuses

        client.ping_stats_error = None
        coordinator._last_ping_stats_attempt = -1_000_000
        recovered = await coordinator._async_update_data()

    assert recovered.ping_stats == client.ping_stats
    assert coordinator.ping_stats_available is True
    assert "secret endpoint detail" not in caplog.text
    assert "refresh recovered" in caplog.text


@pytest.mark.parametrize(
    ("branch", "method"),
    [
        (BRANCH_PING_TASKS, METHOD_PUBLIC_PING_TASKS),
        (BRANCH_PING_STATS, METHOD_PING_METRIC_STATS),
    ],
)
async def test_optional_method_not_found_removes_only_that_capability(
    hass: HomeAssistant,
    branch: str,
    method: str,
) -> None:
    """A stale rpc.methods result cannot make the core coordinator fail."""
    capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_NODES,
            METHOD_PUBLIC_PING_TASKS,
            METHOD_PING_METRIC_STATS,
        }
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    error = KomariMethodNotFoundError(-32601, "method unavailable")
    if branch == BRANCH_PING_TASKS:
        client.ping_tasks_error = error
    else:
        client.ping_stats_error = error
    coordinator = _coordinator(
        hass,
        client,
        options={CONF_PING_TASK_IDS: ["101"]},
    )

    snapshot = await coordinator._async_update_data()

    assert method not in snapshot.capabilities
    assert METHOD_LATEST_STATUS in snapshot.capabilities
    assert coordinator.branch_states[branch].error_category == "method_not_found"


async def test_metadata_refresh_retains_prior_data_on_failure_and_maps_auth(
    hass: HomeAssistant,
) -> None:
    """Six-hour metadata is non-fatal except for credential rejection."""
    client = FakeKomariClient(_bootstrap())
    coordinator = _coordinator(hass, client)
    initial = await coordinator._async_update_data()
    refreshed_panel = KomariPanelInfo(
        name="Panel B",
        version="2.0.0",
        build_hash="new-build",
        rpc_version="2.1",
        private=False,
    )
    client.metadata = (refreshed_panel, initial.capabilities)
    coordinator._last_capabilities_attempt = -1_000_000
    refreshed = await coordinator._async_update_data()
    assert refreshed.panel is refreshed_panel
    assert client.metadata_calls == 1

    client.metadata_error = KomariProtocolError("invalid metadata")
    coordinator._last_capabilities_attempt = -1_000_000
    retained = await coordinator._async_update_data()
    assert retained.panel is refreshed_panel
    assert coordinator.branch_states[BRANCH_CAPABILITIES].error_category == "protocol"

    client.metadata_error = KomariAuthenticationError("denied")
    coordinator._last_capabilities_attempt = -1_000_000
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_ping_task_authentication_failure_requests_reauth(
    hass: HomeAssistant,
) -> None:
    """Credential rejection in an optional branch still starts Reauth."""
    capabilities = frozenset(
        {METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES, METHOD_PUBLIC_PING_TASKS}
    )
    client = FakeKomariClient(_bootstrap(capabilities=capabilities))
    client.ping_tasks_error = KomariPermissionError(-1, "denied")
    coordinator = _coordinator(hass, client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


def test_branch_due_helpers_and_scan_option(hass: HomeAssistant) -> None:
    """Each cadence starts due and scan options are panel scoped."""
    coordinator = _coordinator(
        hass,
        FakeKomariClient(_bootstrap()),
        options={"scan_interval": 15},
    )
    assert coordinator.update_interval == timedelta(seconds=15)
    assert coordinator._ping_tasks_are_due(0)
    assert coordinator._ping_stats_are_due(0)
    assert coordinator._capabilities_are_due(0)


def test_register_panel_and_node_devices(hass: HomeAssistant) -> None:
    """Nodes are linked to their entry's service-type panel device."""
    entry = MockConfigEntry(
        domain="komari",
        title="Panel A",
        data={CONF_URL: "https://example.test", CONF_VERIFY_SSL: True},
    )
    entry.add_to_hass(hass)
    snapshot = KomariSnapshot.from_bootstrap(_bootstrap())

    panel = register_devices(
        hass,
        cast("KomariConfigEntry", entry),
        snapshot,
    )
    registry = dr.async_get(hass)
    node = get_node_device(registry, entry.entry_id, "node-1")

    assert panel.entry_type is dr.DeviceEntryType.SERVICE
    assert panel.identifiers == {panel_identifier(entry.entry_id)}
    assert panel.configuration_url == "https://example.test"
    assert node is not None
    assert node.via_device_id == panel.id
    assert node.name == "Node A"
    assert node.config_entry_id == entry.entry_id


def test_same_node_uuid_is_isolated_between_entries(hass: HomeAssistant) -> None:
    """The same remote UUID under two panels creates independent devices."""
    entries = [
        MockConfigEntry(
            domain="komari",
            title=f"Panel {suffix}",
            data={
                CONF_URL: f"https://{suffix.casefold()}.example.test",
                CONF_VERIFY_SSL: True,
            },
        )
        for suffix in ("A", "B")
    ]
    panels = []
    for entry in entries:
        entry.add_to_hass(hass)
        panels.append(
            register_devices(
                hass,
                cast("KomariConfigEntry", entry),
                KomariSnapshot.from_bootstrap(_bootstrap(panel_name=entry.title)),
            )
        )

    registry = dr.async_get(hass)
    nodes = [get_node_device(registry, entry.entry_id, "node-1") for entry in entries]

    assert nodes[0] is not None
    assert nodes[1] is not None
    assert nodes[0].id != nodes[1].id
    assert nodes[0].via_device_id == panels[0].id
    assert nodes[1].via_device_id == panels[1].id

    assert remove_node_device(registry, entries[0].entry_id, "node-1") is True
    assert get_node_device(registry, entries[0].entry_id, "node-1") is None
    assert get_node_device(registry, entries[1].entry_id, "node-1") is not None
    assert remove_node_device(registry, entries[0].entry_id, "node-1") is False
