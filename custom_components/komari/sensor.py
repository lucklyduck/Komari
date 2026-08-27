"""Sensor platform for Komari panels and nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

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
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAX_PING_ENTITIES
from .entity import KomariNodeEntity, KomariPanelEntity
from .models import KomariPingSeriesKey, KomariPingStats, TrafficLimitType
from .options import get_options

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
        status is not None and status.gpu_usage is not None and status.gpu_usage > 0
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


def _has_expiry(node: KomariNode) -> bool:
    """Return whether the node has a configured expiry timestamp."""
    return node.expired_at is not None


def _remaining_time(node: KomariNode) -> float | None:
    """Return exact non-negative seconds until expiry."""
    if node.expired_at is None:
        return None
    return max(0.0, (node.expired_at - dt_util.utcnow()).total_seconds())


def _has_traffic_limit(node: KomariNode) -> bool:
    """Return whether the node has a positive configured traffic limit."""
    return node.traffic_limit is not None and node.traffic_limit > 0


def traffic_used_by_limit_rule(
    node: KomariNode,
    status: KomariNodeStatus,
) -> int | None:
    """Apply Komari's configured limit rule to current Agent counters."""
    uploaded = status.network_total_up
    downloaded = status.network_total_down
    match node.traffic_limit_type:
        case TrafficLimitType.SUM:
            if uploaded is None or downloaded is None:
                return None
            return uploaded + downloaded
        case TrafficLimitType.MAX:
            if uploaded is None or downloaded is None:
                return None
            return max(uploaded, downloaded)
        case TrafficLimitType.MIN:
            if uploaded is None or downloaded is None:
                return None
            return min(uploaded, downloaded)
        case TrafficLimitType.UP:
            return uploaded
        case TrafficLimitType.DOWN:
            return downloaded
        case _:
            return None


def _traffic_limit_usage(
    node: KomariNode,
    status: KomariNodeStatus,
) -> float | None:
    """Return an uncapped percentage based on reset-sensitive Agent counters."""
    if not _has_traffic_limit(node):
        return None
    used = traffic_used_by_limit_rule(node, status)
    if used is None or node.traffic_limit is None:
        return None
    return used / node.traffic_limit * 100


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


@dataclass(frozen=True, kw_only=True)
class KomariNodeMetadataSensorEntityDescription(SensorEntityDescription):
    """Describe a node sensor sourced from static inventory metadata."""

    value_fn: Callable[[KomariNode], SensorValue]
    create_fn: Callable[[KomariNode], bool]


@dataclass(frozen=True, kw_only=True)
class KomariNodeTrafficSensorEntityDescription(SensorEntityDescription):
    """Describe a node sensor calculated from metadata and Agent counters."""

    value_fn: Callable[[KomariNode, KomariNodeStatus], SensorValue]
    create_fn: Callable[[KomariNode], bool]


@dataclass(frozen=True, kw_only=True)
class KomariNodePingSensorEntityDescription(SensorEntityDescription):
    """Describe one selected Ping statistic on a node."""

    value_fn: Callable[[KomariPingStats], SensorValue]


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

EXPIRY_SENSOR_DESCRIPTIONS: tuple[KomariNodeMetadataSensorEntityDescription, ...] = (
    KomariNodeMetadataSensorEntityDescription(
        key="expires_at",
        translation_key="expires_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        create_fn=_has_expiry,
        value_fn=lambda node: node.expired_at,
    ),
    KomariNodeMetadataSensorEntityDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        create_fn=_has_expiry,
        value_fn=_remaining_time,
    ),
    KomariNodeMetadataSensorEntityDescription(
        key="billing_cycle",
        translation_key="billing_cycle",
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        create_fn=_has_expiry,
        value_fn=lambda node: node.billing_cycle,
    ),
)

EXPIRY_SENSOR_KEYS = frozenset(
    description.key for description in EXPIRY_SENSOR_DESCRIPTIONS
)

TRAFFIC_METADATA_SENSOR_DESCRIPTIONS: tuple[
    KomariNodeMetadataSensorEntityDescription, ...
] = (
    KomariNodeMetadataSensorEntityDescription(
        key="traffic_limit",
        translation_key="traffic_limit",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        create_fn=_has_traffic_limit,
        value_fn=lambda node: node.traffic_limit,
    ),
    KomariNodeMetadataSensorEntityDescription(
        key="traffic_limit_type",
        translation_key="traffic_limit_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        create_fn=_has_traffic_limit,
        value_fn=lambda node: (
            node.traffic_limit_type.value
            if node.traffic_limit_type is not None
            else None
        ),
    ),
)

TRAFFIC_STATUS_SENSOR_DESCRIPTIONS: tuple[
    KomariNodeTrafficSensorEntityDescription, ...
] = (
    KomariNodeTrafficSensorEntityDescription(
        key="traffic_used_by_limit_rule",
        translation_key="traffic_used_by_limit_rule",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL,
        create_fn=_has_traffic_limit,
        value_fn=traffic_used_by_limit_rule,
    ),
    KomariNodeTrafficSensorEntityDescription(
        key="traffic_limit_usage",
        translation_key="traffic_limit_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        create_fn=_has_traffic_limit,
        value_fn=_traffic_limit_usage,
    ),
)

TRAFFIC_SENSOR_KEYS = frozenset(
    description.key
    for description in (
        *TRAFFIC_METADATA_SENSOR_DESCRIPTIONS,
        *TRAFFIC_STATUS_SENSOR_DESCRIPTIONS,
    )
)

PING_SENSOR_DESCRIPTIONS: tuple[KomariNodePingSensorEntityDescription, ...] = (
    KomariNodePingSensorEntityDescription(
        key="latest",
        translation_key="ping_latest",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.latest,
    ),
    KomariNodePingSensorEntityDescription(
        key="loss",
        translation_key="ping_loss",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.loss,
    ),
    KomariNodePingSensorEntityDescription(
        key="min",
        translation_key="ping_min",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.minimum,
    ),
    KomariNodePingSensorEntityDescription(
        key="max",
        translation_key="ping_max",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.maximum,
    ),
    KomariNodePingSensorEntityDescription(
        key="avg",
        translation_key="ping_avg",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.average,
    ),
    KomariNodePingSensorEntityDescription(
        key="p50",
        translation_key="ping_p50",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.p50,
    ),
    KomariNodePingSensorEntityDescription(
        key="p99",
        translation_key="ping_p99",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.p99,
    ),
    KomariNodePingSensorEntityDescription(
        key="stddev",
        translation_key="ping_stddev",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.stddev,
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


class KomariNodeMetadataSensor(KomariNodeEntity, SensorEntity):
    """A sensor sourced from node inventory metadata."""

    entity_description: KomariNodeMetadataSensorEntityDescription

    def __init__(
        self,
        entry: KomariConfigEntry,
        node_uuid: str,
        description: KomariNodeMetadataSensorEntityDescription,
    ) -> None:
        """Initialize a node metadata sensor."""
        super().__init__(entry, node_uuid, description)

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return a normalized inventory value without I/O."""
        if (node := self.node) is None:
            return None
        return self.entity_description.value_fn(node)

    @property
    @override
    def available(self) -> bool:
        """Remain available while offline but not while the panel is unreachable."""
        return super().available and self.native_value is not None


class KomariNodeTrafficSensor(KomariNodeEntity, SensorEntity):
    """A traffic-limit sensor calculated from current Agent counters."""

    entity_description: KomariNodeTrafficSensorEntityDescription

    def __init__(
        self,
        entry: KomariConfigEntry,
        node_uuid: str,
        description: KomariNodeTrafficSensorEntityDescription,
    ) -> None:
        """Initialize a reset-sensitive traffic-limit sensor."""
        super().__init__(entry, node_uuid, description)

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return a value calculated from one coherent snapshot."""
        if (node := self.node) is None or (status := self.status) is None:
            return None
        return self.entity_description.value_fn(node, status)

    @property
    @override
    def available(self) -> bool:
        """Require an online node and all counters needed by the limit rule."""
        return (
            super().available
            and (status := self.status) is not None
            and status.online
            and self.native_value is not None
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, str | bool] | None:
        """Describe the source and reset semantics without exposing node data."""
        if self.entity_description.key != "traffic_used_by_limit_rule":
            return None
        return {
            "calculation_source": "agent_counters",
            "reset_sensitive": True,
        }


class KomariNodePingSensor(KomariNodeEntity, SensorEntity):
    """One selected Ping statistic attached to a visible node."""

    entity_description: KomariNodePingSensorEntityDescription

    def __init__(
        self,
        entry: KomariConfigEntry,
        node_uuid: str,
        task_id: str,
        description: KomariNodePingSensorEntityDescription,
    ) -> None:
        """Initialize a stable node/task/statistic entity."""
        super().__init__(entry, node_uuid, description)
        self._series_key = KomariPingSeriesKey(node_uuid, task_id)
        self._task_id = task_id
        self._attr_unique_id = (
            f"{entry.entry_id}:node:{node_uuid}:ping:{task_id}:{description.key}"
        )
        self._last_task_name = task_id
        self._update_task_name()

    def _update_task_name(self) -> None:
        """Keep display text current without making the name part of identity."""
        task_name = self._last_task_name
        if task := self.coordinator.data.ping_tasks.get(self._task_id):
            task_name = task.name
        if task_name != self._last_task_name:
            self._last_task_name = task_name
            cast("dict[str, object]", self.__dict__).pop("name", None)
        self._attr_translation_placeholders = {
            "task_name": self._last_task_name,
        }

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return only the selected field from the current Ping series."""
        if stats := self.coordinator.data.ping_stats.get(self._series_key):
            return self.entity_description.value_fn(stats)
        return None

    @property
    @override
    def available(self) -> bool:
        """Require an online node and non-stale Ping data for this field."""
        return (
            super().available
            and (status := self.status) is not None
            and status.online
            and self.coordinator.ping_stats_available
            and self.native_value is not None
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Refresh the task display name before publishing the new state."""
        self._update_task_name()
        super()._handle_coordinator_update()


def _remove_node_sensor_keys(
    registry: er.EntityRegistry,
    entry_id: str,
    keys: frozenset[str],
) -> None:
    """Remove one disabled node sensor family from this config entry."""
    prefix = f"{entry_id}:node:"
    for registry_entry in tuple(er.async_entries_for_config_entry(registry, entry_id)):
        if (
            registry_entry.domain == "sensor"
            and registry_entry.unique_id.startswith(prefix)
            and registry_entry.unique_id.rsplit(":", 1)[-1] in keys
        ):
            registry.async_remove(registry_entry.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up panel sensors and dynamically discovered node sensors."""
    coordinator = entry.runtime_data.coordinator
    registry = er.async_get(hass)
    options = get_options(entry)

    if not options.enable_expiry_entities:
        _remove_node_sensor_keys(registry, entry.entry_id, EXPIRY_SENSOR_KEYS)
    if not options.enable_traffic_entities:
        _remove_node_sensor_keys(registry, entry.entry_id, TRAFFIC_SENSOR_KEYS)

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
    known_ping_entity_keys: set[tuple[str, str, str]] = set()

    @callback
    def _async_add_new_node_entities() -> None:
        entities: list[SensorEntity] = []
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
            if options.enable_expiry_entities:
                for description in EXPIRY_SENSOR_DESCRIPTIONS:
                    entity_key = (node_uuid, description.key)
                    if entity_key in known_entity_keys or not description.create_fn(
                        node
                    ):
                        continue
                    known_entity_keys.add(entity_key)
                    entities.append(
                        KomariNodeMetadataSensor(entry, node_uuid, description)
                    )
            if options.enable_traffic_entities:
                for description in TRAFFIC_METADATA_SENSOR_DESCRIPTIONS:
                    entity_key = (node_uuid, description.key)
                    if entity_key in known_entity_keys or not description.create_fn(
                        node
                    ):
                        continue
                    known_entity_keys.add(entity_key)
                    entities.append(
                        KomariNodeMetadataSensor(entry, node_uuid, description)
                    )
                for description in TRAFFIC_STATUS_SENSOR_DESCRIPTIONS:
                    entity_key = (node_uuid, description.key)
                    if entity_key in known_entity_keys or not description.create_fn(
                        node
                    ):
                        continue
                    known_entity_keys.add(entity_key)
                    entities.append(
                        KomariNodeTrafficSensor(entry, node_uuid, description)
                    )
        selected_tasks = frozenset(options.ping_task_ids)
        selected_stats = frozenset(options.ping_stat_keys)
        for series_key in coordinator.data.ping_stats:
            if (
                series_key.node_uuid not in coordinator.data.nodes
                or series_key.task_id not in selected_tasks
            ):
                continue
            for description in PING_SENSOR_DESCRIPTIONS:
                entity_key = (
                    series_key.node_uuid,
                    series_key.task_id,
                    description.key,
                )
                if (
                    description.key not in selected_stats
                    or entity_key in known_ping_entity_keys
                    or len(known_ping_entity_keys) >= MAX_PING_ENTITIES
                ):
                    continue
                known_ping_entity_keys.add(entity_key)
                entities.append(
                    KomariNodePingSensor(
                        entry,
                        series_key.node_uuid,
                        series_key.task_id,
                        description,
                    )
                )
        if entities:
            async_add_entities(entities)

    _async_add_new_node_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_node_entities))
