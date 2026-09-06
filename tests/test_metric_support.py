"""Tests for dynamic-Metric options, Repairs, and diagnostics surfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from custom_components.komari.const import (
    CONF_METRIC_KEYS,
    CONF_METRIC_TAG_FILTERS,
    DOMAIN,
    METHOD_LATEST_STATUS,
    METHOD_PUBLIC_METRIC_DEFINITIONS,
    METHOD_PUBLIC_QUERY_METRICS,
)
from custom_components.komari.coordinator import (
    BRANCH_METRIC_DEFINITIONS,
    BRANCH_METRIC_VALUES,
    BRANCH_PING_TASKS,
    KomariBranchState,
)
from custom_components.komari.diagnostics import async_get_config_entry_diagnostics
from custom_components.komari.repairs import (
    ISSUE_METRIC_CAPABILITY_UNAVAILABLE,
    ISSUE_METRIC_CONFIGURATION_INVALID,
    ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED,
    ISSUE_SELECTED_METRIC_MISSING,
    issue_id,
    update_metric_entity_limit_repair,
    update_repairs,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from custom_components.komari.data import KomariConfigEntry
    from homeassistant.core import HomeAssistant


def _entry_with_metric_runtime(
    hass: HomeAssistant,
    *,
    options: dict[str, object],
    capabilities: frozenset[str] = frozenset({METHOD_LATEST_STATUS}),
    removed_metrics: frozenset[str] = frozenset(),
    definitions: dict[str, object] | None = None,
    values: dict[object, object] | None = None,
) -> MockConfigEntry:
    """Build the smallest entry/runtime fixture needed by support surfaces."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fixture panel",
        data={CONF_URL: "https://example.test", CONF_VERIFY_SSL: True},
        options=options,
    )
    entry.add_to_hass(hass)
    fetched_at = datetime.now(tz=UTC)
    snapshot = SimpleNamespace(
        panel=SimpleNamespace(version="1.4.3", rpc_version="2.0"),
        capabilities=capabilities,
        nodes={},
        statuses={},
        ping_tasks={},
        ping_stats={},
        metric_definitions=definitions or {},
        metric_values=values or {},
    )
    coordinator = SimpleNamespace(
        data=snapshot,
        branch_states={
            BRANCH_PING_TASKS: KomariBranchState(fetched_at=fetched_at),
            BRANCH_METRIC_DEFINITIONS: KomariBranchState(fetched_at=fetched_at),
            BRANCH_METRIC_VALUES: KomariBranchState(fetched_at=fetched_at),
        },
        confirmed_removed_metric_keys=removed_metrics,
        metric_values_available=True,
        last_update_success=True,
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    return entry


async def test_metric_repairs_are_entry_scoped_and_recover(
    hass: HomeAssistant,
) -> None:
    """Unavailable, removed, and malformed Metric selections create safe issues."""
    entry = _entry_with_metric_runtime(
        hass,
        options={
            CONF_METRIC_KEYS: ["custom.rate"],
            CONF_METRIC_TAG_FILTERS: {"custom.rate": ["malformed"]},
        },
        removed_metrics=frozenset({"custom.rate"}),
    )

    update_repairs(hass, cast("KomariConfigEntry", entry))
    registry = ir.async_get(hass)
    for issue_type in (
        ISSUE_METRIC_CAPABILITY_UNAVAILABLE,
        ISSUE_SELECTED_METRIC_MISSING,
        ISSUE_METRIC_CONFIGURATION_INVALID,
    ):
        issue = registry.async_get_issue(DOMAIN, issue_id(entry.entry_id, issue_type))
        assert issue is not None
        assert issue.translation_key == issue_type
        assert issue.translation_placeholders is not None
        assert "custom.rate" not in str(issue.translation_placeholders)

    coordinator = entry.runtime_data.coordinator
    coordinator.data.capabilities = frozenset(
        {
            METHOD_LATEST_STATUS,
            METHOD_PUBLIC_METRIC_DEFINITIONS,
            METHOD_PUBLIC_QUERY_METRICS,
        }
    )
    coordinator.confirmed_removed_metric_keys = frozenset()
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_METRIC_KEYS: ["custom.rate"],
            CONF_METRIC_TAG_FILTERS: {"custom.rate": {"region": "edge"}},
        },
    )
    update_repairs(hass, cast("KomariConfigEntry", entry))

    for issue_type in (
        ISSUE_METRIC_CAPABILITY_UNAVAILABLE,
        ISSUE_SELECTED_METRIC_MISSING,
        ISSUE_METRIC_CONFIGURATION_INVALID,
    ):
        assert (
            registry.async_get_issue(DOMAIN, issue_id(entry.entry_id, issue_type))
            is None
        )


async def test_metric_entity_limit_repair_hides_series_identity(
    hass: HomeAssistant,
) -> None:
    """The sensor candidate count drives a translated entry-scoped warning."""
    entry = _entry_with_metric_runtime(
        hass,
        options={CONF_METRIC_KEYS: ["custom.rate"]},
    )
    update_metric_entity_limit_repair(
        hass,
        cast("KomariConfigEntry", entry),
        entity_count=129,
    )
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(
        DOMAIN,
        issue_id(entry.entry_id, ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED),
    )
    assert issue is not None
    assert issue.translation_placeholders == {
        "entry_title": "Fixture panel",
        "entity_count": "129",
        "entity_limit": "128",
    }

    update_metric_entity_limit_repair(
        hass,
        cast("KomariConfigEntry", entry),
        entity_count=128,
    )
    assert (
        registry.async_get_issue(
            DOMAIN,
            issue_id(entry.entry_id, ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED),
        )
        is None
    )


async def test_metric_diagnostics_return_only_counts_and_capabilities(
    hass: HomeAssistant,
) -> None:
    """Metric diagnostics expose structural state without raw identities or values."""
    entry = _entry_with_metric_runtime(
        hass,
        options={
            CONF_METRIC_KEYS: ["custom.secret_rate"],
            CONF_METRIC_TAG_FILTERS: {"custom.secret_rate": {"region": "private-edge"}},
        },
        capabilities=frozenset(
            {
                METHOD_LATEST_STATUS,
                METHOD_PUBLIC_METRIC_DEFINITIONS,
                METHOD_PUBLIC_QUERY_METRICS,
            }
        ),
        definitions={"custom.secret_rate": object()},
        values={object(): 42.0},
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}:node:private-node:metric:custom.secret_rate:hash",
        config_entry=entry,
    )

    diagnostics = await async_get_config_entry_diagnostics(
        hass,
        cast("KomariConfigEntry", entry),
    )

    assert diagnostics["capabilities"]["metric_definitions"] is True
    assert diagnostics["capabilities"]["metric_values"] is True
    assert diagnostics["options"]["metric_interval"] == 600
    assert diagnostics["options"]["selected_metric_count"] == 1
    assert diagnostics["metrics"] == {
        "definition_count": 1,
        "selected_definition_count": 1,
        "active_series_count": 1,
        "values_available": True,
        "confirmed_removed_definition_count": 0,
        "configuration_invalid": False,
        "skipped_series_count": 0,
        "skipped_point_count": 0,
    }
    assert diagnostics["entities"]["metric"] == 1
    assert "custom.secret_rate" not in str(diagnostics)
    assert "private-edge" not in str(diagnostics)
    assert "private-node" not in str(diagnostics)
    assert "42.0" not in str(diagnostics)
