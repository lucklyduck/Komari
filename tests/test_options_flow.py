"""Tests for the per-panel multi-step Komari options flow."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

from custom_components.komari.const import (
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_PING_INTERVAL,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    METHOD_LATEST_STATUS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_TASKS,
)
from custom_components.komari.coordinator import KomariSnapshot
from custom_components.komari.models import (
    KomariPanelInfo,
    parse_nodes,
    parse_ping_tasks,
    parse_statuses,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from typing import Any

    from custom_components.komari.data import KomariRuntimeData
    from homeassistant.core import HomeAssistant


def _snapshot(*, node_count: int = 2, task_count: int = 2) -> KomariSnapshot:
    """Create a loaded snapshot with sanitized visible nodes and Ping tasks."""
    nodes = parse_nodes(
        [
            {"uuid": f"node-{index}", "name": f"Node {index}"}
            for index in range(node_count)
        ]
    )
    statuses = parse_statuses(
        [{"client": node_uuid, "online": True} for node_uuid in nodes]
    )
    tasks = parse_ping_tasks(
        [
            {
                "id": index,
                "name": f"Task {index}",
                "clients": list(nodes),
                "type": "tcp",
                "interval": 30,
            }
            for index in range(task_count)
        ]
    )
    return KomariSnapshot(
        panel=KomariPanelInfo(
            name="Fixture panel",
            version="1.0.0",
            build_hash="fixture",
            rpc_version="2.0",
            private=False,
        ),
        nodes=nodes,
        statuses=statuses,
        capabilities=frozenset(
            {METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES, METHOD_PUBLIC_PING_TASKS}
        ),
        ping_tasks=tasks,
    )


def _entry(hass: HomeAssistant, snapshot: KomariSnapshot) -> MockConfigEntry:
    """Add an entry with enough loaded runtime data for task discovery."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    runtime = SimpleNamespace(coordinator=SimpleNamespace(data=snapshot))
    entry.runtime_data = cast("KomariRuntimeData", runtime)
    return entry


async def _start(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> dict[str, Any]:
    """Start an options flow and return its first form."""
    result = cast(
        "dict[str, Any]",
        await hass.config_entries.options.async_init(entry.entry_id),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "monitoring"
    return result


async def _to_ping_tasks(
    hass: HomeAssistant,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Advance monitoring and entity-family steps."""
    result = await _configure(
        hass,
        result,
        {CONF_SCAN_INTERVAL: 45, CONF_PING_INTERVAL: 600},
    )
    assert result["step_id"] == "entities"
    result = await _configure(
        hass,
        result,
        {
            CONF_ENABLE_EXPIRY_ENTITIES: False,
            CONF_ENABLE_TRAFFIC_ENTITIES: True,
        },
    )
    assert result["step_id"] == "ping_tasks"
    return result


async def _configure(
    hass: HomeAssistant,
    result: dict[str, Any],
    user_input: dict[str, object],
) -> dict[str, Any]:
    """Continue a flow while retaining a convenient asserted test shape."""
    return cast(
        "dict[str, Any]",
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input,
        ),
    )


async def test_options_flow_saves_once_and_reload_is_automatic(
    hass: HomeAssistant,
) -> None:
    """Task/stat selection persists and triggers exactly one entry reload."""
    entry = _entry(hass, _snapshot())
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(
        hass,
        result,
        {CONF_PING_TASK_IDS: ["0", "1"]},
    )
    assert result["step_id"] == "ping_stats"
    result = await _configure(
        hass,
        result,
        {CONF_PING_STAT_KEYS: ["latest", "loss", "p99"]},
    )
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["entity_count"] == "12"

    result = await _configure(
        hass,
        result,
        {"confirm": False},
    )
    assert result["errors"] == {"base": "not_confirmed"}

    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as reload_entry:
        result = await _configure(
            hass,
            result,
            {"confirm": True},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SCAN_INTERVAL: 45,
        CONF_PING_INTERVAL: 600,
        CONF_ENABLE_EXPIRY_ENTITIES: False,
        CONF_ENABLE_TRAFFIC_ENTITIES: True,
        CONF_PING_TASK_IDS: ["0", "1"],
        CONF_PING_STAT_KEYS: ["latest", "loss", "p99"],
    }
    assert reload_entry.await_count == 1


async def test_empty_task_selection_skips_statistics(hass: HomeAssistant) -> None:
    """No selected Ping tasks proceeds directly to the preview."""
    entry = _entry(hass, _snapshot())
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(
        hass,
        result,
        {CONF_PING_TASK_IDS: []},
    )
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["entity_count"] == "0"


async def test_no_ping_capability_hides_both_ping_steps(
    hass: HomeAssistant,
) -> None:
    """Panels without task discovery show only monitoring/entities/confirm."""
    snapshot = _snapshot(task_count=0)
    snapshot = KomariSnapshot(
        panel=snapshot.panel,
        nodes=snapshot.nodes,
        statuses=snapshot.statuses,
        capabilities=frozenset({METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES}),
    )
    entry = _entry(hass, snapshot)
    result = await _start(hass, entry)
    result = await _configure(
        hass,
        result,
        {CONF_SCAN_INTERVAL: 30, CONF_PING_INTERVAL: 300},
    )
    result = await _configure(
        hass,
        result,
        {
            CONF_ENABLE_EXPIRY_ENTITIES: True,
            CONF_ENABLE_TRAFFIC_ENTITIES: True,
        },
    )
    assert result["step_id"] == "confirm"


async def test_task_and_stat_validation_errors_stay_on_current_step(
    hass: HomeAssistant,
) -> None:
    """Selections cannot exceed task limits or inject unknown identities."""
    entry = _entry(hass, _snapshot(task_count=9))
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(
        hass,
        result,
        {CONF_PING_TASK_IDS: [str(index) for index in range(9)]},
    )
    assert result["errors"] == {"base": "too_many_ping_tasks"}
    result = await _configure(
        hass,
        result,
        {CONF_PING_TASK_IDS: ["0"]},
    )
    result = await _configure(
        hass,
        result,
        {CONF_PING_STAT_KEYS: []},
    )
    assert result["errors"] == {"base": "ping_stats_required"}


async def test_entity_estimate_over_limit_blocks_save(hass: HomeAssistant) -> None:
    """The visible-node maximum is enforced before options are persisted."""
    entry = _entry(hass, _snapshot(node_count=20, task_count=8))
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(
        hass,
        result,
        {CONF_PING_TASK_IDS: [str(index) for index in range(8)]},
    )
    result = await _configure(
        hass,
        result,
        {CONF_PING_STAT_KEYS: ["latest", "loss"]},
    )

    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "too_many_ping_entities"}
    assert result["description_placeholders"]["entity_count"] == "320"
    result = await _configure(
        hass,
        result,
        {"confirm": True},
    )
    assert result["type"] is FlowResultType.FORM
    assert entry.options == {}
