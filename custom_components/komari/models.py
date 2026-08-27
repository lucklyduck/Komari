"""Normalized models for the Komari integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any

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
    clients: frozenset[str]


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
        clients = raw_task.get("clients")
        normalized_clients = (
            frozenset(
                client.strip()
                for client in clients
                if isinstance(client, str) and client.strip()
            )
            if isinstance(clients, list)
            else frozenset()
        )
        tasks[task_id] = KomariPingTask(
            task_id=task_id,
            name=_optional_str(raw_task.get("name")) or f"Ping {task_id}",
            kind=_optional_str(raw_task.get("type")),
            interval=_optional_non_negative_int(raw_task.get("interval")),
            default_on=_optional_bool(raw_task.get("default_on")),
            clients=normalized_clients,
        )
    return MappingProxyType(tasks)


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
        tcp_connections=_optional_non_negative_int(raw.get("connections")),
        udp_connections=_optional_non_negative_int(raw.get("connections_udp")),
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
