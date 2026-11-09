"""Regression tests for Ping identity budgets and registration boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from custom_components.komari.const import DOMAIN
from custom_components.komari.lifecycle import registry_ping_entity_keys
from custom_components.komari.models import KomariPingSeriesKey, parse_ping_tasks
from custom_components.komari.ping import (
    observed_ping_entity_keys,
    predicted_ping_entity_keys,
    retained_ping_entity_keys,
)
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def test_assignment_prediction_uses_visible_clients_and_not_default_on() -> None:
    """Duplicate, hidden and default-on-only clients never inflate the budget."""
    tasks = parse_ping_tasks(
        [
            {"id": 1, "clients": ["node-0", "node-0", "hidden"]},
            {"id": 2, "clients": [], "default_on": True},
            {"id": 3, "clients": None},
            {"id": 4, "clients": ["node-1"]},
        ]
    )
    assert predicted_ping_entity_keys(
        tasks,
        node_ids={"node-0", "node-1"},
        task_ids={"1", "2", "3"},
        stat_keys={"latest", "loss"},
        assignments_current=True,
    ) == {("node-0", "1", "latest"), ("node-0", "1", "loss")}


@pytest.mark.parametrize("reason", ["unknown", "stale", "missing", "absent"])
def test_uncertain_assignments_keep_all_visible_node_candidates(reason: str) -> None:
    """Invalid metadata and missing grace cannot turn into authoritative empty."""
    tasks = parse_ping_tasks(
        [{"id": 1, **({} if reason == "unknown" else {"clients": []})}]
    )
    assert predicted_ping_entity_keys(
        {} if reason == "absent" else tasks,
        node_ids={"node-0", "node-1"},
        task_ids={"1"},
        stat_keys={"latest"},
        assignments_current=reason != "stale",
        missing_tasks={"1"} if reason == "missing" else (),
    ) == {("node-0", "1", "latest"), ("node-1", "1", "latest")}


def test_observed_series_include_all_selected_fields_and_deduplicate() -> None:
    """Observed history is independent of assignments and field value presence."""
    assert observed_ping_entity_keys(
        [
            KomariPingSeriesKey("node-0", "1"),
            KomariPingSeriesKey("node-0", "1"),
            KomariPingSeriesKey("hidden", "1"),
            KomariPingSeriesKey("node-0", "2"),
        ],
        node_ids={"node-0"},
        task_ids={"1"},
        stat_keys={"latest", "loss"},
    ) == {("node-0", "1", "latest"), ("node-0", "1", "loss")}


def test_retained_identity_filter_keeps_only_actual_selected_registrations() -> None:
    """Partial fields stay counted until deselection or authoritative deletion."""
    assert retained_ping_entity_keys(
        {
            ("historical", "1", "latest"),
            ("removed-node", "1", "latest"),
            ("historical", "2", "latest"),
            ("historical", "3", "latest"),
            ("historical", "1", "p99"),
        },
        task_ids={"1", "2"},
        stat_keys={"latest", "loss"},
        removed_nodes={"removed-node"},
        removed_tasks={"2"},
    ) == {("historical", "1", "latest")}


async def test_registry_ping_keys_exclude_other_entries_platforms_and_invalid_ids(
    hass: HomeAssistant,
) -> None:
    """Only matching Komari sensors occupy this entry's budget; disabled counts."""
    entries = [MockConfigEntry(domain=DOMAIN), MockConfigEntry(domain=DOMAIN)]
    registry = er.async_get(hass)
    for entry in entries:
        entry.add_to_hass(hass)
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}:node:shared:ping:1:latest",
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    entry = entries[0]
    for domain, platform, unique_id in (
        ("binary_sensor", DOMAIN, f"{entry.entry_id}:node:shared:ping:1:loss"),
        ("sensor", "other", f"{entry.entry_id}:node:shared:ping:1:loss"),
        ("sensor", DOMAIN, "unscoped:node:shared:ping:1:latest"),
        ("sensor", DOMAIN, f"{entry.entry_id}:node::ping:1:latest"),
        ("sensor", DOMAIN, f"{entry.entry_id}:node:shared:ping:1:invented"),
    ):
        registry.async_get_or_create(domain, platform, unique_id, config_entry=entry)
    assert registry_ping_entity_keys(registry, entry.entry_id) == {
        ("shared", "1", "latest")
    }
    assert registry_ping_entity_keys(registry, entries[1].entry_id) == {
        ("shared", "1", "latest")
    }
