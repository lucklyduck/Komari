"""Sensor platform for Komari panels and nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import KomariNodeEntity, KomariPanelEntity

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime
    from decimal import Decimal

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import KomariSnapshot
    from .data import KomariConfigEntry
    from .models import KomariNode, KomariNodeStatus

    type SensorValue = StateType | date | datetime | Decimal


def usage_percentage(used: int | None, total: int | None) -> float | None:
    """Calculate a usage percentage while preserving a valid zero value."""
    if used is None or total is None or total <= 0:
        return None
    return used / total * 100


def _node_count(snapshot: KomariSnapshot) -> int:
    """Return the inventory node count."""
    return len(snapshot.nodes)


def _online_node_count(snapshot: KomariSnapshot) -> int:
    """Count nodes explicitly reported online."""
    return sum(
        status is not None and status.online
        for node_uuid in snapshot.nodes
        if (status := snapshot.statuses.get(node_uuid)) is not None
    )


def _offline_node_count(snapshot: KomariSnapshot) -> int:
    """Count nodes explicitly reported offline."""
    return sum(
        status is not None and not status.online
        for node_uuid in snapshot.nodes
        if (status := snapshot.statuses.get(node_uuid)) is not None
    )


def _always_create(
    node: KomariNode,
    status: KomariNodeStatus | None,
) -> bool:
    """Create a stable core entity for every inventory node."""
    return True


def _has_gpu(node: KomariNode, status: KomariNodeStatus | None) -> bool:
    """Return whether GPU metadata or a GPU value has been observed."""
    return bool(node.gpu_name) or (
        status is not None
        and status.gpu_usage is not None
        and status.gpu_usage > 0
    )


def _has_swap(node: KomariNode, status: KomariNodeStatus | None) -> bool:
    """Return whether the node reports usable swap capacity."""
    return (node.swap_total is not None and node.swap_total > 0) or (
        status is not None and status.swap_total is not None and status.swap_total > 0
    )


def _has_temperature(node: KomariNode, status: KomariNodeStatus | None) -> bool:
    """Return whether a valid temperature has been observed."""
    return status is not None and _valid_temperature(status.temperature) is not None


def _valid_temperature(value: float | None) -> float | None:
    """Return a physically plausible Celsius value reported by Komari."""
    if value is None or value <= 0 or value > 200:
        return None
    return value


@dataclass(frozen=True, kw_only=True)
class KomariPanelSensorEntityDescription(SensorEntityDescription):
    """Describe a panel sensor."""

    value_fn: Callable[[KomariSnapshot], SensorValue]


@dataclass(frozen=True, kw_only=True)
class KomariNodeSensorEntityDescription(SensorEntityDescription):
    """Describe a node sensor and its creation/availability rules."""

    value_fn: Callable[[KomariNodeStatus], SensorValue]
    create_fn: Callable[[KomariNode, KomariNodeStatus | None], bool] = _always_create
    requires_online: bool = True


PANEL_SENSOR_DESCRIPTIONS: tuple[KomariPanelSensorEntityDescription, ...] = (
    KomariPanelSensorEntityDescription(
        key="node_count",
        translation_key="node_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_node_count,
    ),
    KomariPanelSensorEntityDescription(
        key="online_node_count",
        translation_key="online_node_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_online_node_count,
    ),
    KomariPanelSensorEntityDescription(
        key="offline_node_count",
        translation_key="offline_node_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_offline_node_count,
    ),
    KomariPanelSensorEntityDescription(
        key="komari_version",
        translation_key="komari_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.panel.version,
    ),
    KomariPanelSensorEntityDescription(
        key="rpc_version",
        translation_key="rpc_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.panel.rpc_version,
    ),
    KomariPanelSensorEntityDescription(
        key="build_hash",
        translation_key="build_hash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.panel.build_hash,
    ),
)

NODE_SENSOR_DESCRIPTIONS: tuple[KomariNodeSensorEntityDescription, ...] = (
    KomariNodeSensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_online=False,
        value_fn=lambda status: status.observed_at,
    ),
    KomariNodeSensorEntityDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda status: status.cpu_usage,
    ),
    KomariNodeSensorEntityDescription(
        key="gpu_usage",
        translation_key="gpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        create_fn=_has_gpu,
        value_fn=lambda status: status.gpu_usage,
    ),
    KomariNodeSensorEntityDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda status: usage_percentage(
            status.memory_used, status.memory_total
        ),
    ),
    KomariNodeSensorEntityDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.memory_used,
    ),
    KomariNodeSensorEntityDescription(
        key="memory_total",
        translation_key="memory_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.memory_total,
    ),
    KomariNodeSensorEntityDescription(
        key="swap_usage",
        translation_key="swap_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        create_fn=_has_swap,
        value_fn=lambda status: usage_percentage(status.swap_used, status.swap_total),
    ),
    KomariNodeSensorEntityDescription(
        key="swap_used",
        translation_key="swap_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        create_fn=_has_swap,
        value_fn=lambda status: status.swap_used,
    ),
    KomariNodeSensorEntityDescription(
        key="disk_usage",
        translation_key="disk_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda status: usage_percentage(status.disk_used, status.disk_total),
    ),
    KomariNodeSensorEntityDescription(
        key="disk_used",
        translation_key="disk_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.disk_used,
    ),
    KomariNodeSensorEntityDescription(
        key="disk_total",
        translation_key="disk_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.disk_total,
    ),
    KomariNodeSensorEntityDescription(
        key="load_1m",
        translation_key="load_1m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda status: status.load_1m,
    ),
    KomariNodeSensorEntityDescription(
        key="load_5m",
        translation_key="load_5m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda status: status.load_5m,
    ),
    KomariNodeSensorEntityDescription(
        key="load_15m",
        translation_key="load_15m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda status: status.load_15m,
    ),
    KomariNodeSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        create_fn=_has_temperature,
        value_fn=lambda status: _valid_temperature(status.temperature),
    ),
    KomariNodeSensorEntityDescription(
        key="network_in",
        translation_key="network_in",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.network_in_rate,
    ),
    KomariNodeSensorEntityDescription(
        key="network_out",
        translation_key="network_out",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.network_out_rate,
    ),
    KomariNodeSensorEntityDescription(
        key="network_total_up",
        translation_key="network_total_up",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.network_total_up,
    ),
    KomariNodeSensorEntityDescription(
        key="network_total_down",
        translation_key="network_total_down",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.network_total_down,
    ),
    KomariNodeSensorEntityDescription(
        key="process_count",
        translation_key="process_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.process_count,
    ),
    KomariNodeSensorEntityDescription(
        key="tcp_connections",
        translation_key="tcp_connections",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.tcp_connections,
    ),
    KomariNodeSensorEntityDescription(
        key="udp_connections",
        translation_key="udp_connections",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status: status.udp_connections,
    ),
)


class KomariPanelSensor(KomariPanelEntity, SensorEntity):
    """A sensor on the panel service device."""

    entity_description: KomariPanelSensorEntityDescription

    def __init__(
        self,
        entry: KomariConfigEntry,
        description: KomariPanelSensorEntityDescription,
    ) -> None:
        """Initialize a panel sensor."""
        super().__init__(entry, description)

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return the value from the current coherent snapshot."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    @override
    def available(self) -> bool:
        """Require a successful panel update and a concrete value."""
        return super().available and self.native_value is not None


class KomariNodeSensor(KomariNodeEntity, SensorEntity):
    """A latest-status sensor on one node device."""

    entity_description: KomariNodeSensorEntityDescription

    def __init__(
        self,
        entry: KomariConfigEntry,
        node_uuid: str,
        description: KomariNodeSensorEntityDescription,
    ) -> None:
        """Initialize a node sensor."""
        super().__init__(entry, node_uuid, description)

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return a normalized status value without I/O."""
        if (status := self.status) is None:
            return None
        return self.entity_description.value_fn(status)

    @property
    @override
    def available(self) -> bool:
        """Apply node status, online, and value availability rules."""
        if not super().available or (status := self.status) is None:
            return False
        if self.entity_description.requires_online and not status.online:
            return False
        return self.native_value is not None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up panel sensors and dynamically discovered node sensors."""
    coordinator = entry.runtime_data.coordinator
    registry = er.async_get(hass)

    for node_uuid, node in coordinator.data.nodes.items():
        status = coordinator.data.statuses.get(node_uuid)
        optional_capabilities = {
            "gpu_usage": _has_gpu(node, status),
            "temperature": _has_temperature(node, status),
        }
        for key, supported in optional_capabilities.items():
            if supported:
                continue
            unique_id = f"{entry.entry_id}:node:{node_uuid}:{key}"
            if entity_id := registry.async_get_entity_id("sensor", DOMAIN, unique_id):
                registry.async_remove(entity_id)

    async_add_entities(
        KomariPanelSensor(entry, description)
        for description in PANEL_SENSOR_DESCRIPTIONS
    )

    known_entity_keys: set[tuple[str, str]] = set()

    @callback
    def _async_add_new_node_entities() -> None:
        entities: list[KomariNodeSensor] = []
        for node_uuid, node in coordinator.data.nodes.items():
            status = coordinator.data.statuses.get(node_uuid)
            for description in NODE_SENSOR_DESCRIPTIONS:
                entity_key = (node_uuid, description.key)
                if entity_key in known_entity_keys or not description.create_fn(
                    node, status
                ):
                    continue
                known_entity_keys.add(entity_key)
                entities.append(KomariNodeSensor(entry, node_uuid, description))
        if entities:
            async_add_entities(entities)

    _async_add_new_node_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_node_entities))
