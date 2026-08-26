"""Device registry topology for Komari panels and nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_URL
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import KomariSnapshot
    from .data import KomariConfigEntry


def panel_identifier(entry_id: str) -> tuple[str, str]:
    """Return the entry-scoped panel device identifier."""
    return (DOMAIN, f"{entry_id}:panel")


def node_identifier(entry_id: str, node_uuid: str) -> tuple[str, str]:
    """Return an entry-scoped node identifier."""
    return (DOMAIN, f"{entry_id}:node:{node_uuid}")


def register_devices(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    snapshot: KomariSnapshot,
) -> dr.DeviceEntry:
    """Create or update the panel device and all known node devices."""
    registry = dr.async_get(hass)
    panel = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        configuration_url=entry.data[CONF_URL],
        entry_type=dr.DeviceEntryType.SERVICE,
        identifiers={panel_identifier(entry.entry_id)},
        manufacturer="Komari",
        model="Monitoring panel",
        name=snapshot.panel.name or entry.title,
        sw_version=snapshot.panel.version,
    )

    for node in snapshot.nodes.values():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={node_identifier(entry.entry_id, node.uuid)},
            manufacturer="Komari",
            model=node.cpu_name or "Monitored node",
            name=node.name,
            sw_version=node.agent_version,
            via_device_id=panel.id,
        )

    return panel
