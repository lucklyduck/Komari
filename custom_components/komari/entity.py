"""Shared entity bases for the Komari integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import KomariDataUpdateCoordinator
from .device import node_identifier, panel_identifier

if TYPE_CHECKING:
    from homeassistant.helpers.entity import EntityDescription

    from .data import KomariConfigEntry
    from .models import KomariNode, KomariNodeStatus


class KomariPanelEntity(CoordinatorEntity[KomariDataUpdateCoordinator]):
    """Base for an entity attached to one panel service device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: KomariConfigEntry,
        description: EntityDescription,
    ) -> None:
        """Initialize a panel entity."""
        super().__init__(entry.runtime_data.coordinator, context="panel")
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}:panel:{self.entity_description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={panel_identifier(entry.entry_id)}
        )


class KomariNodeEntity(CoordinatorEntity[KomariDataUpdateCoordinator]):
    """Base for an entity attached to one entry-scoped node device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: KomariConfigEntry,
        node_uuid: str,
        description: EntityDescription,
    ) -> None:
        """Initialize a node entity."""
        super().__init__(entry.runtime_data.coordinator, context=node_uuid)
        self.entity_description = description
        self._entry_id = entry.entry_id
        self._node_uuid = node_uuid
        self._attr_unique_id = (
            f"{entry.entry_id}:node:{node_uuid}:{self.entity_description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={node_identifier(entry.entry_id, node_uuid)},
            via_device_id=entry.runtime_data.panel_device_id,
        )

    @property
    def node(self) -> KomariNode | None:
        """Return current node metadata."""
        return self.coordinator.data.nodes.get(self._node_uuid)

    @property
    def status(self) -> KomariNodeStatus | None:
        """Return the current latest-status record."""
        return self.coordinator.data.statuses.get(self._node_uuid)

    @property
    @override
    def available(self) -> bool:
        """Require a valid panel snapshot and current inventory membership."""
        return super().available and self.node is not None
