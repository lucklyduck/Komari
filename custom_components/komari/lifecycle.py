"""Entry-scoped registry lifecycle helpers for Komari."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import unquote

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PING_STAT_KEYS
from .device import remove_node_device

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from homeassistant.helpers import device_registry as dr

    from .ping import PingEntityKey


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


def registry_ping_entity_keys(
    registry: er.EntityRegistry,
    entry_id: str,
) -> frozenset[PingEntityKey]:
    """Read only this entry's Komari Ping sensor identities, including disabled."""
    return frozenset(
        identity
        for item in er.async_entries_for_config_entry(registry, entry_id)
        if item.domain == "sensor"
        and item.platform == DOMAIN
        and (identity := ping_identity_from_unique_id(entry_id, item.unique_id))
        is not None
        and identity[2] in PING_STAT_KEYS
    )


def metric_identity_from_unique_id(
    entry_id: str,
    unique_id: str,
) -> tuple[str, str] | None:
    """Parse a node and metric key from one entry-scoped Metric unique ID.

    Dynamic Metric identities deliberately keep the tag digest opaque here.  The
    registry lifecycle only needs the metric key to remove a user-deselected or
    authoritatively removed definition; tags remain part of the entity identity
    and are never surfaced by this helper.
    """
    prefix = f"{entry_id}:node:"
    if not unique_id.startswith(prefix):
        return None
    node_uuid, separator, metric_and_digest = unique_id.removeprefix(prefix).partition(
        ":metric:"
    )
    metric_key, digest_separator, digest = metric_and_digest.rpartition(":")
    if (
        not separator
        or not digest_separator
        or not node_uuid
        or not metric_key
        or not digest
    ):
        return None
    decoded_metric_key = unquote(metric_key)
    return (node_uuid, decoded_metric_key) if decoded_metric_key else None


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


def remove_disallowed_metric_entities(
    registry: er.EntityRegistry,
    entry_id: str,
    *,
    allowed_metric_keys: Collection[str],
    confirmed_removed_metric_keys: Collection[str] = (),
) -> int:
    """Remove deselected or authoritatively deleted Metric entities.

    A temporarily absent Series must not be removed: this helper makes a
    decision from persisted selection or the coordinator's three-successful-
    definition-misses signal only.
    """
    allowed_metrics = frozenset(allowed_metric_keys)
    removed_metrics = frozenset(confirmed_removed_metric_keys)
    removed = 0
    for item in tuple(er.async_entries_for_config_entry(registry, entry_id)):
        identity = metric_identity_from_unique_id(entry_id, item.unique_id)
        if identity is None:
            continue
        _, metric_key = identity
        if metric_key in allowed_metrics and metric_key not in removed_metrics:
            continue
        registry.async_remove(item.entity_id)
        removed += 1
    return removed
