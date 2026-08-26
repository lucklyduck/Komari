"""Binary sensor platform for Komari panels and nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import callback

from .entity import KomariNodeEntity, KomariPanelEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import KomariConfigEntry


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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up panel binary sensors and dynamic node connectivity."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        (
            KomariRpcAvailableBinarySensor(entry),
            KomariNodeProblemBinarySensor(entry),
        )
    )

    known_nodes: set[str] = set()

    @callback
    def _async_add_new_node_entities() -> None:
        new_nodes = set(coordinator.data.nodes) - known_nodes
        if not new_nodes:
            return
        known_nodes.update(new_nodes)
        async_add_entities(
            KomariNodeOnlineBinarySensor(entry, node_uuid)
            for node_uuid in sorted(new_nodes)
        )

    _async_add_new_node_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_node_entities))
