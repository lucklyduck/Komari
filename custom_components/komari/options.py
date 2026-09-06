"""Validated read-time options for one Komari config entry."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING

from .const import (
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_METRIC_INTERVAL,
    CONF_METRIC_KEYS,
    CONF_METRIC_TAG_FILTERS,
    CONF_PING_INTERVAL,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_EXPIRY_ENTITIES,
    DEFAULT_ENABLE_TRAFFIC_ENTITIES,
    DEFAULT_METRIC_INTERVAL,
    DEFAULT_PING_INTERVAL,
    DEFAULT_PING_STAT_KEYS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    MAX_METRIC_FILTERS_JSON_BYTES,
    MAX_METRIC_INTERVAL,
    MAX_METRIC_KEYS,
    MAX_METRIC_TAG_KEY_LENGTH,
    MAX_METRIC_TAG_VALUE_LENGTH,
    MAX_METRIC_TAGS,
    MAX_PING_INTERVAL,
    MAX_PING_TASKS,
    MAX_SCAN_INTERVAL,
    MIN_METRIC_INTERVAL,
    MIN_PING_INTERVAL,
    MIN_SCAN_INTERVAL,
    PING_STAT_KEYS,
)

if TYPE_CHECKING:
    from .data import KomariConfigEntry


@dataclass(frozen=True, slots=True)
class KomariOptions:
    """Normalized options consumed by runtime code."""

    scan_interval: int
    ping_interval: int
    enable_expiry_entities: bool
    enable_traffic_entities: bool
    ping_task_ids: tuple[str, ...]
    ping_stat_keys: tuple[str, ...]
    metric_interval: int
    metric_keys: tuple[str, ...]
    metric_tag_filters: Mapping[str, Mapping[str, str]]
    invalid_metric_filter_keys: frozenset[str]
    metric_configuration_invalid: bool

    @classmethod
    def from_mapping(cls, options: Mapping[str, object]) -> KomariOptions:
        """Build options with safe defaults and deterministic ordering."""
        task_ids = _string_tuple(options.get(CONF_PING_TASK_IDS))[:MAX_PING_TASKS]
        requested_stats = frozenset(_string_tuple(options.get(CONF_PING_STAT_KEYS)))
        stat_keys = tuple(key for key in PING_STAT_KEYS if key in requested_stats)
        if not stat_keys:
            stat_keys = DEFAULT_PING_STAT_KEYS
        metric_keys, metric_keys_invalid = _metric_key_tuple(
            options.get(CONF_METRIC_KEYS)
        )
        (
            metric_tag_filters,
            invalid_metric_filter_keys,
            filters_invalid,
        ) = _metric_tag_filters(
            options.get(CONF_METRIC_TAG_FILTERS),
            metric_keys,
        )

        return cls(
            scan_interval=_bounded_int(
                options.get(CONF_SCAN_INTERVAL),
                default=DEFAULT_SCAN_INTERVAL_SECONDS,
                minimum=MIN_SCAN_INTERVAL,
                maximum=MAX_SCAN_INTERVAL,
            ),
            ping_interval=_bounded_int(
                options.get(CONF_PING_INTERVAL),
                default=DEFAULT_PING_INTERVAL,
                minimum=MIN_PING_INTERVAL,
                maximum=MAX_PING_INTERVAL,
            ),
            enable_expiry_entities=_strict_bool(
                options.get(CONF_ENABLE_EXPIRY_ENTITIES),
                DEFAULT_ENABLE_EXPIRY_ENTITIES,
            ),
            enable_traffic_entities=_strict_bool(
                options.get(CONF_ENABLE_TRAFFIC_ENTITIES),
                DEFAULT_ENABLE_TRAFFIC_ENTITIES,
            ),
            ping_task_ids=task_ids,
            ping_stat_keys=stat_keys,
            metric_interval=_bounded_int(
                options.get(CONF_METRIC_INTERVAL),
                default=DEFAULT_METRIC_INTERVAL,
                minimum=MIN_METRIC_INTERVAL,
                maximum=MAX_METRIC_INTERVAL,
            ),
            metric_keys=metric_keys,
            metric_tag_filters=metric_tag_filters,
            invalid_metric_filter_keys=invalid_metric_filter_keys,
            metric_configuration_invalid=(metric_keys_invalid or filters_invalid),
        )

    def as_dict(self) -> dict[str, object]:
        """Return the complete user-selected options payload."""
        return {
            CONF_SCAN_INTERVAL: self.scan_interval,
            CONF_PING_INTERVAL: self.ping_interval,
            CONF_ENABLE_EXPIRY_ENTITIES: self.enable_expiry_entities,
            CONF_ENABLE_TRAFFIC_ENTITIES: self.enable_traffic_entities,
            CONF_PING_TASK_IDS: list(self.ping_task_ids),
            CONF_PING_STAT_KEYS: list(self.ping_stat_keys),
            CONF_METRIC_INTERVAL: self.metric_interval,
            CONF_METRIC_KEYS: list(self.metric_keys),
            CONF_METRIC_TAG_FILTERS: {
                metric_key: dict(tags)
                for metric_key, tags in self.metric_tag_filters.items()
            },
        }


def get_options(entry: KomariConfigEntry) -> KomariOptions:
    """Return normalized options for a config entry."""
    return KomariOptions.from_mapping(entry.options)


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Return an in-range integer without coercing booleans or strings."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    numeric = float(value)
    if not isfinite(numeric) or not numeric.is_integer():
        return default
    return min(max(int(numeric), minimum), maximum)


def _strict_bool(value: object, default: bool) -> bool:
    """Return a boolean option without truthy-string coercion."""
    return value if isinstance(value, bool) else default


def _string_tuple(value: object) -> tuple[str, ...]:
    """Normalize a list-like option to unique non-empty strings."""
    if not isinstance(value, list | tuple):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, str | int):
            continue
        normalized = str(item).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _metric_key_tuple(value: object) -> tuple[tuple[str, ...], bool]:
    """Normalize selected Metric names without coercing arbitrary values."""
    if value is None:
        return (), False
    if not isinstance(value, list | tuple):
        return (), True

    result: list[str] = []
    invalid = False
    for item in value:
        if not isinstance(item, str):
            invalid = True
            continue
        normalized = item.strip()
        if not normalized:
            invalid = True
            continue
        if normalized not in result:
            result.append(normalized)
    if len(result) > MAX_METRIC_KEYS:
        invalid = True
        result = result[:MAX_METRIC_KEYS]
    return tuple(result), invalid


def _metric_tag_filters(
    value: object,
    selected_metric_keys: tuple[str, ...],
) -> tuple[
    Mapping[str, Mapping[str, str]],
    frozenset[str],
    bool,
]:
    """Normalize exact Metric tag filters without broadening invalid filters.

    A malformed filter must never become an empty filter, because that would
    turn an intentionally narrow series query into an unbounded query. The
    invalid Metric key is exposed to the runtime so it can skip that request.
    """
    if value is None:
        return MappingProxyType({}), frozenset(), False
    if not isinstance(value, Mapping):
        return (
            MappingProxyType({}),
            frozenset(selected_metric_keys),
            bool(selected_metric_keys),
        )

    normalized: dict[str, Mapping[str, str]] = {}
    invalid_keys: set[str] = set()
    invalid = False
    for raw_metric_key, raw_tags in value.items():
        if not isinstance(raw_metric_key, str) or not raw_metric_key.strip():
            invalid = True
            continue
        metric_key = raw_metric_key.strip()
        if not isinstance(raw_tags, Mapping):
            invalid_keys.add(metric_key)
            invalid = True
            continue
        if len(raw_tags) > MAX_METRIC_TAGS:
            invalid_keys.add(metric_key)
            invalid = True
            continue
        tags: dict[str, str] = {}
        for raw_key, raw_tag_value in raw_tags.items():
            if (
                not isinstance(raw_key, str)
                or not isinstance(raw_tag_value, str)
                or not raw_key
                or len(raw_key) > MAX_METRIC_TAG_KEY_LENGTH
                or len(raw_tag_value) > MAX_METRIC_TAG_VALUE_LENGTH
            ):
                invalid_keys.add(metric_key)
                invalid = True
                break
            tags[raw_key] = raw_tag_value
        else:
            normalized[metric_key] = MappingProxyType(dict(sorted(tags.items())))

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except TypeError, ValueError:
        encoded = b""
        invalid = True
        invalid_keys.update(selected_metric_keys)
    if len(encoded) > MAX_METRIC_FILTERS_JSON_BYTES:
        invalid = True
        invalid_keys.update(
            metric_key
            for metric_key in normalized
            if metric_key in selected_metric_keys
        )

    for metric_key in invalid_keys:
        normalized.pop(metric_key, None)
    return (
        MappingProxyType(normalized),
        frozenset(invalid_keys),
        invalid,
    )
