"""Integration-style tests for Komari panel and node entities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.komari.binary_sensor import (
    EXPIRY_BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.komari.const import CONF_ENABLE_EXPIRY_ENTITIES, DOMAIN
from custom_components.komari.coordinator import KomariSnapshot
from custom_components.komari.device import get_node_device, get_panel_device
from custom_components.komari.models import (
    KomariBootstrapData,
    KomariNode,
    KomariNodeStatus,
    KomariPanelInfo,
    parse_nodes,
    parse_statuses,
)
from custom_components.komari.sensor import (
    EXPIRY_SENSOR_DESCRIPTIONS,
    NODE_SENSOR_DESCRIPTIONS,
    PANEL_SENSOR_DESCRIPTIONS,
    usage_percentage,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_URL,
    CONF_VERIFY_SSL,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.komari.data import KomariConfigEntry
    from homeassistant.core import HomeAssistant, State

NODE_UUID = "00000000-0000-0000-0000-000000000001"
SECOND_NODE_UUID = "00000000-0000-0000-0000-000000000002"


def _node_payload(
    node_uuid: str = NODE_UUID,
    *,
    name: str = "Example node",
    optional_hardware: bool = True,
) -> dict[str, Any]:
    """Return sanitized node metadata."""
    payload: dict[str, Any] = {
        "uuid": node_uuid,
        "name": name,
        "cpu_name": "Example CPU",
        "version": "1.0.0",
    }
    if optional_hardware:
        payload.update(
            {
                "gpu_name": "Example GPU",
                "swap_total": 2_147_483_648,
            }
        )
    return payload


def _status_payload(
    node_uuid: str = NODE_UUID,
    *,
    online: bool = True,
    optional_metrics: bool = True,
) -> dict[str, Any]:
    """Return a sanitized latest-status record with valid zero values."""
    payload: dict[str, Any] = {
        "client": node_uuid,
        "online": online,
        "time": "2026-08-26T00:00:00Z",
        "cpu": 12.5,
        "ram": 4_294_967_296,
        "ram_total": 8_589_934_592,
        "disk": 53_687_091_200,
        "disk_total": 107_374_182_400,
        "load": 0.25,
        "load5": 0.2,
        "load15": 0.1,
        "net_in": 1024,
        "net_out": 2048,
        "net_total_down": 987_654_321,
        "net_total_up": 123_456_789,
        "process": 123,
        "connections": 10,
        "connections_udp": 2,
    }
    if optional_metrics:
        payload.update(
            {
                "gpu": 0,
                "swap": 0,
                "swap_total": 2_147_483_648,
                "temp": 42.5,
            }
        )
    return payload


def _bootstrap(
    *,
    panel_name: str = "Komari Lab",
    node_payloads: list[dict[str, Any]] | None = None,
    status_payloads: list[dict[str, Any]] | None = None,
) -> KomariBootstrapData:
    """Build a complete fake panel snapshot."""
    nodes = parse_nodes(node_payloads or [_node_payload()])
    statuses = parse_statuses(status_payloads or [_status_payload()])
    return KomariBootstrapData(
        panel=KomariPanelInfo(
            name=panel_name,
            version="1.2.3",
            build_hash="sanitized-build",
            rpc_version="2.0",
            private=False,
        ),
        nodes=nodes,
        statuses=statuses,
        capabilities=frozenset(),
    )


async def _setup_entry(
    hass: HomeAssistant,
    bootstrap: KomariBootstrapData,
    *,
    title: str = "Komari Lab",
    url: str = "https://example.test",
    options: dict[str, Any] | None = None,
) -> KomariConfigEntry:
    """Set up one config entry through Home Assistant's real platform loader."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={CONF_URL: url, CONF_VERIFY_SSL: True},
        options=options or {},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return cast("KomariConfigEntry", entry)


def _unique_id(entry_id: str, scope: str, key: str) -> str:
    """Return an expected stable entity unique ID."""
    return f"{entry_id}:{scope}:{key}"


def _entity_id(
    hass: HomeAssistant,
    platform: str,
    unique_id: str,
) -> str:
    """Resolve a registered Komari entity and fail clearly when missing."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def _state(hass: HomeAssistant, entity_id: str) -> State:
    """Return an entity state and fail clearly when missing."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _updated_snapshot(
    entry: KomariConfigEntry,
    *,
    nodes: Mapping[str, KomariNode] | None = None,
    statuses: Mapping[str, KomariNodeStatus] | None = None,
) -> KomariSnapshot:
    """Copy the current snapshot with selected fields replaced."""
    current = entry.runtime_data.coordinator.data
    return KomariSnapshot(
        panel=current.panel,
        nodes=nodes or current.nodes,
        statuses=statuses if statuses is not None else current.statuses,
        capabilities=current.capabilities,
    )


def test_usage_percentage_handles_zero_and_invalid_totals() -> None:
    """Percentage calculation preserves zero without dividing by invalid totals."""
    assert usage_percentage(0, 100) == 0
    assert usage_percentage(50, 100) == 50
    assert usage_percentage(120, 100) == 120
    assert usage_percentage(None, 100) is None
    assert usage_percentage(1, None) is None
    assert usage_percentage(1, 0) is None


async def test_panel_and_node_core_entities(hass: HomeAssistant) -> None:
    """All enabled core entities expose stable values and correct HA metadata."""
    entry = await _setup_entry(hass, _bootstrap())
    node_scope = f"node:{NODE_UUID}"

    rpc_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, "panel", "rpc_available")
    )
    problem_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, "panel", "node_problem")
    )
    online_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, node_scope, "online")
    )
    node_count_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, "panel", "node_count")
    )
    online_count_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, "panel", "online_node_count")
    )
    offline_count_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, "panel", "offline_node_count")
    )

    assert _state(hass, rpc_id).state == "on"
    assert _state(hass, rpc_id).attributes[ATTR_DEVICE_CLASS] == (
        BinarySensorDeviceClass.CONNECTIVITY
    )
    assert _state(hass, problem_id).state == "off"
    assert _state(hass, online_id).state == "on"
    assert _state(hass, node_count_id).state == "1"
    assert _state(hass, online_count_id).state == "1"
    assert _state(hass, offline_count_id).state == "0"

    expected_states = {
        "last_seen": "2026-08-26T00:00:00+00:00",
        "cpu_usage": "12.5",
        "gpu_usage": "0.0",
        "memory_usage": "50.0",
        "memory_used": "4294967296",
        "swap_usage": "0.0",
        "disk_usage": "50.0",
        "disk_used": "53687091200",
        "load_1m": "0.25",
        "load_5m": "0.2",
        "load_15m": "0.1",
        "temperature": "42.5",
        "network_in": "1024.0",
        "network_out": "2048.0",
        "network_total_up": "123456789",
        "network_total_down": "987654321",
    }
    for key, expected_state in expected_states.items():
        entity_id = _entity_id(
            hass, "sensor", _unique_id(entry.entry_id, node_scope, key)
        )
        assert _state(hass, entity_id).state == expected_state

    cpu_state = _state(
        hass,
        _entity_id(
            hass,
            "sensor",
            _unique_id(entry.entry_id, node_scope, "cpu_usage"),
        ),
    )
    assert cpu_state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE
    assert cpu_state.attributes["state_class"] == SensorStateClass.MEASUREMENT

    memory_state = _state(
        hass,
        _entity_id(
            hass,
            "sensor",
            _unique_id(entry.entry_id, node_scope, "memory_used"),
        ),
    )
    assert memory_state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.DATA_SIZE
    assert memory_state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfInformation.BYTES

    temperature_state = _state(
        hass,
        _entity_id(
            hass,
            "sensor",
            _unique_id(entry.entry_id, node_scope, "temperature"),
        ),
    )
    assert temperature_state.attributes[ATTR_DEVICE_CLASS] == (
        SensorDeviceClass.TEMPERATURE
    )
    assert temperature_state.attributes[ATTR_UNIT_OF_MEASUREMENT] == (
        UnitOfTemperature.CELSIUS
    )

    rate_state = _state(
        hass,
        _entity_id(
            hass,
            "sensor",
            _unique_id(entry.entry_id, node_scope, "network_in"),
        ),
    )
    assert rate_state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.DATA_RATE
    assert rate_state.attributes[ATTR_UNIT_OF_MEASUREMENT] == (
        UnitOfDataRate.BYTES_PER_SECOND
    )

    total_state = _state(
        hass,
        _entity_id(
            hass,
            "sensor",
            _unique_id(entry.entry_id, node_scope, "network_total_up"),
        ),
    )
    assert total_state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING


async def test_default_disabled_entities_are_registered(hass: HomeAssistant) -> None:
    """Advanced diagnostic entities exist but do not create states by default."""
    entry = await _setup_entry(hass, _bootstrap())
    registry = er.async_get(hass)
    node_scope = f"node:{NODE_UUID}"
    disabled_keys = {
        "rpc_version": "panel",
        "build_hash": "panel",
        "memory_total": node_scope,
        "swap_used": node_scope,
        "disk_total": node_scope,
        "process_count": node_scope,
        "tcp_connections": node_scope,
        "udp_connections": node_scope,
    }

    for key, scope in disabled_keys.items():
        entity_id = _entity_id(hass, "sensor", _unique_id(entry.entry_id, scope, key))
        registry_entry = registry.async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(entity_id) is None


async def test_device_ownership_and_entity_unique_ids(hass: HomeAssistant) -> None:
    """Panel and node entities attach to their exact entry-scoped devices."""
    entry = await _setup_entry(hass, _bootstrap())
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    panel = get_panel_device(device_registry, entry.entry_id)
    node = get_node_device(device_registry, entry.entry_id, NODE_UUID)
    assert panel is not None
    assert node is not None
    assert node.via_device_id == panel.id

    panel_entity_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, "panel", "node_count"),
    )
    node_entity_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, f"node:{NODE_UUID}", "cpu_usage"),
    )
    panel_entity = entity_registry.async_get(panel_entity_id)
    node_entity = entity_registry.async_get(node_entity_id)
    assert panel_entity is not None
    assert node_entity is not None
    assert panel_entity.device_id == panel.id
    assert node_entity.device_id == node.id
    assert panel_entity.unique_id == f"{entry.entry_id}:panel:node_count"
    assert node_entity.unique_id == (f"{entry.entry_id}:node:{NODE_UUID}:cpu_usage")


async def test_offline_missing_and_panel_failure_availability(
    hass: HomeAssistant,
) -> None:
    """Explicit offline, missing status, and panel failure remain distinguishable."""
    entry = await _setup_entry(hass, _bootstrap())
    coordinator = entry.runtime_data.coordinator
    node_scope = f"node:{NODE_UUID}"
    rpc_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, "panel", "rpc_available")
    )
    problem_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, "panel", "node_problem")
    )
    online_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, node_scope, "online")
    )
    cpu_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, node_scope, "cpu_usage")
    )
    last_seen_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, node_scope, "last_seen")
    )
    node_count_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, "panel", "node_count")
    )

    offline_statuses = parse_statuses([_status_payload(online=False)])
    coordinator.async_set_updated_data(
        _updated_snapshot(entry, statuses=offline_statuses)
    )
    await hass.async_block_till_done()
    assert _state(hass, online_id).state == "off"
    assert _state(hass, cpu_id).state == STATE_UNAVAILABLE
    assert _state(hass, last_seen_id).state == "2026-08-26T00:00:00+00:00"
    assert _state(hass, problem_id).state == "on"

    coordinator.async_set_updated_data(_updated_snapshot(entry, statuses={}))
    await hass.async_block_till_done()
    assert _state(hass, online_id).state == STATE_UNAVAILABLE
    assert _state(hass, cpu_id).state == STATE_UNAVAILABLE
    assert _state(hass, last_seen_id).state == STATE_UNAVAILABLE
    assert _state(hass, problem_id).state == "off"

    coordinator.async_set_update_error(UpdateFailed("sanitized test failure"))
    await hass.async_block_till_done()
    assert _state(hass, rpc_id).state == "off"
    assert _state(hass, online_id).state == STATE_UNAVAILABLE
    assert _state(hass, problem_id).state == STATE_UNAVAILABLE
    assert _state(hass, node_count_id).state == STATE_UNAVAILABLE

    coordinator.async_set_updated_data(
        _updated_snapshot(entry, statuses=parse_statuses([_status_payload()]))
    )
    await hass.async_block_till_done()
    assert _state(hass, rpc_id).state == "on"
    assert _state(hass, online_id).state == "on"


async def test_conditional_entities_appear_when_data_is_observed(
    hass: HomeAssistant,
) -> None:
    """GPU, swap, and temperature entities are added once data becomes valid."""
    entry = await _setup_entry(
        hass,
        _bootstrap(
            node_payloads=[_node_payload(optional_hardware=False)],
            status_payloads=[_status_payload(optional_metrics=False)],
        ),
    )
    registry = er.async_get(hass)
    node_scope = f"node:{NODE_UUID}"
    conditional_keys = {"gpu_usage", "swap_usage", "swap_used", "temperature"}
    for key in conditional_keys:
        assert (
            registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                _unique_id(entry.entry_id, node_scope, key),
            )
            is None
        )

    coordinator = entry.runtime_data.coordinator
    observed = _status_payload()
    observed["gpu"] = 1
    coordinator.async_set_updated_data(
        _updated_snapshot(entry, statuses=parse_statuses([observed]))
    )
    await hass.async_block_till_done()

    for key in conditional_keys:
        assert (
            registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                _unique_id(entry.entry_id, node_scope, key),
            )
            is not None
        )


async def test_zero_gpu_and_invalid_temperatures_do_not_create_entities(
    hass: HomeAssistant,
) -> None:
    """Zero/sentinel telemetry from unsupported hardware stays absent."""
    node = _node_payload(optional_hardware=False)
    node["gpu_name"] = "unknown"
    status = _status_payload(optional_metrics=False)
    status.update({"gpu": 0, "temp": 0})
    entry = await _setup_entry(
        hass,
        _bootstrap(node_payloads=[node], status_payloads=[status]),
    )
    registry = er.async_get(hass)
    for key in ("gpu_usage", "temperature"):
        assert (
            registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                _unique_id(entry.entry_id, f"node:{NODE_UUID}", key),
            )
            is None
        )


@pytest.mark.parametrize("temperature", [-1, 0, 200.1, 201])
async def test_invalid_temperature_boundaries_do_not_create_entity(
    hass: HomeAssistant,
    temperature: float,
) -> None:
    """Only values inside the locked 0 < temperature <= 200 range qualify."""
    status = _status_payload(optional_metrics=False)
    status["temp"] = temperature
    entry = await _setup_entry(
        hass,
        _bootstrap(
            node_payloads=[_node_payload(optional_hardware=False)],
            status_payloads=[status],
        ),
    )
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor",
            DOMAIN,
            _unique_id(entry.entry_id, f"node:{NODE_UUID}", "temperature"),
        )
        is None
    )


async def test_temperature_upper_boundary_is_valid(hass: HomeAssistant) -> None:
    """The inclusive 200 degree upper boundary remains representable."""
    status = _status_payload(optional_metrics=False)
    status["temp"] = 200
    entry = await _setup_entry(
        hass,
        _bootstrap(
            node_payloads=[_node_payload(optional_hardware=False)],
            status_payloads=[status],
        ),
    )
    entity_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, f"node:{NODE_UUID}", "temperature"),
    )
    assert _state(hass, entity_id).state == "200.0"


async def test_upgrade_removes_stale_optional_hardware_registry_entries(
    hass: HomeAssistant,
) -> None:
    """P1 false-positive GPU and temperature entries are removed on setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Komari Lab",
        data={CONF_URL: "https://example.test", CONF_VERIFY_SSL: True},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in ("gpu_usage", "temperature"):
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            _unique_id(entry.entry_id, f"node:{NODE_UUID}", key),
            config_entry=entry,
        )

    node = _node_payload(optional_hardware=False)
    node["gpu_name"] = "null"
    status = _status_payload(optional_metrics=False)
    status.update({"gpu": 0, "temp": 0})
    bootstrap = _bootstrap(node_payloads=[node], status_payloads=[status])
    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for key in ("gpu_usage", "temperature"):
        assert (
            registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                _unique_id(entry.entry_id, f"node:{NODE_UUID}", key),
            )
            is None
        )


async def test_new_node_is_added_once_without_reload(hass: HomeAssistant) -> None:
    """A coordinator inventory update creates one device and one entity set."""
    entry = await _setup_entry(hass, _bootstrap())
    coordinator = entry.runtime_data.coordinator
    entity_registry = er.async_get(hass)
    before_count = len(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    )
    node_payloads = [
        _node_payload(),
        _node_payload(SECOND_NODE_UUID, name="Second node"),
    ]
    status_payloads = [
        _status_payload(),
        _status_payload(SECOND_NODE_UUID),
    ]
    updated = _updated_snapshot(
        entry,
        nodes=parse_nodes(node_payloads),
        statuses=parse_statuses(status_payloads),
    )

    coordinator.async_set_updated_data(updated)
    await hass.async_block_till_done()
    after_count = len(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    )
    second_scope = f"node:{SECOND_NODE_UUID}"
    assert _entity_id(
        hass,
        "binary_sensor",
        _unique_id(entry.entry_id, second_scope, "online"),
    )
    assert _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, second_scope, "cpu_usage"),
    )
    second_device = get_node_device(
        dr.async_get(hass),
        entry.entry_id,
        SECOND_NODE_UUID,
    )
    assert second_device is not None
    assert after_count > before_count

    coordinator.async_set_updated_data(updated)
    await hass.async_block_till_done()
    assert (
        len(er.async_entries_for_config_entry(entity_registry, entry.entry_id))
        == after_count
    )


async def test_two_panels_are_runtime_and_failure_isolated(
    hass: HomeAssistant,
) -> None:
    """The same node UUID under two entries has independent entities and health."""
    entry_a = await _setup_entry(
        hass,
        _bootstrap(panel_name="Panel A"),
        title="Panel A",
        url="https://a.example.test",
    )
    entry_b = await _setup_entry(
        hass,
        _bootstrap(panel_name="Panel B"),
        title="Panel B",
        url="https://b.example.test",
    )
    assert entry_a.runtime_data.client is not entry_b.runtime_data.client
    assert entry_a.runtime_data.coordinator is not entry_b.runtime_data.coordinator
    assert entry_a.runtime_data.panel_device_id != entry_b.runtime_data.panel_device_id

    rpc_a = _entity_id(
        hass,
        "binary_sensor",
        _unique_id(entry_a.entry_id, "panel", "rpc_available"),
    )
    rpc_b = _entity_id(
        hass,
        "binary_sensor",
        _unique_id(entry_b.entry_id, "panel", "rpc_available"),
    )
    cpu_b = _entity_id(
        hass,
        "sensor",
        _unique_id(entry_b.entry_id, f"node:{NODE_UUID}", "cpu_usage"),
    )
    assert rpc_a != rpc_b

    entry_a.runtime_data.coordinator.async_set_update_error(
        UpdateFailed("panel A only")
    )
    await hass.async_block_till_done()
    assert _state(hass, rpc_a).state == "off"
    assert _state(hass, rpc_b).state == "on"
    assert _state(hass, cpu_b).state == "12.5"

    assert await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, rpc_a).state == STATE_UNAVAILABLE
    assert entry_a.state is ConfigEntryState.NOT_LOADED
    assert _state(hass, rpc_b).state == "on"


async def test_reload_and_unload_do_not_duplicate_entities(
    hass: HomeAssistant,
) -> None:
    """Reload preserves unique IDs and unload removes active entity states."""
    bootstrap = _bootstrap()
    entry = await _setup_entry(hass, bootstrap)
    registry = er.async_get(hass)
    initial_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    initial_unique_ids = {item.unique_id for item in initial_entries}
    rpc_id = _entity_id(
        hass,
        "binary_sensor",
        _unique_id(entry.entry_id, "panel", "rpc_available"),
    )

    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    reloaded_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {item.unique_id for item in reloaded_entries} == initial_unique_ids
    assert len(reloaded_entries) == len(initial_entries)
    assert _state(hass, rpc_id).state == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, rpc_id).state == STATE_UNAVAILABLE
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("expired_at", "expected_expires_at", "remaining", "expired_state"),
    [
        (
            "2026-08-28T08:00:00+08:00",
            "2026-08-28T00:00:00+00:00",
            "86400.0",
            "off",
        ),
        (
            "2026-08-27T00:00:00Z",
            "2026-08-27T00:00:00+00:00",
            "0.0",
            "on",
        ),
        (
            "2026-08-26T08:00:00+08:00",
            "2026-08-26T00:00:00+00:00",
            "0.0",
            "on",
        ),
    ],
)
async def test_expiry_entities_handle_future_current_past_and_timezone(
    hass: HomeAssistant,
    expired_at: str,
    expected_expires_at: str,
    remaining: str,
    expired_state: str,
) -> None:
    """Expiry values use exact UTC timestamps and non-negative duration seconds."""
    now = datetime(2026, 8, 27, tzinfo=UTC)
    node = _node_payload()
    node.update(
        {
            "expired_at": expired_at,
            "auto_renewal": True,
            "billing_cycle": 30,
        }
    )
    with (
        patch(
            "custom_components.komari.sensor.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.komari.binary_sensor.dt_util.utcnow",
            return_value=now,
        ),
    ):
        entry = await _setup_entry(
            hass,
            _bootstrap(node_payloads=[node]),
        )

    scope = f"node:{NODE_UUID}"
    expires_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "expires_at")
    )
    remaining_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "remaining_time")
    )
    expired_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, scope, "expired")
    )

    assert _state(hass, expires_id).state == expected_expires_at
    assert _state(hass, remaining_id).state == remaining
    assert _state(hass, expired_id).state == expired_state
    assert _state(hass, expires_id).attributes[ATTR_DEVICE_CLASS] == (
        SensorDeviceClass.TIMESTAMP
    )
    assert _state(hass, remaining_id).attributes[ATTR_DEVICE_CLASS] == (
        SensorDeviceClass.DURATION
    )
    assert _state(hass, remaining_id).attributes[ATTR_UNIT_OF_MEASUREMENT] == (
        UnitOfTime.SECONDS
    )
    assert _state(hass, expired_id).attributes[ATTR_DEVICE_CLASS] == (
        BinarySensorDeviceClass.PROBLEM
    )


async def test_expiry_diagnostics_and_offline_availability(
    hass: HomeAssistant,
) -> None:
    """Static expiry metadata survives node outages but not panel outages."""
    now = datetime(2026, 8, 27, tzinfo=UTC)
    node = _node_payload()
    node.update(
        {
            "expired_at": "2026-08-28T00:00:00Z",
            "auto_renewal": False,
            "billing_cycle": 365,
        }
    )
    bootstrap = _bootstrap(node_payloads=[node])
    with (
        patch(
            "custom_components.komari.sensor.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.komari.binary_sensor.dt_util.utcnow",
            return_value=now,
        ),
    ):
        entry = await _setup_entry(hass, bootstrap)

    registry = er.async_get(hass)
    scope = f"node:{NODE_UUID}"
    auto_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, scope, "auto_renewal")
    )
    billing_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "billing_cycle")
    )
    auto_entry = registry.async_get(auto_id)
    billing_entry = registry.async_get(billing_id)
    assert auto_entry is not None
    assert billing_entry is not None
    assert auto_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert billing_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert auto_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert billing_entry.entity_category is EntityCategory.DIAGNOSTIC

    registry.async_update_entity(auto_id, disabled_by=None)
    registry.async_update_entity(billing_id, disabled_by=None)
    with (
        patch(
            "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
            new=AsyncMock(return_value=bootstrap),
        ),
        patch(
            "custom_components.komari.sensor.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.komari.binary_sensor.dt_util.utcnow",
            return_value=now,
        ),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert _state(hass, auto_id).state == "off"
    assert _state(hass, billing_id).state == "365"
    assert _state(hass, billing_id).attributes[ATTR_UNIT_OF_MEASUREMENT] == (
        UnitOfTime.DAYS
    )

    expires_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "expires_at")
    )
    remaining_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "remaining_time")
    )
    expired_id = _entity_id(
        hass, "binary_sensor", _unique_id(entry.entry_id, scope, "expired")
    )
    coordinator = entry.runtime_data.coordinator
    with (
        patch(
            "custom_components.komari.sensor.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.komari.binary_sensor.dt_util.utcnow",
            return_value=now,
        ),
    ):
        coordinator.async_set_updated_data(
            _updated_snapshot(
                entry,
                statuses=parse_statuses([_status_payload(online=False)]),
            )
        )
        await hass.async_block_till_done()

    assert _state(hass, expires_id).state == "2026-08-28T00:00:00+00:00"
    assert _state(hass, remaining_id).state == "86400.0"
    assert _state(hass, expired_id).state == "off"
    assert _state(hass, auto_id).state == "off"
    assert _state(hass, billing_id).state == "365"

    coordinator.async_set_update_error(UpdateFailed("sanitized panel outage"))
    await hass.async_block_till_done()
    for entity_id in (expires_id, remaining_id, expired_id, auto_id, billing_id):
        assert _state(hass, entity_id).state == STATE_UNAVAILABLE


async def test_expiry_entities_follow_option_and_clean_registry(
    hass: HomeAssistant,
) -> None:
    """Disabling expiry removes its registry entries and re-enabling restores them."""
    node = _node_payload()
    node.update(
        {
            "expired_at": "2030-01-01T00:00:00Z",
            "auto_renewal": True,
            "billing_cycle": 30,
        }
    )
    bootstrap = _bootstrap(node_payloads=[node])
    entry = await _setup_entry(
        hass,
        bootstrap,
        options={CONF_ENABLE_EXPIRY_ENTITIES: False},
    )
    registry = er.async_get(hass)
    scope = f"node:{NODE_UUID}"
    entity_keys = {
        "sensor": {"expires_at", "remaining_time", "billing_cycle"},
        "binary_sensor": {"expired", "auto_renewal"},
    }

    for platform, keys in entity_keys.items():
        for key in keys:
            assert (
                registry.async_get_entity_id(
                    platform,
                    DOMAIN,
                    _unique_id(entry.entry_id, scope, key),
                )
                is None
            )

    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_EXPIRY_ENTITIES: True},
    )
    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for platform, keys in entity_keys.items():
        for key in keys:
            assert (
                registry.async_get_entity_id(
                    platform,
                    DOMAIN,
                    _unique_id(entry.entry_id, scope, key),
                )
                is not None
            )

    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_EXPIRY_ENTITIES: False},
    )
    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for platform, keys in entity_keys.items():
        for key in keys:
            assert (
                registry.async_get_entity_id(
                    platform,
                    DOMAIN,
                    _unique_id(entry.entry_id, scope, key),
                )
                is None
            )


async def test_expiry_entities_are_added_when_metadata_appears(
    hass: HomeAssistant,
) -> None:
    """An existing node gains expiry entities after an inventory refresh."""
    entry = await _setup_entry(hass, _bootstrap())
    registry = er.async_get(hass)
    unique_id = _unique_id(
        entry.entry_id,
        f"node:{NODE_UUID}",
        "expires_at",
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None

    node = _node_payload()
    node["expired_at"] = "2030-01-01T00:00:00Z"
    entry.runtime_data.coordinator.async_set_updated_data(
        _updated_snapshot(entry, nodes=parse_nodes([node]))
    )
    await hass.async_block_till_done()

    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is not None


def test_description_defaults_match_the_p1_contract() -> None:
    """Description tables encode the planned default enablement policy."""
    panel = {description.key: description for description in PANEL_SENSOR_DESCRIPTIONS}
    node = {description.key: description for description in NODE_SENSOR_DESCRIPTIONS}

    assert panel["komari_version"].entity_registry_enabled_default is True
    assert panel["rpc_version"].entity_registry_enabled_default is False
    assert panel["build_hash"].entity_registry_enabled_default is False
    assert node["cpu_usage"].entity_registry_enabled_default is True
    assert node["gpu_usage"].entity_registry_enabled_default is True
    assert node["memory_total"].entity_registry_enabled_default is False
    assert node["swap_usage"].entity_registry_enabled_default is True
    assert node["swap_used"].entity_registry_enabled_default is False
    assert node["disk_total"].entity_registry_enabled_default is False
    assert node["network_total_up"].state_class is SensorStateClass.TOTAL_INCREASING
    assert node["process_count"].entity_registry_enabled_default is False
    expiry_sensor = {
        description.key: description for description in EXPIRY_SENSOR_DESCRIPTIONS
    }
    expiry_binary = {
        description.key: description
        for description in EXPIRY_BINARY_SENSOR_DESCRIPTIONS
    }
    assert expiry_sensor["expires_at"].entity_registry_enabled_default is True
    assert expiry_sensor["remaining_time"].entity_registry_enabled_default is True
    assert expiry_sensor["billing_cycle"].entity_registry_enabled_default is False
    assert expiry_binary["expired"].entity_registry_enabled_default is True
    assert expiry_binary["auto_renewal"].entity_registry_enabled_default is False
