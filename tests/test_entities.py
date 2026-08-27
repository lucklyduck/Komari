"""Integration-style tests for Komari panel and node entities."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.komari import async_remove_config_entry_device
from custom_components.komari.binary_sensor import (
    EXPIRY_BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.komari.const import (
    CONF_API_KEY,
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    DOMAIN,
    MAX_PING_ENTITIES,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_PING_TASKS,
)
from custom_components.komari.coordinator import (
    BRANCH_PING_STATS,
    KomariBranchState,
    KomariSnapshot,
)
from custom_components.komari.device import get_node_device, get_panel_device
from custom_components.komari.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
    redact_diagnostics,
)
from custom_components.komari.models import (
    KomariBootstrapData,
    KomariNode,
    KomariNodeStatus,
    KomariPanelInfo,
    TrafficLimitType,
    parse_nodes,
    parse_ping_metric_stats,
    parse_ping_tasks,
    parse_statuses,
)
from custom_components.komari.sensor import (
    EXPIRY_SENSOR_DESCRIPTIONS,
    NODE_SENSOR_DESCRIPTIONS,
    PANEL_SENSOR_DESCRIPTIONS,
    PING_SENSOR_DESCRIPTIONS,
    TRAFFIC_METADATA_SENSOR_DESCRIPTIONS,
    TRAFFIC_STATUS_SENSOR_DESCRIPTIONS,
    traffic_used_by_limit_rule,
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
    capabilities: frozenset[str] | None = None,
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
        capabilities=capabilities or frozenset(),
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


async def _setup_ping_entry(
    hass: HomeAssistant,
    *,
    task_payloads: list[dict[str, Any]],
    stats_payloads: list[dict[str, Any]],
    selected_task_ids: list[str],
    selected_stats: list[str],
    node_payloads: list[dict[str, Any]] | None = None,
    status_payloads: list[dict[str, Any]] | None = None,
    title: str = "Komari Lab",
    url: str = "https://example.test",
) -> KomariConfigEntry:
    """Set up a real entity platform with deterministic Ping branches."""
    tasks = parse_ping_tasks(task_payloads)
    stats = parse_ping_metric_stats({"stats": stats_payloads})
    bootstrap = _bootstrap(
        panel_name=title,
        node_payloads=node_payloads,
        status_payloads=status_payloads,
        capabilities=frozenset({METHOD_PUBLIC_PING_TASKS, METHOD_PING_METRIC_STATS}),
    )
    with (
        patch(
            "custom_components.komari.api.KomariApiClient.async_get_ping_tasks",
            new=AsyncMock(return_value=tasks),
        ),
        patch(
            "custom_components.komari.api.KomariApiClient.async_get_ping_stats",
            new=AsyncMock(return_value=stats),
        ),
    ):
        return await _setup_entry(
            hass,
            bootstrap,
            title=title,
            url=url,
            options={
                CONF_PING_TASK_IDS: selected_task_ids,
                CONF_PING_STAT_KEYS: selected_stats,
            },
        )


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


@pytest.mark.parametrize(
    ("limit_type", "uploaded", "downloaded", "expected"),
    [
        (TrafficLimitType.SUM, 40, 80, 120),
        (TrafficLimitType.MAX, 40, 80, 80),
        (TrafficLimitType.MIN, 40, 80, 40),
        (TrafficLimitType.UP, 40, 80, 40),
        (TrafficLimitType.DOWN, 40, 80, 80),
        (TrafficLimitType.SUM, None, 80, None),
        (TrafficLimitType.MAX, 40, None, None),
        (TrafficLimitType.MIN, None, None, None),
        (TrafficLimitType.UP, None, 80, None),
        (TrafficLimitType.DOWN, None, 80, 80),
    ],
)
def test_traffic_limit_calculation_rules(
    limit_type: TrafficLimitType,
    uploaded: int | None,
    downloaded: int | None,
    expected: int | None,
) -> None:
    """All five rules consume only the counters required by their semantics."""
    node_payload = _node_payload()
    node_payload.update({"traffic_limit": 100, "traffic_limit_type": limit_type.value})
    status_payload = _status_payload()
    status_payload["net_total_up"] = uploaded
    status_payload["net_total_down"] = downloaded
    node = parse_nodes([node_payload])[NODE_UUID]
    status = parse_statuses([status_payload])[NODE_UUID]

    assert traffic_used_by_limit_rule(node, status) == expected


def test_unknown_traffic_limit_type_is_not_guessed() -> None:
    """An unknown remote rule never falls back to a subjective calculation."""
    node_payload = _node_payload()
    node_payload.update({"traffic_limit": 100, "traffic_limit_type": "future-rule"})
    node = parse_nodes([node_payload])[NODE_UUID]
    status = parse_statuses([_status_payload()])[NODE_UUID]

    assert node.traffic_limit_type is None
    assert traffic_used_by_limit_rule(node, status) is None


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


async def test_traffic_limit_entities_and_reset_sensitive_usage(
    hass: HomeAssistant,
) -> None:
    """Traffic entities expose uncapped usage without locally accumulating resets."""
    node = _node_payload()
    node.update({"traffic_limit": 100, "traffic_limit_type": "sum"})
    status = _status_payload()
    status.update({"net_total_up": 80, "net_total_down": 40})
    entry = await _setup_entry(
        hass,
        _bootstrap(node_payloads=[node], status_payloads=[status]),
    )
    scope = f"node:{NODE_UUID}"
    limit_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "traffic_limit")
    )
    type_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "traffic_limit_type")
    )
    used_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, scope, "traffic_used_by_limit_rule"),
    )
    usage_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "traffic_limit_usage")
    )

    assert _state(hass, limit_id).state == "100"
    assert _state(hass, used_id).state == "120"
    assert _state(hass, usage_id).state == "120.0"
    assert _state(hass, limit_id).attributes[ATTR_DEVICE_CLASS] == (
        SensorDeviceClass.DATA_SIZE
    )
    assert _state(hass, used_id).attributes[ATTR_DEVICE_CLASS] == (
        SensorDeviceClass.DATA_SIZE
    )
    assert _state(hass, used_id).attributes[ATTR_UNIT_OF_MEASUREMENT] == (
        UnitOfInformation.BYTES
    )
    assert _state(hass, used_id).attributes["state_class"] == SensorStateClass.TOTAL
    assert _state(hass, used_id).attributes["calculation_source"] == ("agent_counters")
    assert _state(hass, used_id).attributes["reset_sensitive"] is True
    assert _state(hass, usage_id).attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE

    type_entry = er.async_get(hass).async_get(type_id)
    assert type_entry is not None
    assert type_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert type_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(type_id) is None

    reset_status = _status_payload()
    reset_status.update({"net_total_up": 4, "net_total_down": 6})
    entry.runtime_data.coordinator.async_set_updated_data(
        _updated_snapshot(entry, statuses=parse_statuses([reset_status]))
    )
    await hass.async_block_till_done()
    assert _state(hass, used_id).state == "10"
    assert _state(hass, usage_id).state == "10.0"


async def test_traffic_static_and_derived_availability(
    hass: HomeAssistant,
) -> None:
    """Limits survive node outages while Agent-derived values do not."""
    node = _node_payload()
    node.update({"traffic_limit": 1000, "traffic_limit_type": "max"})
    bootstrap = _bootstrap(node_payloads=[node])
    entry = await _setup_entry(hass, bootstrap)
    scope = f"node:{NODE_UUID}"
    limit_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "traffic_limit")
    )
    used_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, scope, "traffic_used_by_limit_rule"),
    )
    usage_id = _entity_id(
        hass, "sensor", _unique_id(entry.entry_id, scope, "traffic_limit_usage")
    )

    entry.runtime_data.coordinator.async_set_updated_data(
        _updated_snapshot(
            entry,
            statuses=parse_statuses([_status_payload(online=False)]),
        )
    )
    await hass.async_block_till_done()
    assert _state(hass, limit_id).state == "1000"
    assert _state(hass, used_id).state == STATE_UNAVAILABLE
    assert _state(hass, usage_id).state == STATE_UNAVAILABLE

    entry.runtime_data.coordinator.async_set_update_error(
        UpdateFailed("sanitized panel outage")
    )
    await hass.async_block_till_done()
    assert _state(hass, limit_id).state == STATE_UNAVAILABLE
    assert _state(hass, used_id).state == STATE_UNAVAILABLE
    assert _state(hass, usage_id).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("limit", "limit_type", "derived_available"),
    [
        (0, "sum", False),
        (100, "future-rule", False),
        (100, "sum", True),
    ],
)
async def test_traffic_limit_creation_and_unknown_rule(
    hass: HomeAssistant,
    limit: int,
    limit_type: str,
    derived_available: bool,
) -> None:
    """Zero limits create nothing; unknown rules retain only authoritative data."""
    node = _node_payload()
    node.update({"traffic_limit": limit, "traffic_limit_type": limit_type})
    entry = await _setup_entry(hass, _bootstrap(node_payloads=[node]))
    registry = er.async_get(hass)
    scope = f"node:{NODE_UUID}"
    limit_unique_id = _unique_id(entry.entry_id, scope, "traffic_limit")
    used_unique_id = _unique_id(entry.entry_id, scope, "traffic_used_by_limit_rule")
    usage_unique_id = _unique_id(entry.entry_id, scope, "traffic_limit_usage")

    if limit == 0:
        for unique_id in (limit_unique_id, used_unique_id, usage_unique_id):
            assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
        return

    assert registry.async_get_entity_id("sensor", DOMAIN, limit_unique_id) is not None
    used_id = _entity_id(hass, "sensor", used_unique_id)
    usage_id = _entity_id(hass, "sensor", usage_unique_id)
    if derived_available:
        assert _state(hass, used_id).state != STATE_UNAVAILABLE
        assert _state(hass, usage_id).state != STATE_UNAVAILABLE
    else:
        assert _state(hass, used_id).state == STATE_UNAVAILABLE
        assert _state(hass, usage_id).state == STATE_UNAVAILABLE


async def test_traffic_entities_follow_option_and_clean_registry(
    hass: HomeAssistant,
) -> None:
    """The traffic option removes and restores every traffic registry entity."""
    node = _node_payload()
    node.update({"traffic_limit": 1000, "traffic_limit_type": "down"})
    bootstrap = _bootstrap(node_payloads=[node])
    entry = await _setup_entry(
        hass,
        bootstrap,
        options={CONF_ENABLE_TRAFFIC_ENTITIES: False},
    )
    registry = er.async_get(hass)
    scope = f"node:{NODE_UUID}"
    keys = {
        "traffic_limit",
        "traffic_limit_type",
        "traffic_used_by_limit_rule",
        "traffic_limit_usage",
    }

    for key in keys:
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, _unique_id(entry.entry_id, scope, key)
            )
            is None
        )

    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_TRAFFIC_ENTITIES: True},
    )
    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for key in keys:
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, _unique_id(entry.entry_id, scope, key)
            )
            is not None
        )

    hass.config_entries.async_update_entry(
        entry,
        options={CONF_ENABLE_TRAFFIC_ENTITIES: False},
    )
    with patch(
        "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=bootstrap),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for key in keys:
        assert (
            registry.async_get_entity_id(
                "sensor", DOMAIN, _unique_id(entry.entry_id, scope, key)
            )
            is None
        )


async def test_two_panels_keep_same_uuid_traffic_entities_separate(
    hass: HomeAssistant,
) -> None:
    """Traffic entities remain entry-scoped when panels share a node UUID."""
    node_a = _node_payload()
    node_a.update({"traffic_limit": 1000, "traffic_limit_type": "up"})
    status_a = _status_payload()
    status_a["net_total_up"] = 100
    node_b = _node_payload()
    node_b.update({"traffic_limit": 2000, "traffic_limit_type": "down"})
    status_b = _status_payload()
    status_b["net_total_down"] = 400

    entry_a = await _setup_entry(
        hass,
        _bootstrap(node_payloads=[node_a], status_payloads=[status_a]),
        title="Panel A",
        url="https://traffic-a.example.test",
    )
    entry_b = await _setup_entry(
        hass,
        _bootstrap(node_payloads=[node_b], status_payloads=[status_b]),
        title="Panel B",
        url="https://traffic-b.example.test",
    )
    unique_a = _unique_id(
        entry_a.entry_id,
        f"node:{NODE_UUID}",
        "traffic_used_by_limit_rule",
    )
    unique_b = _unique_id(
        entry_b.entry_id,
        f"node:{NODE_UUID}",
        "traffic_used_by_limit_rule",
    )
    entity_a = _entity_id(hass, "sensor", unique_a)
    entity_b = _entity_id(hass, "sensor", unique_b)

    assert unique_a != unique_b
    assert entity_a != entity_b
    assert _state(hass, entity_a).state == "100"
    assert _state(hass, entity_b).state == "400"


async def test_ping_entities_use_stable_identity_units_and_field_availability(
    hass: HomeAssistant,
) -> None:
    """One returned series creates selected fields, including missing fields."""
    entry = await _setup_ping_entry(
        hass,
        task_payloads=[
            {
                "id": 101,
                "name": "Example endpoint",
                "clients": [NODE_UUID, "hidden-node"],
                "type": "http",
            }
        ],
        stats_payloads=[
            {
                "entity_id": NODE_UUID,
                "task_id": 101,
                "latest": 12.5,
                "loss": 0,
            },
            {
                "entity_id": "hidden-node",
                "task_id": 101,
                "latest": 99,
                "loss": 50,
            },
        ],
        selected_task_ids=["101"],
        selected_stats=["latest", "loss", "min"],
    )
    prefix = f"{entry.entry_id}:node:{NODE_UUID}:ping:101"
    latest_id = _entity_id(hass, "sensor", f"{prefix}:latest")
    loss_id = _entity_id(hass, "sensor", f"{prefix}:loss")
    minimum_id = _entity_id(hass, "sensor", f"{prefix}:min")

    latest = _state(hass, latest_id)
    assert float(latest.state) == 12.5
    assert latest.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.DURATION
    assert latest.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTime.MILLISECONDS
    assert float(_state(hass, loss_id).state) == 0
    assert _state(hass, loss_id).attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE
    assert _state(hass, minimum_id).state == STATE_UNAVAILABLE

    registry = er.async_get(hass)
    hidden_prefix = f"{entry.entry_id}:node:hidden-node:ping:"
    assert not any(
        item.unique_id.startswith(hidden_prefix)
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    )


async def test_ping_entities_follow_online_and_two_failure_ttl(
    hass: HomeAssistant,
) -> None:
    """Node state and Ping staleness do not affect ordinary core entities."""
    entry = await _setup_ping_entry(
        hass,
        task_payloads=[{"id": 101, "name": "Example endpoint"}],
        stats_payloads=[
            {"entity_id": NODE_UUID, "task_id": 101, "latest": 8, "loss": 0}
        ],
        selected_task_ids=["101"],
        selected_stats=["latest"],
    )
    ping_id = _entity_id(
        hass,
        "sensor",
        f"{entry.entry_id}:node:{NODE_UUID}:ping:101:latest",
    )
    cpu_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, f"node:{NODE_UUID}", "cpu_usage"),
    )
    coordinator = entry.runtime_data.coordinator

    offline = parse_statuses([_status_payload(online=False)])
    coordinator.async_set_updated_data(replace(coordinator.data, statuses=offline))
    await hass.async_block_till_done()
    assert _state(hass, ping_id).state == STATE_UNAVAILABLE

    coordinator._branch_states[BRANCH_PING_STATS] = KomariBranchState(
        fetched_at=datetime.now(tz=UTC),
        error_category="connection",
        consecutive_failures=2,
    )
    online = parse_statuses([_status_payload()])
    coordinator.async_set_updated_data(replace(coordinator.data, statuses=online))
    await hass.async_block_till_done()
    assert _state(hass, ping_id).state == STATE_UNAVAILABLE
    assert _state(hass, cpu_id).state != STATE_UNAVAILABLE


async def test_ping_entities_add_new_series_once_and_ignore_unselected_stats(
    hass: HomeAssistant,
) -> None:
    """Actual result discovery is dynamic without speculative advanced entities."""
    entry = await _setup_ping_entry(
        hass,
        task_payloads=[
            {"id": 101, "name": "Task one"},
            {"id": 102, "name": "Task two"},
        ],
        stats_payloads=[
            {
                "entity_id": NODE_UUID,
                "task_id": 101,
                "latest": 10,
                "loss": 0,
                "p99": 20,
            }
        ],
        selected_task_ids=["101", "102"],
        selected_stats=["latest", "loss"],
    )
    registry = er.async_get(hass)
    task_two_latest = f"{entry.entry_id}:node:{NODE_UUID}:ping:102:latest"
    unselected_p99 = f"{entry.entry_id}:node:{NODE_UUID}:ping:101:p99"
    assert registry.async_get_entity_id("sensor", DOMAIN, task_two_latest) is None
    assert registry.async_get_entity_id("sensor", DOMAIN, unselected_p99) is None

    new_stats = parse_ping_metric_stats(
        {
            "stats": [
                {"entity_id": NODE_UUID, "task_id": 101, "latest": 10, "loss": 0},
                {"entity_id": NODE_UUID, "task_id": 102, "latest": 30, "loss": 5},
            ]
        }
    )
    coordinator = entry.runtime_data.coordinator
    coordinator.async_set_updated_data(replace(coordinator.data, ping_stats=new_stats))
    await hass.async_block_till_done()
    entity_id = _entity_id(hass, "sensor", task_two_latest)
    assert float(_state(hass, entity_id).state) == 30

    before = len(er.async_entries_for_config_entry(registry, entry.entry_id))
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == before


async def test_ping_task_rename_changes_display_not_unique_id(
    hass: HomeAssistant,
) -> None:
    """Task names remain presentation data and never become entity identity."""
    entry = await _setup_ping_entry(
        hass,
        task_payloads=[{"id": 101, "name": "Old endpoint"}],
        stats_payloads=[
            {"entity_id": NODE_UUID, "task_id": 101, "latest": 10, "loss": 0}
        ],
        selected_task_ids=["101"],
        selected_stats=["latest"],
    )
    unique_id = f"{entry.entry_id}:node:{NODE_UUID}:ping:101:latest"
    entity_id = _entity_id(hass, "sensor", unique_id)
    renamed_tasks = parse_ping_tasks([{"id": 101, "name": "Renamed endpoint"}])
    coordinator = entry.runtime_data.coordinator
    coordinator.async_set_updated_data(
        replace(coordinator.data, ping_tasks=renamed_tasks)
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) == entity_id
    assert "Renamed endpoint" in _state(hass, entity_id).attributes["friendly_name"]


async def test_ping_entity_creation_stops_at_per_entry_limit(
    hass: HomeAssistant,
) -> None:
    """Inventory growth cannot create more than 256 Ping entities per panel."""
    node_payloads = [
        _node_payload(f"node-{index}", name=f"Node {index}") for index in range(1, 6)
    ]
    status_payloads = [_status_payload(f"node-{index}") for index in range(1, 6)]
    task_payloads = [{"id": index, "name": f"Task {index}"} for index in range(1, 9)]
    stats_payloads = [
        {
            "entity_id": f"node-{node_index}",
            "task_id": task_index,
            "latest": 1,
            "loss": 0,
            "min": 1,
            "max": 1,
            "avg": 1,
            "p50": 1,
            "p99": 1,
            "stddev": 0,
        }
        for node_index in range(1, 6)
        for task_index in range(1, 9)
    ]
    entry = await _setup_ping_entry(
        hass,
        task_payloads=task_payloads,
        stats_payloads=stats_payloads,
        selected_task_ids=[str(index) for index in range(1, 9)],
        selected_stats=[description.key for description in PING_SENSOR_DESCRIPTIONS],
        node_payloads=node_payloads,
        status_payloads=status_payloads,
    )

    ping_entries = [
        item
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if ":ping:" in item.unique_id
    ]
    assert len(ping_entries) == MAX_PING_ENTITIES


async def test_two_panels_keep_same_ping_identity_inputs_separate(
    hass: HomeAssistant,
) -> None:
    """Entry IDs isolate identical node UUIDs and Ping task IDs."""
    common: dict[str, Any] = {
        "task_payloads": [{"id": 101, "name": "Shared task"}],
        "selected_task_ids": ["101"],
        "selected_stats": ["latest"],
    }
    entry_a = await _setup_ping_entry(
        hass,
        **common,
        stats_payloads=[
            {"entity_id": NODE_UUID, "task_id": 101, "latest": 10, "loss": 0}
        ],
        title="Panel A",
        url="https://ping-a.example.test",
    )
    entry_b = await _setup_ping_entry(
        hass,
        **common,
        stats_payloads=[
            {"entity_id": NODE_UUID, "task_id": 101, "latest": 20, "loss": 0}
        ],
        title="Panel B",
        url="https://ping-b.example.test",
    )
    unique_a = f"{entry_a.entry_id}:node:{NODE_UUID}:ping:101:latest"
    unique_b = f"{entry_b.entry_id}:node:{NODE_UUID}:ping:101:latest"
    entity_a = _entity_id(hass, "sensor", unique_a)
    entity_b = _entity_id(hass, "sensor", unique_b)

    assert unique_a != unique_b
    assert entity_a != entity_b
    assert float(_state(hass, entity_a).state) == 10
    assert float(_state(hass, entity_b).state) == 20


async def test_confirmed_node_removal_cleans_device_and_all_node_entities(
    hass: HomeAssistant,
) -> None:
    """The third successful omission removes only the missing node subtree."""
    bootstrap = _bootstrap(
        node_payloads=[
            _node_payload(NODE_UUID),
            _node_payload(SECOND_NODE_UUID, name="Second node"),
        ],
        status_payloads=[
            _status_payload(NODE_UUID),
            _status_payload(SECOND_NODE_UUID),
        ],
    )
    entry = await _setup_entry(hass, bootstrap)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    second_device = get_node_device(
        device_registry,
        entry.entry_id,
        SECOND_NODE_UUID,
    )
    assert second_device is not None
    second_cpu_id = _entity_id(
        hass,
        "sensor",
        _unique_id(
            entry.entry_id,
            f"node:{SECOND_NODE_UUID}",
            "cpu_usage",
        ),
    )
    remaining_nodes = parse_nodes([_node_payload(NODE_UUID)])
    remaining_statuses = parse_statuses([_status_payload(NODE_UUID)])
    coordinator = entry.runtime_data.coordinator

    with (
        patch.object(
            coordinator.client,
            "async_get_nodes",
            new=AsyncMock(return_value=remaining_nodes),
        ),
        patch.object(
            coordinator.client,
            "async_get_latest_status",
            new=AsyncMock(return_value=remaining_statuses),
        ),
    ):
        for miss in range(1, 4):
            coordinator._last_inventory_attempt = -1_000_000
            coordinator.async_set_updated_data(await coordinator._async_update_data())
            await hass.async_block_till_done()
            if miss < 3:
                assert (
                    get_node_device(
                        device_registry,
                        entry.entry_id,
                        SECOND_NODE_UUID,
                    )
                    is not None
                )

    assert get_node_device(device_registry, entry.entry_id, SECOND_NODE_UUID) is None
    assert get_node_device(device_registry, entry.entry_id, NODE_UUID) is not None
    assert get_panel_device(device_registry, entry.entry_id) is not None
    second_prefix = f"{entry.entry_id}:node:{SECOND_NODE_UUID}:"
    assert not any(
        item.unique_id.startswith(second_prefix)
        for item in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    )
    assert hass.states.get(second_cpu_id) is None


async def test_confirmed_ping_task_removal_cleans_only_that_task(
    hass: HomeAssistant,
) -> None:
    """Three task-list omissions remove its entities without touching core data."""
    entry = await _setup_ping_entry(
        hass,
        task_payloads=[
            {"id": 101, "name": "Task one"},
            {"id": 202, "name": "Task two"},
        ],
        stats_payloads=[
            {"entity_id": NODE_UUID, "task_id": 101, "latest": 10, "loss": 0},
            {"entity_id": NODE_UUID, "task_id": 202, "latest": 20, "loss": 5},
        ],
        selected_task_ids=["101", "202"],
        selected_stats=["latest", "loss"],
    )
    registry = er.async_get(hass)
    task_one_id = f"{entry.entry_id}:node:{NODE_UUID}:ping:101:latest"
    task_two_prefix = f"{entry.entry_id}:node:{NODE_UUID}:ping:202:"
    task_two_entity_id = _entity_id(hass, "sensor", f"{task_two_prefix}latest")
    remaining_tasks = parse_ping_tasks([{"id": 101, "name": "Task one"}])
    coordinator = entry.runtime_data.coordinator

    with (
        patch.object(
            coordinator.client,
            "async_get_ping_tasks",
            new=AsyncMock(return_value=remaining_tasks),
        ),
        patch.object(
            coordinator.client,
            "async_get_latest_status",
            new=AsyncMock(return_value=coordinator.data.statuses),
        ),
    ):
        for _ in range(3):
            coordinator._last_ping_tasks_attempt = -1_000_000
            coordinator.async_set_updated_data(await coordinator._async_update_data())
            await hass.async_block_till_done()

    assert registry.async_get_entity_id("sensor", DOMAIN, task_one_id) is not None
    assert not any(
        item.unique_id.startswith(task_two_prefix)
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    assert hass.states.get(task_two_entity_id) is None
    cpu_id = _entity_id(
        hass,
        "sensor",
        _unique_id(entry.entry_id, f"node:{NODE_UUID}", "cpu_usage"),
    )
    assert _state(hass, cpu_id).state != STATE_UNAVAILABLE


async def test_reload_cleans_deselected_ping_tasks_and_statistics(
    hass: HomeAssistant,
) -> None:
    """Options reload removes old task and advanced-stat registry entries."""
    task_payloads = [
        {"id": 101, "name": "Task one"},
        {"id": 202, "name": "Task two"},
    ]
    stats_payloads = [
        {"entity_id": NODE_UUID, "task_id": 101, "latest": 10, "loss": 0},
        {"entity_id": NODE_UUID, "task_id": 202, "latest": 20, "loss": 5},
    ]
    entry = await _setup_ping_entry(
        hass,
        task_payloads=task_payloads,
        stats_payloads=stats_payloads,
        selected_task_ids=["101", "202"],
        selected_stats=["latest", "loss"],
    )
    current = entry.runtime_data.coordinator.data
    bootstrap = KomariBootstrapData(
        panel=current.panel,
        nodes=current.nodes,
        statuses=current.statuses,
        capabilities=current.capabilities,
    )
    tasks = parse_ping_tasks(task_payloads)
    stats = parse_ping_metric_stats({"stats": stats_payloads})
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_PING_TASK_IDS: ["101"],
            CONF_PING_STAT_KEYS: ["latest"],
        },
    )
    with (
        patch(
            "custom_components.komari.api.KomariApiClient.async_get_bootstrap",
            new=AsyncMock(return_value=bootstrap),
        ),
        patch(
            "custom_components.komari.api.KomariApiClient.async_get_ping_tasks",
            new=AsyncMock(return_value=tasks),
        ),
        patch(
            "custom_components.komari.api.KomariApiClient.async_get_ping_stats",
            new=AsyncMock(return_value=stats),
        ),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    retained = f"{entry.entry_id}:node:{NODE_UUID}:ping:101:latest"
    removed = {
        f"{entry.entry_id}:node:{NODE_UUID}:ping:101:loss",
        f"{entry.entry_id}:node:{NODE_UUID}:ping:202:latest",
        f"{entry.entry_id}:node:{NODE_UUID}:ping:202:loss",
    }
    assert registry.async_get_entity_id("sensor", DOMAIN, retained) is not None
    assert all(
        registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
        for unique_id in removed
    )


async def test_manual_device_removal_uses_latest_successful_raw_inventory(
    hass: HomeAssistant,
) -> None:
    """Panel/current nodes are protected while one retained missing node is allowed."""
    bootstrap = _bootstrap(
        node_payloads=[
            _node_payload(NODE_UUID),
            _node_payload(SECOND_NODE_UUID, name="Second node"),
        ],
        status_payloads=[
            _status_payload(NODE_UUID),
            _status_payload(SECOND_NODE_UUID),
        ],
    )
    entry = await _setup_entry(hass, bootstrap)
    registry = dr.async_get(hass)
    panel = get_panel_device(registry, entry.entry_id)
    first = get_node_device(registry, entry.entry_id, NODE_UUID)
    second = get_node_device(registry, entry.entry_id, SECOND_NODE_UUID)
    assert panel is not None
    assert first is not None
    assert second is not None
    assert not await async_remove_config_entry_device(hass, entry, panel)
    assert not await async_remove_config_entry_device(hass, entry, second)

    coordinator = entry.runtime_data.coordinator
    with (
        patch.object(
            coordinator.client,
            "async_get_nodes",
            new=AsyncMock(return_value=parse_nodes([_node_payload(NODE_UUID)])),
        ),
        patch.object(
            coordinator.client,
            "async_get_latest_status",
            new=AsyncMock(return_value=parse_statuses([_status_payload(NODE_UUID)])),
        ),
    ):
        coordinator._last_inventory_attempt = -1_000_000
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        await hass.async_block_till_done()

    assert not await async_remove_config_entry_device(hass, entry, first)
    assert await async_remove_config_entry_device(hass, entry, second)


async def test_config_and_device_diagnostics_are_structural_and_redacted(
    hass: HomeAssistant,
) -> None:
    """Diagnostics expose counts and field presence without monitored identity."""
    node = _node_payload(name="Sensitive node")
    node.update(
        {
            "expired_at": "2027-01-01T00:00:00Z",
            "traffic_limit": 1000,
            "traffic_limit_type": "sum",
        }
    )
    entry = await _setup_ping_entry(
        hass,
        task_payloads=[
            {
                "id": 101,
                "name": "Sensitive target 203.0.113.9",
                "target": "https://203.0.113.9/private",
                "clients": [NODE_UUID],
            }
        ],
        stats_payloads=[
            {"entity_id": NODE_UUID, "task_id": 101, "latest": 12, "loss": 0}
        ],
        selected_task_ids=["101"],
        selected_stats=["latest", "loss"],
        node_payloads=[node],
        title="Sensitive panel",
        url="https://sensitive-panel.example.test",
    )
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_API_KEY: "diagnostics-secret-key",
        },
    )

    entry_diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert entry_diagnostics["versions"] == {
        "integration": "0.1.0-alpha.1",
        "komari": "1.2.3",
        "rpc": "2.0",
    }
    assert entry_diagnostics["capabilities"]["ping_tasks"] is True
    assert entry_diagnostics["capabilities"]["ping_metric_stats"] is True
    assert entry_diagnostics["nodes"] == {
        "visible": 1,
        "online": 1,
        "offline": 0,
        "status_missing": 0,
    }
    assert entry_diagnostics["options"]["selected_ping_task_count"] == 1
    assert entry_diagnostics["options"]["selected_ping_stat_count"] == 2
    assert entry_diagnostics["entities"] == {
        "expiry": 5,
        "traffic": 4,
        "ping": 2,
    }
    assert entry_diagnostics["traffic_calculation_source"] == "agent_counters"

    node_device = get_node_device(dr.async_get(hass), entry.entry_id, NODE_UUID)
    panel_device = get_panel_device(dr.async_get(hass), entry.entry_id)
    assert node_device is not None
    assert panel_device is not None
    node_diagnostics = await async_get_device_diagnostics(hass, entry, node_device)
    panel_diagnostics = await async_get_device_diagnostics(hass, entry, panel_device)
    assert node_diagnostics["device_type"] == "node"
    assert len(node_diagnostics["node_identifier"]) == 16
    assert node_diagnostics["node_identifier"] != NODE_UUID
    assert node_diagnostics["inventory_present"] is True
    assert node_diagnostics["status_present"] is True
    assert all(node_diagnostics["inventory_field_presence"].values()) is False
    assert all(
        isinstance(value, bool)
        for value in node_diagnostics["inventory_field_presence"].values()
    )
    assert all(
        isinstance(value, bool)
        for value in node_diagnostics["status_field_presence"].values()
    )
    assert panel_diagnostics["device_type"] == "panel"

    serialized = json.dumps(
        {
            "entry": entry_diagnostics,
            "node": node_diagnostics,
            "panel": panel_diagnostics,
        }
    )
    for sensitive in (
        "diagnostics-secret-key",
        "sensitive-panel.example.test",
        "Sensitive panel",
        "Sensitive node",
        "Sensitive target",
        "203.0.113.9",
        NODE_UUID,
    ):
        assert sensitive not in serialized


def test_nested_diagnostics_defense_redacts_sensitive_keys() -> None:
    """Defense-in-depth redaction traverses nested mappings and arrays."""
    redacted = redact_diagnostics(
        {
            "safe": True,
            "nested": {
                "Authorization": "Bearer nested-secret",
                "items": [
                    {
                        "uuid": NODE_UUID,
                        "target": "https://203.0.113.9/private",
                    }
                ],
            },
        }
    )
    serialized = json.dumps(redacted)
    assert redacted["safe"] is True
    assert "nested-secret" not in serialized
    assert NODE_UUID not in serialized
    assert "203.0.113.9" not in serialized


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
    traffic_metadata = {
        description.key: description
        for description in TRAFFIC_METADATA_SENSOR_DESCRIPTIONS
    }
    traffic_status = {
        description.key: description
        for description in TRAFFIC_STATUS_SENSOR_DESCRIPTIONS
    }
    assert traffic_metadata["traffic_limit"].entity_registry_enabled_default is True
    assert (
        traffic_metadata["traffic_limit_type"].entity_registry_enabled_default is False
    )
    assert (
        traffic_status["traffic_used_by_limit_rule"].entity_registry_enabled_default
        is True
    )
    assert traffic_status["traffic_limit_usage"].entity_registry_enabled_default is True
    assert all(
        description.entity_registry_enabled_default
        for description in PING_SENSOR_DESCRIPTIONS
    )
