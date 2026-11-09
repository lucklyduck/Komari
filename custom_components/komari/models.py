"""Normalized models for the Komari integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil, isfinite
from statistics import fmean, pstdev
from types import MappingProxyType
from typing import Any

from .const import (
    MAX_METRIC_DEFINITIONS,
    MAX_METRIC_POINTS_PER_SERIES,
    MAX_METRIC_SERIES,
    MAX_METRIC_TAG_KEY_LENGTH,
    MAX_METRIC_TAG_VALUE_LENGTH,
    MAX_METRIC_TAGS,
)
from .exceptions import KomariProtocolError

_EMPTY_GPU_NAMES = frozenset(
    {
        "-",
        "n/a",
        "n-a",
        "na",
        "none",
        "null",
        "unknown",
    }
)


class TrafficLimitType(StrEnum):
    """Supported Komari traffic-limit calculation modes."""

    SUM = "sum"
    MAX = "max"
    MIN = "min"
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class KomariPanelInfo:
    """Normalized Komari panel metadata."""

    name: str
    version: str | None
    build_hash: str | None
    rpc_version: str | None
    private: bool | None


@dataclass(frozen=True, slots=True)
class KomariNode:
    """Normalized Komari node metadata."""

    uuid: str
    name: str
    cpu_name: str | None
    virtualization: str | None
    arch: str | None
    cpu_cores: int | None
    cpu_physical_cores: int | None
    os: str | None
    kernel_version: str | None
    gpu_name: str | None
    region: str | None
    memory_total: int | None
    swap_total: int | None
    disk_total: int | None
    agent_version: str | None
    expired_at: datetime | None
    traffic_limit: int | None
    traffic_limit_type: TrafficLimitType | None
    billing_cycle: int | None
    auto_renewal: bool | None


@dataclass(frozen=True, slots=True)
class KomariNodeStatus:
    """Normalized latest status for a Komari node."""

    node_uuid: str
    online: bool
    observed_at: datetime | None
    cpu_usage: float | None
    gpu_usage: float | None
    memory_used: int | None
    memory_total: int | None
    swap_used: int | None
    swap_total: int | None
    disk_used: int | None
    disk_total: int | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    temperature: float | None
    network_in_rate: float | None
    network_out_rate: float | None
    network_total_up: int | None
    network_total_down: int | None
    process_count: int | None
    tcp_connections: int | None
    udp_connections: int | None


@dataclass(frozen=True, slots=True)
class KomariPingTask:
    """Normalized public Ping task metadata."""

    task_id: str
    name: str
    kind: str | None
    interval: int | None
    default_on: bool | None
    clients: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class KomariPingSeriesKey:
    """Stable identity of one node/task Ping series."""

    node_uuid: str
    task_id: str


@dataclass(frozen=True, slots=True)
class KomariPingStats:
    """Normalized Ping statistics for one node/task pair."""

    latest: float | None
    loss: float | None
    minimum: float | None
    maximum: float | None
    average: float | None
    p50: float | None
    p99: float | None
    stddev: float | None
    total: int | None
    valid: int | None


@dataclass(frozen=True, slots=True)
class KomariMetricDefinition:
    """Normalized server-declared Metric Definition."""

    metric_key: str
    description: str | Mapping[str, str] | None
    metric_type: str
    unit: str | None
    retention_days: int
    metadata: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class KomariMetricSeriesKey:
    """Stable identity of one node-scoped Metric series."""

    metric_key: str
    entity_id: str
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class KomariMetricValue:
    """Newest valid numeric value of one Metric series."""

    value: float | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class KomariMetricValues(Mapping[KomariMetricSeriesKey, KomariMetricValue]):
    """Immutable Metric values plus sanitized parser skip counters."""

    data: Mapping[KomariMetricSeriesKey, KomariMetricValue]
    skipped_series_count: int = 0
    skipped_point_count: int = 0

    def __getitem__(self, key: KomariMetricSeriesKey) -> KomariMetricValue:
        """Return one normalized series value."""
        return self.data[key]

    def __iter__(self):
        """Iterate normalized series identities."""
        return iter(self.data)

    def __len__(self) -> int:
        """Return the number of retained Metric series."""
        return len(self.data)


@dataclass(frozen=True, slots=True)
class KomariBootstrapData:
    """Data required to create and initially load a config entry."""

    panel: KomariPanelInfo
    nodes: Mapping[str, KomariNode]
    statuses: Mapping[str, KomariNodeStatus]
    capabilities: frozenset[str]


def parse_panel_info(
    settings: object,
    version_info: object,
    rpc_version: str | None,
) -> KomariPanelInfo:
    """Normalize public settings and server-version responses."""
    settings_map = _mapping_or_empty(settings)
    version_map = _mapping_or_empty(version_info)
    return KomariPanelInfo(
        name=_optional_str(settings_map.get("sitename")) or "",
        version=_optional_str(version_map.get("version")),
        build_hash=_optional_str(version_map.get("hash")),
        rpc_version=rpc_version,
        private=_optional_bool(settings_map.get("private_site")),
    )


def parse_nodes(payload: object) -> Mapping[str, KomariNode]:
    """Normalize a node mapping, array, or single node response."""
    entries: Iterable[tuple[str | None, object]]
    if isinstance(payload, Mapping):
        if "uuid" in payload:
            entries = ((None, payload),)
        else:
            entries = ((str(key), value) for key, value in payload.items())
    elif isinstance(payload, list):
        entries = ((None, value) for value in payload)
    else:
        raise KomariProtocolError("Node response must be an object or array")

    nodes: dict[str, KomariNode] = {}
    for fallback_uuid, raw_node in entries:
        if not isinstance(raw_node, Mapping):
            raise KomariProtocolError("Each node response must be an object")
        node_uuid = _optional_str(raw_node.get("uuid")) or fallback_uuid
        if not node_uuid:
            raise KomariProtocolError("Node response is missing uuid")
        nodes[node_uuid] = _parse_node(node_uuid, raw_node)
    return MappingProxyType(nodes)


def parse_statuses(payload: object) -> Mapping[str, KomariNodeStatus]:
    """Normalize latest node status responses."""
    entries: Iterable[tuple[str | None, object]]
    if isinstance(payload, Mapping):
        if "client" in payload:
            entries = ((None, payload),)
        else:
            entries = ((str(key), value) for key, value in payload.items())
    elif isinstance(payload, list):
        entries = ((None, value) for value in payload)
    else:
        raise KomariProtocolError("Status response must be an object or array")

    statuses: dict[str, KomariNodeStatus] = {}
    for fallback_uuid, raw_status in entries:
        if not isinstance(raw_status, Mapping):
            raise KomariProtocolError("Each status response must be an object")
        node_uuid = _optional_str(raw_status.get("client")) or fallback_uuid
        if not node_uuid:
            raise KomariProtocolError("Status response is missing client uuid")
        online = raw_status.get("online")
        if not isinstance(online, bool):
            raise KomariProtocolError("Status response is missing online state")
        statuses[node_uuid] = _parse_status(node_uuid, online, raw_status)
    return MappingProxyType(statuses)


def parse_ping_tasks(payload: object) -> Mapping[str, KomariPingTask]:
    """Normalize the public Ping task list without exposing target clients."""
    if not isinstance(payload, list):
        raise KomariProtocolError("Ping task response must be an array")

    tasks: dict[str, KomariPingTask] = {}
    for raw_task in payload:
        if not isinstance(raw_task, Mapping):
            raise KomariProtocolError("Each Ping task must be an object")
        task_id = _identifier(raw_task.get("id"))
        if task_id is None:
            raise KomariProtocolError("Ping task is missing id")
        normalized_clients = _ping_clients(raw_task)
        tasks[task_id] = KomariPingTask(
            task_id=task_id,
            name=_optional_str(raw_task.get("name")) or f"Ping {task_id}",
            kind=_optional_str(raw_task.get("type")),
            interval=_optional_non_negative_int(raw_task.get("interval")),
            default_on=_optional_bool(raw_task.get("default_on")),
            clients=normalized_clients,
        )
    return MappingProxyType(tasks)


def _ping_clients(raw: Mapping[str, Any]) -> frozenset[str] | None:
    """Distinguish an authoritative empty slice from unknown assignments.

    Public tasks project the Go Clients slice directly: both [] and null mean
    no assigned clients. Missing or partly malformed lists cannot prove that.
    """
    if "clients" not in raw:
        return None
    clients = raw["clients"]
    if clients is None:
        return frozenset()
    if not isinstance(clients, list) or any(
        not isinstance(client, str) or not client.strip() for client in clients
    ):
        return None
    return frozenset(client.strip() for client in clients)


def parse_metric_definitions(
    payload: object,
    *,
    max_definitions: int = MAX_METRIC_DEFINITIONS,
) -> Mapping[str, KomariMetricDefinition]:
    """Normalize a bounded Metric Definition list.

    The RPC contract returns an array. A malformed top-level response, an
    over-limit list, or an ambiguous duplicate is a protocol failure rather
    than a partially published discovery result.
    """
    if not isinstance(payload, list):
        raise KomariProtocolError("Metric definitions response must be an array")
    if len(payload) > max_definitions:
        raise KomariProtocolError("Metric definitions response exceeds safe limit")

    definitions: dict[str, KomariMetricDefinition] = {}
    for raw_definition in payload:
        if not isinstance(raw_definition, Mapping):
            raise KomariProtocolError("Each Metric definition must be an object")
        metric_key = _optional_str(raw_definition.get("name"))
        metric_type = _optional_str(raw_definition.get("type"))
        if metric_key is None or metric_type is None:
            raise KomariProtocolError("Metric definition is missing name or type")
        if metric_key in definitions:
            raise KomariProtocolError("Metric definitions contain a duplicate name")

        raw_retention = raw_definition.get("retention_days")
        retention_days = _optional_non_negative_int(raw_retention)
        if raw_retention is not None and retention_days is None:
            raise KomariProtocolError("Metric definition has invalid retention_days")
        definitions[metric_key] = KomariMetricDefinition(
            metric_key=metric_key,
            description=_parse_metric_description(raw_definition.get("description")),
            metric_type=metric_type,
            unit=_optional_str(raw_definition.get("unit")),
            retention_days=retention_days or 0,
            metadata=_parse_metric_metadata(raw_definition.get("metadata")),
        )
    return MappingProxyType(definitions)


def parse_metric_values(
    payload: object,
    *,
    max_series: int = MAX_METRIC_SERIES,
    max_points_per_series: int = MAX_METRIC_POINTS_PER_SERIES,
) -> KomariMetricValues:
    """Normalize bounded Metric query results without retaining raw points.

    Only a valid time-zone-aware timestamp and a finite numeric value produce
    a current value. ``null`` is preserved as an unavailable series value so
    callers can leave an existing entity unavailable without inventing a
    numeric state. ``labels`` is deliberately ignored: only ``tags`` belongs
    to the wire-level series identity.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("series"), list):
        raise KomariProtocolError("Metric query response must contain a series array")
    raw_series = payload["series"]
    if len(raw_series) > max_series:
        raise KomariProtocolError("Metric query response exceeds safe series limit")

    values: dict[KomariMetricSeriesKey, KomariMetricValue] = {}
    skipped_series_count = 0
    skipped_point_count = 0
    for raw in raw_series:
        parsed = _parse_metric_series(
            raw,
            max_points_per_series=max_points_per_series,
        )
        if parsed is None:
            skipped_series_count += 1
            continue
        key, candidate, skipped_points = parsed
        skipped_point_count += skipped_points
        existing = values.get(key)
        if existing is None or _prefer_metric_value(candidate, existing):
            values[key] = candidate
    return KomariMetricValues(
        data=MappingProxyType(values),
        skipped_series_count=skipped_series_count,
        skipped_point_count=skipped_point_count,
    )


def _parse_metric_description(value: object) -> str | Mapping[str, str] | None:
    """Normalize a localizable Metric description without coercing values."""
    if isinstance(value, str):
        return _optional_str(value)
    if not isinstance(value, Mapping):
        return None
    translations = {
        key.strip(): text.strip()
        for key, text in value.items()
        if isinstance(key, str)
        and key.strip()
        and isinstance(text, str)
        and text.strip()
    }
    return MappingProxyType(translations) if translations else None


def _parse_metric_metadata(value: object) -> Mapping[str, str]:
    """Keep only explicit string metadata in an immutable mapping."""
    if not isinstance(value, Mapping):
        return MappingProxyType({})
    return MappingProxyType(
        {
            key.strip(): item
            for key, item in value.items()
            if isinstance(key, str) and key.strip() and isinstance(item, str)
        }
    )


def _parse_metric_series(
    raw: object,
    *,
    max_points_per_series: int,
) -> tuple[KomariMetricSeriesKey, KomariMetricValue, int] | None:
    """Parse one query series or ignore a malformed non-authoritative entry."""
    if not isinstance(raw, Mapping):
        return None
    metric_key = _optional_str(raw.get("metric_key"))
    entity_id = _optional_str(raw.get("entity_id"))
    tags = _canonical_metric_tags(raw.get("tags"))
    points = raw.get("points")
    if (
        metric_key is None
        or entity_id is None
        or tags is None
        or not isinstance(points, list)
    ):
        return None
    if len(points) > max_points_per_series:
        raise KomariProtocolError("Metric series exceeds safe point limit")

    latest_point_at: datetime | None = None
    latest_value: tuple[datetime, float] | None = None
    skipped_point_count = 0
    for raw_point in points:
        if not isinstance(raw_point, Mapping):
            skipped_point_count += 1
            continue
        observed_at = _parse_metric_point_datetime(raw_point)
        if observed_at is None:
            skipped_point_count += 1
            continue
        if latest_point_at is None or observed_at > latest_point_at:
            latest_point_at = observed_at
        raw_value = raw_point.get("value")
        if raw_value is None:
            continue
        value = _optional_finite_float(raw_value)
        if value is None:
            skipped_point_count += 1
            continue
        if latest_value is None or observed_at > latest_value[0]:
            latest_value = (observed_at, value)

    if latest_value is not None:
        value, observed_at = latest_value[1], latest_value[0]
    elif latest_point_at is not None:
        value, observed_at = None, latest_point_at
    else:
        return None
    return (
        KomariMetricSeriesKey(
            metric_key=metric_key,
            entity_id=entity_id,
            tags=tags,
        ),
        KomariMetricValue(value=value, observed_at=observed_at),
        skipped_point_count,
    )


def _canonical_metric_tags(value: object) -> tuple[tuple[str, str], ...] | None:
    """Return bounded sorted tag identity without delimiter-based collisions."""
    if value is None:
        return ()
    if not isinstance(value, Mapping) or len(value) > MAX_METRIC_TAGS:
        return None
    tags: list[tuple[str, str]] = []
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > MAX_METRIC_TAG_KEY_LENGTH
            or not isinstance(item, str)
            or len(item) > MAX_METRIC_TAG_VALUE_LENGTH
        ):
            return None
        tags.append((key, item))
    return tuple(sorted(tags))


def _parse_metric_point_datetime(value: Mapping[object, object]) -> datetime | None:
    """Accept documented and transitional point timestamp field names."""
    for field in ("timestamp", "time", "ts"):
        if (parsed := parse_datetime(value.get(field))) is not None:
            return parsed
    return None


def _prefer_metric_value(
    candidate: KomariMetricValue,
    existing: KomariMetricValue,
) -> bool:
    """Choose a finite value over null, then the newest timestamp."""
    if candidate.value is not None and existing.value is None:
        return True
    if candidate.value is None and existing.value is not None:
        return False
    if candidate.observed_at is None:
        return False
    if existing.observed_at is None:
        return True
    return candidate.observed_at > existing.observed_at


def parse_ping_metric_stats(
    payload: object,
) -> Mapping[KomariPingSeriesKey, KomariPingStats]:
    """Normalize the current public Ping statistics response."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("stats"), list):
        raise KomariProtocolError("Ping statistics response must contain an array")

    result: dict[KomariPingSeriesKey, KomariPingStats] = {}
    for raw_stats in payload["stats"]:
        if not isinstance(raw_stats, Mapping):
            raise KomariProtocolError("Each Ping statistic must be an object")
        node_uuid = _optional_str(raw_stats.get("entity_id")) or _optional_str(
            raw_stats.get("uuid")
        )
        task_id = _identifier(raw_stats.get("task_id"))
        if node_uuid is None or task_id is None:
            raise KomariProtocolError("Ping statistic is missing series identity")
        key = KomariPingSeriesKey(node_uuid=node_uuid, task_id=task_id)
        result[key] = KomariPingStats(
            latest=_optional_non_negative_float(raw_stats.get("latest")),
            loss=_optional_non_negative_float(raw_stats.get("loss")),
            minimum=_optional_non_negative_float(raw_stats.get("min")),
            maximum=_optional_non_negative_float(raw_stats.get("max")),
            average=_optional_non_negative_float(raw_stats.get("avg")),
            p50=_optional_non_negative_float(raw_stats.get("p50")),
            p99=_optional_non_negative_float(raw_stats.get("p99")),
            stddev=_optional_non_negative_float(raw_stats.get("stddev")),
            total=_optional_non_negative_int(raw_stats.get("total")),
            valid=_optional_non_negative_int(raw_stats.get("valid")),
        )
    return MappingProxyType(result)


def parse_ping_records_stats(
    payload: object,
    *,
    node_uuids: tuple[str, ...],
    task_ids: tuple[str, ...],
    max_points: int,
) -> Mapping[KomariPingSeriesKey, KomariPingStats]:
    """Calculate typed Ping statistics from one batched records response."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("records"), list):
        raise KomariProtocolError("Ping records response must contain an array")
    if max_points <= 0:
        raise KomariProtocolError("Ping records max_points must be positive")

    visible_nodes = frozenset(node_uuids)
    selected_tasks = frozenset(task_ids)
    grouped: dict[KomariPingSeriesKey, list[tuple[datetime, float]]] = {}
    for raw_record in payload["records"]:
        if not isinstance(raw_record, Mapping):
            raise KomariProtocolError("Each Ping record must be an object")
        node_uuid = _optional_str(raw_record.get("client")) or _optional_str(
            raw_record.get("entity_id")
        )
        task_id = _identifier(raw_record.get("task_id"))
        observed_at = parse_datetime(raw_record.get("time"))
        value = _optional_finite_float(raw_record.get("value"))
        if node_uuid is None or task_id is None or observed_at is None or value is None:
            raise KomariProtocolError("Ping record is missing required fields")
        if node_uuid not in visible_nodes or task_id not in selected_tasks:
            continue
        key = KomariPingSeriesKey(node_uuid=node_uuid, task_id=task_id)
        grouped.setdefault(key, []).append((observed_at, value))

    result: dict[KomariPingSeriesKey, KomariPingStats] = {}
    for key, records in grouped.items():
        records.sort(key=lambda item: item[0])
        records = records[-max_points:]
        valid_values = [value for _, value in records if value >= 0]
        total = len(records)
        valid = len(valid_values)
        latest = next(
            (value for _, value in reversed(records) if value >= 0),
            None,
        )
        average = fmean(valid_values) if valid_values else None
        result[key] = KomariPingStats(
            latest=latest,
            loss=(total - valid) / total * 100,
            minimum=min(valid_values) if valid_values else None,
            maximum=max(valid_values) if valid_values else None,
            average=average,
            p50=_nearest_rank(valid_values, 0.50),
            p99=_nearest_rank(valid_values, 0.99),
            stddev=pstdev(valid_values) if valid_values else None,
            total=total,
            valid=valid,
        )
    return MappingProxyType(result)


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    """Return a deterministic nearest-rank percentile for valid samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _parse_node(node_uuid: str, raw: Mapping[str, Any]) -> KomariNode:
    """Normalize one Komari node."""
    traffic_limit_type: TrafficLimitType | None = None
    raw_limit_type = _optional_str(raw.get("traffic_limit_type"))
    if raw_limit_type:
        try:
            traffic_limit_type = TrafficLimitType(raw_limit_type)
        except ValueError:
            traffic_limit_type = None

    return KomariNode(
        uuid=node_uuid,
        name=_optional_str(raw.get("name")) or node_uuid,
        cpu_name=_optional_str(raw.get("cpu_name")),
        virtualization=_optional_str(raw.get("virtualization")),
        arch=_optional_str(raw.get("arch")),
        cpu_cores=_optional_non_negative_int(raw.get("cpu_cores")),
        cpu_physical_cores=_optional_non_negative_int(raw.get("cpu_physical_cores")),
        os=_optional_str(raw.get("os")),
        kernel_version=_optional_str(raw.get("kernel_version")),
        gpu_name=_optional_gpu_name(raw.get("gpu_name")),
        region=_optional_str(raw.get("region")),
        memory_total=_optional_non_negative_int(raw.get("mem_total")),
        swap_total=_optional_non_negative_int(raw.get("swap_total")),
        disk_total=_optional_non_negative_int(raw.get("disk_total")),
        agent_version=_optional_str(raw.get("version")),
        expired_at=parse_datetime(raw.get("expired_at")),
        traffic_limit=_optional_non_negative_int(raw.get("traffic_limit")),
        traffic_limit_type=traffic_limit_type,
        billing_cycle=_optional_non_negative_int(raw.get("billing_cycle")),
        auto_renewal=_optional_bool(raw.get("auto_renewal")),
    )


def _parse_status(
    node_uuid: str,
    online: bool,
    raw: Mapping[str, Any],
) -> KomariNodeStatus:
    """Normalize one latest-status record."""
    connections = _optional_non_negative_int(raw.get("connections"))
    udp_connections = _optional_non_negative_int(raw.get("connections_udp"))
    tcp_connections = (
        connections - udp_connections
        if connections is not None
        and udp_connections is not None
        and connections >= udp_connections
        else None
    )
    return KomariNodeStatus(
        node_uuid=node_uuid,
        online=online,
        observed_at=parse_datetime(raw.get("time")),
        cpu_usage=_optional_finite_float(raw.get("cpu")),
        gpu_usage=_optional_finite_float(raw.get("gpu")),
        memory_used=_optional_non_negative_int(raw.get("ram")),
        memory_total=_optional_non_negative_int(raw.get("ram_total")),
        swap_used=_optional_non_negative_int(raw.get("swap")),
        swap_total=_optional_non_negative_int(raw.get("swap_total")),
        disk_used=_optional_non_negative_int(raw.get("disk")),
        disk_total=_optional_non_negative_int(raw.get("disk_total")),
        load_1m=_optional_finite_float(raw.get("load")),
        load_5m=_optional_finite_float(raw.get("load5")),
        load_15m=_optional_finite_float(raw.get("load15")),
        temperature=_optional_finite_float(raw.get("temp")),
        network_in_rate=_optional_non_negative_float(raw.get("net_in")),
        network_out_rate=_optional_non_negative_float(raw.get("net_out")),
        network_total_up=_optional_non_negative_int(raw.get("net_total_up")),
        network_total_down=_optional_non_negative_int(raw.get("net_total_down")),
        process_count=_optional_non_negative_int(raw.get("process")),
        tcp_connections=tcp_connections,
        udp_connections=udp_connections,
    )


def parse_datetime(value: object) -> datetime | None:
    """Parse a Komari UTC timestamp without guessing invalid values."""
    parsed: datetime
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    elif isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = float(value)
        if not isfinite(timestamp):
            return None
        if abs(timestamp) >= 100_000_000_000:
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=UTC)
        except OSError, OverflowError, ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    """Return a mapping or an empty immutable mapping."""
    if isinstance(value, Mapping):
        return value
    return MappingProxyType({})


def _optional_str(value: object) -> str | None:
    """Return a non-empty string."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _optional_gpu_name(value: object) -> str | None:
    """Return a meaningful GPU name, excluding common absence sentinels."""
    name = _optional_str(value)
    if name is None or name.casefold() in _EMPTY_GPU_NAMES:
        return None
    return name


def _identifier(value: object) -> str | None:
    """Normalize a numeric or string remote identifier to a stable string."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_bool(value: object) -> bool | None:
    """Return a bool without coercion."""
    return value if isinstance(value, bool) else None


def _optional_non_negative_int(value: object) -> int | None:
    """Return a non-negative integer without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _optional_finite_float(value: object) -> float | None:
    """Return a finite numeric value without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if isfinite(result) else None


def _optional_non_negative_float(value: object) -> float | None:
    """Return a finite non-negative numeric value."""
    result = _optional_finite_float(value)
    if result is None or result < 0:
        return None
    return result
