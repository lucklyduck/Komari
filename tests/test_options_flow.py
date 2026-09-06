"""Tests for the per-panel multi-step Komari options flow."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.komari.const import (
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_METRIC_INTERVAL,
    CONF_METRIC_KEYS,
    CONF_METRIC_TAG_FILTERS,
    CONF_PING_INTERVAL,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    METHOD_LATEST_STATUS,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_METRIC_DEFINITIONS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_RECORDS,
    METHOD_PUBLIC_PING_TASKS,
    METHOD_PUBLIC_QUERY_METRICS,
)
from custom_components.komari.coordinator import KomariSnapshot
from custom_components.komari.models import (
    KomariMetricSeriesKey,
    KomariMetricValue,
    KomariPanelInfo,
    parse_metric_definitions,
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


def _snapshot(
    *,
    node_count: int = 2,
    task_count: int = 2,
    ping_stats_capability: bool = True,
) -> KomariSnapshot:
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
    capabilities = {
        METHOD_LATEST_STATUS,
        METHOD_PUBLIC_NODES,
        METHOD_PUBLIC_PING_TASKS,
    }
    if ping_stats_capability:
        capabilities.add(METHOD_PING_METRIC_STATS)
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
        capabilities=frozenset(capabilities),
        ping_tasks=tasks,
    )


def _entry(
    hass: HomeAssistant,
    snapshot: KomariSnapshot | None,
    *,
    options: dict[str, object] | None = None,
    client: object | None = None,
) -> MockConfigEntry:
    """Add an entry with enough loaded runtime data for task discovery."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {})
    entry.add_to_hass(hass)
    if snapshot is not None:
        runtime = SimpleNamespace(
            coordinator=SimpleNamespace(data=snapshot),
            client=client,
        )
        entry.runtime_data = cast("KomariRuntimeData", runtime)
    return entry


def _metric_snapshot() -> tuple[
    KomariSnapshot,
    KomariMetricSeriesKey,
    KomariMetricValue,
]:
    """Return a discoverable custom Metric and one visible node-scoped series."""
    snapshot = _snapshot()
    series_key = KomariMetricSeriesKey(
        "custom.rate",
        "node-0",
        (("region", "edge"),),
    )
    value = KomariMetricValue(value=12.5, observed_at=None)
    return (
        replace(
            snapshot,
            capabilities=(
                snapshot.capabilities
                | {
                    METHOD_PUBLIC_METRIC_DEFINITIONS,
                    METHOD_PUBLIC_QUERY_METRICS,
                }
            ),
            metric_definitions=parse_metric_definitions(
                [
                    {
                        "name": "custom.rate",
                        "description": {"en": "Custom rate"},
                        "type": "gauge",
                        "unit": "bytes/s",
                        "retention_days": 7,
                    }
                ]
            ),
        ),
        series_key,
        value,
    )


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
        CONF_METRIC_INTERVAL: 600,
        CONF_METRIC_KEYS: [],
        CONF_METRIC_TAG_FILTERS: {},
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


async def test_task_discovery_without_stats_capability_hides_ping_steps(
    hass: HomeAssistant,
) -> None:
    """A task list alone cannot offer a Ping configuration that never updates."""
    entry = _entry(hass, _snapshot(ping_stats_capability=False))
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
    assert result["description_placeholders"]["entity_count"] == "0"


@pytest.mark.parametrize(
    "availability",
    ["runtime_missing", "capability_missing", "task_list_empty"],
)
async def test_unavailable_ping_discovery_preserves_existing_selection(
    hass: HomeAssistant,
    availability: str,
) -> None:
    """An unavailable Ping editor never silently drops saved task selections."""
    snapshot: KomariSnapshot | None
    if availability == "runtime_missing":
        snapshot = None
    elif availability == "capability_missing":
        available = _snapshot()
        snapshot = KomariSnapshot(
            panel=available.panel,
            nodes=available.nodes,
            statuses=available.statuses,
            capabilities=frozenset({METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES}),
            ping_tasks=available.ping_tasks,
        )
    else:
        snapshot = _snapshot(task_count=0)

    entry = _entry(
        hass,
        snapshot,
        options={
            CONF_PING_TASK_IDS: ["0"],
            CONF_PING_STAT_KEYS: ["latest", "loss"],
        },
    )
    result = await _start(hass, entry)
    result = await _configure(
        hass,
        result,
        {CONF_SCAN_INTERVAL: 45, CONF_PING_INTERVAL: 600},
    )
    result = await _configure(
        hass,
        result,
        {
            CONF_ENABLE_EXPIRY_ENTITIES: False,
            CONF_ENABLE_TRAFFIC_ENTITIES: True,
        },
    )

    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["entity_count"] == "0"
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ):
        result = await _configure(hass, result, {"confirm": True})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PING_TASK_IDS] == ["0"]
    assert entry.options[CONF_PING_STAT_KEYS] == ["latest", "loss"]


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


async def test_public_records_only_rejects_multi_task_fanout(
    hass: HomeAssistant,
) -> None:
    """Options prevents a selection that cannot be fetched in one request."""
    snapshot = _snapshot()
    snapshot = KomariSnapshot(
        panel=snapshot.panel,
        nodes=snapshot.nodes,
        statuses=snapshot.statuses,
        capabilities=frozenset(
            {
                METHOD_LATEST_STATUS,
                METHOD_PUBLIC_NODES,
                METHOD_PUBLIC_PING_TASKS,
                METHOD_PUBLIC_PING_RECORDS,
            }
        ),
        ping_tasks=snapshot.ping_tasks,
    )
    entry = _entry(hass, snapshot)
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(
        hass,
        result,
        {CONF_PING_TASK_IDS: ["0", "1"]},
    )

    assert result["step_id"] == "ping_tasks"
    assert result["errors"] == {"base": "ping_batch_unsupported"}


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


async def test_metric_options_preview_uses_exact_filters_and_saves_once(
    hass: HomeAssistant,
) -> None:
    """Metric selection performs one bounded preview before options are persisted."""
    snapshot, series_key, value = _metric_snapshot()
    query_metrics = AsyncMock(return_value={series_key: value})
    entry = _entry(
        hass,
        snapshot,
        client=SimpleNamespace(async_query_metrics=query_metrics),
    )
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(hass, result, {CONF_PING_TASK_IDS: []})
    assert result["step_id"] == "metrics"

    result = await _configure(
        hass,
        result,
        {CONF_METRIC_INTERVAL: 600, CONF_METRIC_KEYS: ["custom.rate"]},
    )
    assert result["step_id"] == "metric_filters"
    result = await _configure(
        hass,
        result,
        {CONF_METRIC_TAG_FILTERS: {"custom.rate": {"region": "edge"}}},
    )
    assert result["step_id"] == "metric_preview"
    assert result["description_placeholders"]["entity_count"] == "1"
    query_metrics.assert_awaited_once_with(
        snapshot.capabilities,
        metric_keys=("custom.rate",),
        entity_ids=("node-0", "node-1"),
        tags={"region": "edge"},
        hours=4,
        max_points=1,
        aggregation="last",
        fill_empty=False,
    )

    result = await _configure(hass, result, {"confirm": False})
    assert result["errors"] == {"base": "not_confirmed"}
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ) as reload_entry:
        result = await _configure(hass, result, {"confirm": True})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_METRIC_INTERVAL] == 600
    assert entry.options[CONF_METRIC_KEYS] == ["custom.rate"]
    assert entry.options[CONF_METRIC_TAG_FILTERS] == {"custom.rate": {"region": "edge"}}
    reload_entry.assert_awaited_once_with(entry.entry_id)


async def test_metric_options_retain_unavailable_selection_unless_explicitly_cleared(
    hass: HomeAssistant,
) -> None:
    """A missing optional Metric API cannot silently erase a dormant selection."""
    snapshot = _snapshot(task_count=0)
    snapshot = replace(
        snapshot,
        capabilities=frozenset({METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES}),
    )
    entry = _entry(
        hass,
        snapshot,
        options={
            CONF_METRIC_KEYS: ["custom.rate"],
            CONF_METRIC_TAG_FILTERS: {"custom.rate": {"region": "edge"}},
        },
    )
    result = await _start(hass, entry)
    result = await _configure(
        hass,
        result,
        {CONF_SCAN_INTERVAL: 45, CONF_PING_INTERVAL: 600},
    )
    result = await _configure(
        hass,
        result,
        {
            CONF_ENABLE_EXPIRY_ENTITIES: False,
            CONF_ENABLE_TRAFFIC_ENTITIES: True,
        },
    )
    assert result["step_id"] == "metrics_unavailable"

    result = await _configure(
        hass,
        result,
        {"clear_saved_metric_selections": False},
    )
    assert result["step_id"] == "confirm"
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ):
        result = await _configure(hass, result, {"confirm": True})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_METRIC_KEYS] == ["custom.rate"]
    assert entry.options[CONF_METRIC_TAG_FILTERS] == {"custom.rate": {"region": "edge"}}


async def test_metric_options_explicit_clear_removes_dormant_selection(
    hass: HomeAssistant,
) -> None:
    """Only the opt-in unavailable-page control may clear saved Metric settings."""
    snapshot = _snapshot(task_count=0)
    snapshot = replace(
        snapshot,
        capabilities=frozenset({METHOD_LATEST_STATUS, METHOD_PUBLIC_NODES}),
    )
    entry = _entry(
        hass,
        snapshot,
        options={
            CONF_METRIC_KEYS: ["custom.rate"],
            CONF_METRIC_TAG_FILTERS: {"custom.rate": {"region": "edge"}},
        },
    )
    result = await _start(hass, entry)
    result = await _configure(
        hass,
        result,
        {CONF_SCAN_INTERVAL: 45, CONF_PING_INTERVAL: 600},
    )
    result = await _configure(
        hass,
        result,
        {
            CONF_ENABLE_EXPIRY_ENTITIES: False,
            CONF_ENABLE_TRAFFIC_ENTITIES: True,
        },
    )
    assert result["step_id"] == "metrics_unavailable"
    result = await _configure(
        hass,
        result,
        {"clear_saved_metric_selections": True},
    )
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(return_value=True),
    ):
        result = await _configure(hass, result, {"confirm": True})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_METRIC_KEYS] == []
    assert entry.options[CONF_METRIC_TAG_FILTERS] == {}


async def test_metric_preview_handles_connection_failure_and_missing_definition(
    hass: HomeAssistant,
) -> None:
    """Preview failure blocks saving, while a saved missing key remains dormant."""
    snapshot, _, _ = _metric_snapshot()
    failed_entry = _entry(
        hass,
        snapshot,
        client=SimpleNamespace(
            async_query_metrics=AsyncMock(side_effect=RuntimeError())
        ),
    )
    result = await _to_ping_tasks(hass, await _start(hass, failed_entry))
    result = await _configure(hass, result, {CONF_PING_TASK_IDS: []})
    result = await _configure(
        hass,
        result,
        {CONF_METRIC_INTERVAL: 600, CONF_METRIC_KEYS: ["custom.rate"]},
    )
    result = await _configure(
        hass,
        result,
        {CONF_METRIC_TAG_FILTERS: {}},
    )
    assert result["step_id"] == "metric_preview"
    assert result["errors"] == {"base": "metric_preview_failed"}

    missing_snapshot = replace(
        _snapshot(),
        capabilities=frozenset(
            {
                METHOD_LATEST_STATUS,
                METHOD_PUBLIC_NODES,
                METHOD_PUBLIC_PING_TASKS,
                METHOD_PING_METRIC_STATS,
                METHOD_PUBLIC_METRIC_DEFINITIONS,
                METHOD_PUBLIC_QUERY_METRICS,
            }
        ),
    )
    query_metrics = AsyncMock()
    missing_entry = _entry(
        hass,
        missing_snapshot,
        options={CONF_METRIC_KEYS: ["custom.removed"]},
        client=SimpleNamespace(async_query_metrics=query_metrics),
    )
    result = await _to_ping_tasks(hass, await _start(hass, missing_entry))
    result = await _configure(hass, result, {CONF_PING_TASK_IDS: []})
    assert result["step_id"] == "metrics"
    result = await _configure(
        hass,
        result,
        {CONF_METRIC_INTERVAL: 600, CONF_METRIC_KEYS: ["custom.removed"]},
    )
    result = await _configure(hass, result, {CONF_METRIC_TAG_FILTERS: {}})
    assert result["step_id"] == "metric_preview"
    assert result["description_placeholders"]["entity_count"] == "0"
    query_metrics.assert_not_awaited()


async def test_metric_preview_blocks_high_cardinality_and_invalid_filters(
    hass: HomeAssistant,
) -> None:
    """The flow rejects malformed filters and estimates above the hard entity cap."""
    snapshot, _, value = _metric_snapshot()
    values = {
        KomariMetricSeriesKey(
            "custom.rate",
            "node-0",
            (("series", str(index)),),
        ): value
        for index in range(129)
    }
    entry = _entry(
        hass,
        snapshot,
        client=SimpleNamespace(async_query_metrics=AsyncMock(return_value=values)),
    )
    result = await _to_ping_tasks(hass, await _start(hass, entry))
    result = await _configure(hass, result, {CONF_PING_TASK_IDS: []})
    result = await _configure(
        hass,
        result,
        {CONF_METRIC_INTERVAL: 600, CONF_METRIC_KEYS: ["custom.rate"]},
    )
    result = await _configure(
        hass,
        result,
        {CONF_METRIC_TAG_FILTERS: {"custom.rate": ["not-an-object"]}},
    )
    assert result["step_id"] == "metric_filters"
    assert result["errors"] == {"base": "invalid_metric_filter"}

    result = await _configure(
        hass,
        result,
        {CONF_METRIC_TAG_FILTERS: {}},
    )
    assert result["step_id"] == "metric_preview"
    assert result["errors"] == {"base": "too_many_metric_entities"}
    assert result["description_placeholders"]["entity_count"] == "129"
    result = await _configure(hass, result, {"confirm": True})
    assert result["type"] is FlowResultType.FORM
    assert entry.options == {}
