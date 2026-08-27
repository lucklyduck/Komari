"""Privacy-preserving diagnostics for Komari."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from .const import (
    CONF_API_KEY,
    DOMAIN,
    METHOD_COMMON_NODES,
    METHOD_COMMON_RECORDS,
    METHOD_LATEST_STATUS,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_RECORDS,
    METHOD_PUBLIC_PING_TASKS,
)
from .device import panel_identifier
from .lifecycle import node_uuid_from_identifiers, ping_identity_from_unique_id
from .options import get_options

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

    from .coordinator import KomariBranchState
    from .data import KomariConfigEntry
    from .models import KomariNode, KomariNodeStatus

TO_REDACT = {
    CONF_API_KEY,
    "url",
    "authorization",
    "Authorization",
    "cookie",
    "Cookie",
    "token",
    "access_token",
    "uuid",
    "node_uuid",
    "ip",
    "address",
    "host",
    "hostname",
    "name",
    "note",
    "notes",
    "target",
    "clients",
    "raw",
    "raw_response",
    "response",
}

EXPIRY_ENTITY_KEYS = frozenset(
    {"expires_at", "remaining_time", "expired", "auto_renewal", "billing_cycle"}
)
TRAFFIC_ENTITY_KEYS = frozenset(
    {
        "traffic_limit",
        "traffic_limit_type",
        "traffic_used_by_limit_rule",
        "traffic_limit_usage",
    }
)
NODE_METADATA_FIELDS = (
    "cpu_name",
    "virtualization",
    "arch",
    "cpu_cores",
    "cpu_physical_cores",
    "os",
    "kernel_version",
    "gpu_name",
    "region",
    "memory_total",
    "swap_total",
    "disk_total",
    "agent_version",
    "expired_at",
    "traffic_limit",
    "traffic_limit_type",
    "billing_cycle",
    "auto_renewal",
)
NODE_STATUS_FIELDS = (
    "observed_at",
    "cpu_usage",
    "gpu_usage",
    "memory_used",
    "memory_total",
    "swap_used",
    "swap_total",
    "disk_used",
    "disk_total",
    "load_1m",
    "load_5m",
    "load_15m",
    "temperature",
    "network_in_rate",
    "network_out_rate",
    "network_total_up",
    "network_total_down",
    "process_count",
    "tcp_connections",
    "udp_connections",
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: KomariConfigEntry,
) -> dict[str, Any]:
    """Return panel diagnostics without connection or monitored-host identity."""
    coordinator = config_entry.runtime_data.coordinator
    snapshot = coordinator.data
    options = get_options(config_entry)
    integration = await async_get_integration(hass, DOMAIN)
    counts = _entity_counts(er.async_get(hass), config_entry.entry_id)
    online = sum(
        status.online
        for node_uuid in snapshot.nodes
        if (status := snapshot.statuses.get(node_uuid)) is not None
    )
    offline = sum(
        not status.online
        for node_uuid in snapshot.nodes
        if (status := snapshot.statuses.get(node_uuid)) is not None
    )
    diagnostics = {
        "versions": {
            "integration": (
                str(integration.version) if integration.version is not None else None
            ),
            "komari": snapshot.panel.version,
            "rpc": snapshot.panel.rpc_version,
        },
        "capabilities": _capability_diagnostics(snapshot.capabilities),
        "nodes": {
            "visible": len(snapshot.nodes),
            "online": online,
            "offline": offline,
            "status_missing": len(snapshot.nodes) - online - offline,
        },
        "options": {
            "scan_interval": options.scan_interval,
            "ping_interval": options.ping_interval,
            "expiry_entities_enabled": options.enable_expiry_entities,
            "traffic_entities_enabled": options.enable_traffic_entities,
            "selected_ping_task_count": len(options.ping_task_ids),
            "selected_ping_stat_count": len(options.ping_stat_keys),
        },
        "branches": {
            branch: _branch_diagnostics(state)
            for branch, state in coordinator.branch_states.items()
        },
        "entities": counts,
        "traffic_calculation_source": "agent_counters",
        "last_update_success": coordinator.last_update_success,
    }
    return redact_diagnostics(diagnostics)


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    config_entry: KomariConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return panel or node field-presence diagnostics without real values."""
    if panel_identifier(config_entry.entry_id) in device.identifiers:
        return redact_diagnostics(
            {
                "device_type": "panel",
                "last_update_success": (
                    config_entry.runtime_data.coordinator.last_update_success
                ),
            }
        )
    node_uuid = node_uuid_from_identifiers(
        config_entry.entry_id,
        device.identifiers,
    )
    if node_uuid is None:
        return {"device_type": "unknown"}

    snapshot = config_entry.runtime_data.coordinator.data
    node = snapshot.nodes.get(node_uuid)
    status = snapshot.statuses.get(node_uuid)
    diagnostics = {
        "device_type": "node",
        "node_identifier": _node_hash(config_entry.entry_id, node_uuid),
        "inventory_present": node is not None,
        "status_present": status is not None,
        "inventory_field_presence": _field_presence(node, NODE_METADATA_FIELDS),
        "status_field_presence": _field_presence(status, NODE_STATUS_FIELDS),
        "entity_count": _node_entity_count(
            er.async_get(hass),
            config_entry.entry_id,
            node_uuid,
        ),
    }
    return redact_diagnostics(diagnostics)


def redact_diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    """Apply recursive defense-in-depth redaction to an already-safe payload."""
    return async_redact_data(data, TO_REDACT)


def _capability_diagnostics(capabilities: frozenset[str]) -> dict[str, bool]:
    """Map RPC methods to stable feature booleans without returning the method list."""
    return {
        "node_inventory": bool(
            capabilities & {METHOD_PUBLIC_NODES, METHOD_COMMON_NODES}
        ),
        "latest_status": METHOD_LATEST_STATUS in capabilities,
        "ping_tasks": METHOD_PUBLIC_PING_TASKS in capabilities,
        "ping_metric_stats": METHOD_PING_METRIC_STATS in capabilities,
        "ping_public_records": METHOD_PUBLIC_PING_RECORDS in capabilities,
        "ping_common_records": METHOD_COMMON_RECORDS in capabilities,
    }


def _branch_diagnostics(state: KomariBranchState) -> dict[str, str | int | None]:
    """Return only sanitized branch transition state."""
    return {
        "last_success": _isoformat(state.fetched_at),
        "consecutive_failures": state.consecutive_failures,
        "error_category": state.error_category,
    }


def _isoformat(value: datetime | None) -> str | None:
    """Return a JSON-safe timestamp."""
    return value.isoformat() if value is not None else None


def _entity_counts(
    registry: er.EntityRegistry,
    entry_id: str,
) -> dict[str, int]:
    """Count optional entity families without returning any entity identity."""
    expiry = traffic = ping = 0
    for item in er.async_entries_for_config_entry(registry, entry_id):
        if ping_identity_from_unique_id(entry_id, item.unique_id) is not None:
            ping += 1
            continue
        key = item.unique_id.rsplit(":", 1)[-1]
        expiry += key in EXPIRY_ENTITY_KEYS
        traffic += key in TRAFFIC_ENTITY_KEYS
    return {"expiry": expiry, "traffic": traffic, "ping": ping}


def _node_entity_count(
    registry: er.EntityRegistry,
    entry_id: str,
    node_uuid: str,
) -> int:
    """Count entities for one node without exposing their identity."""
    prefix = f"{entry_id}:node:{node_uuid}:"
    return sum(
        item.unique_id.startswith(prefix)
        for item in er.async_entries_for_config_entry(registry, entry_id)
    )


def _field_presence(
    value: KomariNode | KomariNodeStatus | None,
    fields: tuple[str, ...],
) -> dict[str, bool]:
    """Return only whether normalized fields exist, never their values."""
    return {
        field: value is not None and getattr(value, field) is not None
        for field in fields
    }


def _node_hash(entry_id: str, node_uuid: str) -> str:
    """Return a short entry-scoped one-way node identifier."""
    return sha256(f"{entry_id}\0{node_uuid}".encode()).hexdigest()[:16]
