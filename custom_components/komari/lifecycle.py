"""Entry-scoped registry lifecycle helpers for Komari."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .device import remove_node_device

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from homeassistant.helpers import device_registry as dr


def node_uuid_from_identifiers(
    entry_id: str,
    identifiers: Iterable[tuple[str, str]],
) -> str | None:
    """Extract one node UUID only from this entry's scoped identifier."""
    prefix = f"{entry_id}:node:"
    for domain, identifier in identifiers:
        if domain == DOMAIN and identifier.startswith(prefix):
            node_uuid = identifier.removeprefix(prefix)
            return node_uuid or None
    return None


def registry_node_ids(
    registry: dr.DeviceRegistry,
    entry_id: str,
) -> frozenset[str]:
    """Return node IDs already registered for exactly one config entry."""
    return frozenset(
        node_uuid
        for device in registry.devices.values()
        if device.config_entry_id == entry_id
        and (node_uuid := node_uuid_from_identifiers(entry_id, device.identifiers))
        is not None
    )


def ping_identity_from_unique_id(
    entry_id: str,
    unique_id: str,
) -> tuple[str, str, str] | None:
    """Parse a node/task/stat identity from one entry-scoped Ping unique ID."""
    prefix = f"{entry_id}:node:"
    if not unique_id.startswith(prefix):
        return None
    node_uuid, separator, task_and_stat = unique_id.removeprefix(prefix).partition(
        ":ping:"
    )
    task_id, statistic_separator, stat_key = task_and_stat.rpartition(":")
    if not separator or not statistic_separator or not node_uuid or not task_id:
        return None
    if not stat_key:
        return None
    return node_uuid, task_id, stat_key


def registry_ping_task_ids(
    registry: er.EntityRegistry,
    entry_id: str,
) -> frozenset[str]:
    """Return task IDs represented by existing Ping registry entities."""
    return frozenset(
        identity[1]
        for item in er.async_entries_for_config_entry(registry, entry_id)
        if (identity := ping_identity_from_unique_id(entry_id, item.unique_id))
        is not None
    )


def remove_node_registry_entries(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry_id: str,
    node_uuid: str,
) -> bool:
    """Remove all entities and the device for one confirmed entry-scoped node."""
    prefix = f"{entry_id}:node:{node_uuid}:"
    removed = False
    for item in tuple(er.async_entries_for_config_entry(entity_registry, entry_id)):
        if item.unique_id.startswith(prefix):
            entity_registry.async_remove(item.entity_id)
            removed = True
    return remove_node_device(device_registry, entry_id, node_uuid) or removed


def remove_disallowed_ping_entities(
    registry: er.EntityRegistry,
    entry_id: str,
    *,
    allowed_task_ids: Collection[str],
    allowed_stat_keys: Collection[str],
    confirmed_removed_task_ids: Collection[str] = (),
) -> int:
    """Remove deselected or authoritatively deleted Ping entities for one entry."""
    allowed_tasks = frozenset(allowed_task_ids)
    allowed_stats = frozenset(allowed_stat_keys)
    removed_tasks = frozenset(confirmed_removed_task_ids)
    removed = 0
    for item in tuple(er.async_entries_for_config_entry(registry, entry_id)):
        identity = ping_identity_from_unique_id(entry_id, item.unique_id)
        if identity is None:
            continue
        _, task_id, stat_key = identity
        if (
            task_id in allowed_tasks
            and task_id not in removed_tasks
            and stat_key in allowed_stats
        ):
            continue
        registry.async_remove(item.entity_id)
        removed += 1
    return removed
