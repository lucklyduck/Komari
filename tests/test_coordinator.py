"""Tests for per-entry coordination and device topology."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

import pytest
from custom_components.komari.const import (
    DEFAULT_SCAN_INTERVAL,
    METHOD_LATEST_STATUS,
    METHOD_PUBLIC_NODES,
)
from custom_components.komari.coordinator import (
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
    KomariPermissionError,
)
from custom_components.komari.models import (
    KomariBootstrapData,
    KomariNode,
    KomariNodeStatus,
    KomariPanelInfo,
    parse_nodes,
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
    *, panel_name: str = "Panel A", nodes: Mapping[str, KomariNode] | None = None
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
        capabilities=frozenset({METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES}),
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
        self.bootstrap_calls = 0
        self.nodes_calls = 0
        self.statuses_calls = 0

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


def _coordinator(
    hass: HomeAssistant,
    client: FakeKomariClient,
    *,
    title: str = "Panel A",
) -> KomariDataUpdateCoordinator:
    """Create a coordinator without loading entity platforms."""
    entry = MockConfigEntry(
        domain="komari",
        title=title,
        data={CONF_URL: "https://example.test", CONF_VERIFY_SSL: True},
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
