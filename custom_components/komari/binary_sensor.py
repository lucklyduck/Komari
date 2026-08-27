"""Binary sensor platform for Komari panels and nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .entity import KomariNodeEntity, KomariPanelEntity
from .options import get_options

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import KomariConfigEntry
    from .models import KomariNode


RPC_AVAILABLE_DESCRIPTION = BinarySensorEntityDescription(
    key="rpc_available",
    translation_key="rpc_available",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)

NODE_PROBLEM_DESCRIPTION = BinarySensorEntityDescription(
    key="node_problem",
    translation_key="node_problem",
    device_class=BinarySensorDeviceClass.PROBLEM,
)

NODE_ONLINE_DESCRIPTION = BinarySensorEntityDescription(
    key="online",
    translation_key="online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)


def _has_expiry(node: KomariNode) -> bool:
    """Return whether expiry-derived binary sensors should exist."""
    return node.expired_at is not None


@dataclass(frozen=True, kw_only=True)
class KomariNodeMetadataBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe a node binary sensor sourced from inventory metadata."""

    value_fn: Callable[[KomariNode], bool | None]
    create_fn: Callable[[KomariNode], bool]


EXPIRY_BINARY_SENSOR_DESCRIPTIONS: tuple[
    KomariNodeMetadataBinarySensorEntityDescription, ...
] = (
    KomariNodeMetadataBinarySensorEntityDescription(
        key="expired",
        translation_key="expired",
        device_class=BinarySensorDeviceClass.PROBLEM,
        create_fn=_has_expiry,
        value_fn=lambda node: (
            dt_util.utcnow() >= node.expired_at if node.expired_at is not None else None
        ),
    ),
    KomariNodeMetadataBinarySensorEntityDescription(
        key="auto_renewal",
        translation_key="auto_renewal",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        create_fn=_has_expiry,
        value_fn=lambda node: node.auto_renewal,
    ),
)

EXPIRY_BINARY_SENSOR_KEYS = frozenset(
    description.key for description in EXPIRY_BINARY_SENSOR_DESCRIPTIONS
)


class KomariRpcAvailableBinarySensor(KomariPanelEntity, BinarySensorEntity):
    """Panel RPC connectivity that remains available during an outage."""

    entity_description = RPC_AVAILABLE_DESCRIPTION

    def __init__(self, entry: KomariConfigEntry) -> None:
        """Initialize panel RPC connectivity."""
        super().__init__(entry, self.entity_description)

    @property
    @override
    def available(self) -> bool:
        """Remain available after the first successful config entry setup."""
        return True

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the most recent panel refresh succeeded."""
        return self.coordinator.last_update_success


class KomariNodeProblemBinarySensor(KomariPanelEntity, BinarySensorEntity):
    """Report whether any known node is explicitly offline."""

    entity_description = NODE_PROBLEM_DESCRIPTION

    def __init__(self, entry: KomariConfigEntry) -> None:
        """Initialize the node problem summary."""
        super().__init__(entry, self.entity_description)

    @property
    @override
    def is_on(self) -> bool:
        """Return true when at least one inventory node is explicitly offline."""
        return any(
            status is not None and not status.online
            for node_uuid in self.coordinator.data.nodes
            if (status := self.coordinator.data.statuses.get(node_uuid)) is not None
        )


class KomariNodeOnlineBinarySensor(KomariNodeEntity, BinarySensorEntity):
    """Connectivity for one node with missing-state protection."""

    entity_description = NODE_ONLINE_DESCRIPTION

    def __init__(self, entry: KomariConfigEntry, node_uuid: str) -> None:
        """Initialize node connectivity."""
        super().__init__(entry, node_uuid, self.entity_description)

    @property
    @override
    def available(self) -> bool:
        """Treat missing status as unknown, not explicitly offline."""
        return super().available and self.status is not None

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the explicit node online state."""
        return self.status.online if self.status is not None else None


class KomariNodeMetadataBinarySensor(KomariNodeEntity, BinarySensorEntity):
    """A binary sensor sourced from node inventory metadata."""

    entity_description: KomariNodeMetadataBinarySensorEntityDescription

    def __init__(
        self,
        entry: KomariConfigEntry,
        node_uuid: str,
        description: KomariNodeMetadataBinarySensorEntityDescription,
    ) -> None:
        """Initialize a node metadata binary sensor."""
        super().__init__(entry, node_uuid, description)

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the normalized inventory boolean."""
        if (node := self.node) is None:
            return None
        return self.entity_description.value_fn(node)

    @property
    @override
    def available(self) -> bool:
        """Remain available while offline when metadata is known."""
        return super().available and self.is_on is not None


def _remove_node_binary_sensor_keys(
    registry: er.EntityRegistry,
    entry_id: str,
    keys: frozenset[str],
) -> None:
    """Remove one disabled node binary sensor family from this config entry."""
    prefix = f"{entry_id}:node:"
    for registry_entry in tuple(er.async_entries_for_config_entry(registry, entry_id)):
        if (
            registry_entry.domain == "binary_sensor"
            and registry_entry.unique_id.startswith(prefix)
            and registry_entry.unique_id.rsplit(":", 1)[-1] in keys
        ):
            registry.async_remove(registry_entry.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up panel binary sensors and dynamic node connectivity."""
    coordinator = entry.runtime_data.coordinator
    options = get_options(entry)
    if not options.enable_expiry_entities:
        _remove_node_binary_sensor_keys(
            er.async_get(hass), entry.entry_id, EXPIRY_BINARY_SENSOR_KEYS
        )

    async_add_entities(
        (
            KomariRpcAvailableBinarySensor(entry),
            KomariNodeProblemBinarySensor(entry),
        )
    )

    known_entity_keys: set[tuple[str, str]] = set()

    @callback
    def _async_add_new_node_entities() -> None:
        entities: list[BinarySensorEntity] = []
        for node_uuid, node in sorted(coordinator.data.nodes.items()):
            online_key = (node_uuid, NODE_ONLINE_DESCRIPTION.key)
            if online_key not in known_entity_keys:
                known_entity_keys.add(online_key)
                entities.append(KomariNodeOnlineBinarySensor(entry, node_uuid))

            if options.enable_expiry_entities:
                for description in EXPIRY_BINARY_SENSOR_DESCRIPTIONS:
                    entity_key = (node_uuid, description.key)
                    if entity_key in known_entity_keys or not description.create_fn(
                        node
                    ):
                        continue
                    known_entity_keys.add(entity_key)
                    entities.append(
                        KomariNodeMetadataBinarySensor(entry, node_uuid, description)
                    )
        if entities:
            async_add_entities(entities)

    _async_add_new_node_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_node_entities))
