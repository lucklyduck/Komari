"""Tests for Komari config-entry options and migration."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from custom_components.komari import async_migrate_entry
from custom_components.komari.const import (
    CONF_API_KEY,
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
)
from custom_components.komari.options import KomariOptions, get_options
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from custom_components.komari.data import KomariConfigEntry
    from homeassistant.core import HomeAssistant


def test_options_defaults_are_read_without_mutating_entry() -> None:
    """A P1 entry receives runtime defaults without persisted option writes."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    options = get_options(cast("KomariConfigEntry", entry))

    assert options == KomariOptions(
        scan_interval=30,
        ping_interval=300,
        enable_expiry_entities=True,
        enable_traffic_entities=True,
        ping_task_ids=(),
        ping_stat_keys=("latest", "loss"),
        metric_interval=600,
        metric_keys=(),
        metric_tag_filters={},
        invalid_metric_filter_keys=frozenset(),
        metric_configuration_invalid=False,
    )
    assert entry.options == {}


def test_options_are_bounded_filtered_and_serialized() -> None:
    """Corrupt/manual values cannot bypass runtime limits."""
    options = KomariOptions.from_mapping(
        {
            CONF_SCAN_INTERVAL: 1,
            CONF_PING_INTERVAL: 9999,
            CONF_ENABLE_EXPIRY_ENTITIES: False,
            CONF_ENABLE_TRAFFIC_ENTITIES: "false",
            CONF_PING_TASK_IDS: [1, "2", "2", "", None, *range(3, 20)],
            CONF_PING_STAT_KEYS: ["p99", "latest", "unsupported", "p99"],
            CONF_METRIC_INTERVAL: 1,
            CONF_METRIC_KEYS: ["custom.rate", "custom.count", "custom.rate"],
            CONF_METRIC_TAG_FILTERS: {
                "custom.rate": {"region": "edge", "role": "web"},
                "custom.count": {"region": "edge"},
            },
        }
    )

    assert options.scan_interval == 15
    assert options.ping_interval == 1800
    assert options.enable_expiry_entities is False
    assert options.enable_traffic_entities is True
    assert options.ping_task_ids == ("1", "2", "3", "4", "5", "6", "7", "8")
    assert options.ping_stat_keys == ("latest", "p99")
    assert options.metric_interval == 300
    assert options.metric_keys == ("custom.rate", "custom.count")
    assert options.metric_tag_filters == {
        "custom.rate": {"region": "edge", "role": "web"},
        "custom.count": {"region": "edge"},
    }
    assert options.invalid_metric_filter_keys == frozenset()
    assert options.metric_configuration_invalid is False
    assert options.as_dict() == {
        CONF_SCAN_INTERVAL: 15,
        CONF_PING_INTERVAL: 1800,
        CONF_ENABLE_EXPIRY_ENTITIES: False,
        CONF_ENABLE_TRAFFIC_ENTITIES: True,
        CONF_PING_TASK_IDS: ["1", "2", "3", "4", "5", "6", "7", "8"],
        CONF_PING_STAT_KEYS: ["latest", "p99"],
        CONF_METRIC_INTERVAL: 300,
        CONF_METRIC_KEYS: ["custom.rate", "custom.count"],
        CONF_METRIC_TAG_FILTERS: {
            "custom.rate": {"region": "edge", "role": "web"},
            "custom.count": {"region": "edge"},
        },
    }


def test_invalid_option_types_fall_back_to_defaults() -> None:
    """Strings, booleans, and empty stat selections do not leak into runtime."""
    options = KomariOptions.from_mapping(
        {
            CONF_SCAN_INTERVAL: True,
            CONF_PING_INTERVAL: "60",
            CONF_PING_TASK_IDS: "1",
            CONF_PING_STAT_KEYS: [],
            CONF_METRIC_INTERVAL: True,
            CONF_METRIC_KEYS: "custom.rate",
            CONF_METRIC_TAG_FILTERS: {"custom.rate": ["not", "a", "mapping"]},
        }
    )
    assert options.scan_interval == 30
    assert options.ping_interval == 300
    assert options.ping_task_ids == ()
    assert options.ping_stat_keys == ("latest", "loss")
    assert options.metric_interval == 600
    assert options.metric_keys == ()
    assert options.metric_tag_filters == {}
    assert options.invalid_metric_filter_keys == frozenset({"custom.rate"})
    assert options.metric_configuration_invalid is True

    selector_values = KomariOptions.from_mapping(
        {CONF_SCAN_INTERVAL: 45.0, CONF_PING_INTERVAL: 600.0}
    )
    assert selector_values.scan_interval == 45
    assert selector_values.ping_interval == 600


def test_metric_options_reject_invalid_filters_without_broadening_queries() -> None:
    """Malformed Metric filters disable that key instead of becoming unfiltered."""
    options = KomariOptions.from_mapping(
        {
            CONF_METRIC_KEYS: ["custom.rate", "custom.count"],
            CONF_METRIC_TAG_FILTERS: {
                "custom.rate": ["not", "a", "mapping"],
                "custom.count": {"region": "edge"},
            },
        }
    )

    assert options.metric_keys == ("custom.rate", "custom.count")
    assert options.metric_tag_filters == {"custom.count": {"region": "edge"}}
    assert options.invalid_metric_filter_keys == frozenset({"custom.rate"})
    assert options.metric_configuration_invalid is True


def test_metric_options_bound_key_and_filter_sizes() -> None:
    """Manual data cannot bypass Metric key/tag/serialized-size limits."""
    metric_keys = [f"custom.{index}" for index in range(9)]
    oversized_tags = {f"tag{index}": "x" * 256 for index in range(16)}
    options = KomariOptions.from_mapping(
        {
            CONF_METRIC_KEYS: metric_keys,
            CONF_METRIC_TAG_FILTERS: {
                "custom.0": oversized_tags,
                "custom.1": {"x" * 65: "edge"},
            },
        }
    )

    assert options.metric_keys == tuple(metric_keys[:8])
    assert options.metric_tag_filters == {}
    assert options.invalid_metric_filter_keys == frozenset({"custom.0", "custom.1"})
    assert options.metric_configuration_invalid is True


def test_metric_options_mark_each_malformed_filter_without_widening_scope() -> None:
    """All malformed saved filter shapes disable only their affected query key."""
    options = KomariOptions.from_mapping(
        {
            CONF_METRIC_KEYS: ["custom.one", "custom.two", "custom.three"],
            CONF_METRIC_TAG_FILTERS: {
                1: {},
                "custom.one": {f"tag{index}": "value" for index in range(17)},
                "custom.two": {"region": object()},
                "custom.three": {"region": "edge"},
            },
        }
    )

    assert options.metric_tag_filters == {}
    assert options.invalid_metric_filter_keys == frozenset(
        {"custom.one", "custom.two", "custom.three"}
    )
    assert options.metric_configuration_invalid is True


async def test_migrate_p1_entry_preserves_data_options_and_identity(
    hass: HomeAssistant,
) -> None:
    """Minor migration changes only the schema version."""
    data = {
        CONF_URL: "https://example.test",
        CONF_VERIFY_SSL: False,
        CONF_API_KEY: "fixture-key",
    }
    options = {CONF_SCAN_INTERVAL: 60}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options=options,
        version=1,
        minor_version=0,
    )
    entry.add_to_hass(hass)
    entry_id = entry.entry_id

    assert await async_migrate_entry(hass, cast("KomariConfigEntry", entry))
    assert entry.version == 1
    assert entry.minor_version == 1
    assert entry.entry_id == entry_id
    assert entry.data == data
    assert entry.options == options
    assert await async_migrate_entry(hass, cast("KomariConfigEntry", entry))


async def test_migration_rejects_unknown_major_and_reports_update_failure(
    hass: HomeAssistant,
) -> None:
    """Unsupported or failed migrations return false without changing data."""
    future = MockConfigEntry(domain=DOMAIN, data={}, version=2, minor_version=0)
    assert not await async_migrate_entry(hass, cast("KomariConfigEntry", future))

    old = MockConfigEntry(domain=DOMAIN, data={}, version=1, minor_version=0)
    with patch.object(
        hass.config_entries,
        "async_update_entry",
        side_effect=RuntimeError("sanitized migration failure"),
    ):
        assert not await async_migrate_entry(hass, cast("KomariConfigEntry", old))
    assert old.minor_version == 0
