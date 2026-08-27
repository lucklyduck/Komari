"""Validated read-time options for one Komari config entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .const import (
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_PING_INTERVAL,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_EXPIRY_ENTITIES,
    DEFAULT_ENABLE_TRAFFIC_ENTITIES,
    DEFAULT_PING_INTERVAL,
    DEFAULT_PING_STAT_KEYS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    MAX_PING_INTERVAL,
    MAX_PING_TASKS,
    MAX_SCAN_INTERVAL,
    MIN_PING_INTERVAL,
    MIN_SCAN_INTERVAL,
    PING_STAT_KEYS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

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

    @classmethod
    def from_mapping(cls, options: Mapping[str, object]) -> KomariOptions:
        """Build options with safe defaults and deterministic ordering."""
        task_ids = _string_tuple(options.get(CONF_PING_TASK_IDS))[:MAX_PING_TASKS]
        requested_stats = frozenset(_string_tuple(options.get(CONF_PING_STAT_KEYS)))
        stat_keys = tuple(key for key in PING_STAT_KEYS if key in requested_stats)
        if not stat_keys:
            stat_keys = DEFAULT_PING_STAT_KEYS

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
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, minimum), maximum)


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
