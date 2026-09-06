"""Komari integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KomariApiClient
from .const import CONF_API_KEY, DEFAULT_VERIFY_SSL, DOMAIN, PLATFORMS
from .coordinator import KomariDataUpdateCoordinator
from .data import KomariConfigEntry, KomariRuntimeData
from .device import register_devices
from .lifecycle import (
    node_uuid_from_identifiers,
    registry_node_ids,
    registry_ping_task_ids,
    remove_disallowed_metric_entities,
    remove_disallowed_ping_entities,
    remove_node_registry_entries,
)
from .options import get_options
from .repairs import clear_repairs, update_repairs

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)
CONFIG_ENTRY_VERSION = 1
CONFIG_ENTRY_MINOR_VERSION = 1


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Komari integration."""
    return True


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
) -> bool:
    """Migrate an older Komari config entry without changing its identity."""
    if entry.version != CONFIG_ENTRY_VERSION:
        return False
    if entry.minor_version >= CONFIG_ENTRY_MINOR_VERSION:
        return True

    try:
        hass.config_entries.async_update_entry(
            entry,
            version=CONFIG_ENTRY_VERSION,
            minor_version=CONFIG_ENTRY_MINOR_VERSION,
        )
    except Exception:
        _LOGGER.exception("Unable to migrate Komari config entry")
        return False
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
) -> bool:
    """Set up one isolated Komari panel."""
    session = async_get_clientsession(
        hass,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    client = KomariApiClient(
        session,
        entry.data[CONF_URL],
        entry.data.get(CONF_API_KEY),
    )
    coordinator = KomariDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    coordinator.track_registry_node_ids(
        registry_node_ids(device_registry, entry.entry_id)
    )
    coordinator.track_registry_ping_task_ids(
        registry_ping_task_ids(entity_registry, entry.entry_id)
    )
    options = get_options(entry)
    remove_disallowed_ping_entities(
        entity_registry,
        entry.entry_id,
        allowed_task_ids=options.ping_task_ids,
        allowed_stat_keys=options.ping_stat_keys,
    )
    remove_disallowed_metric_entities(
        entity_registry,
        entry.entry_id,
        allowed_metric_keys=options.metric_keys,
    )

    panel = register_devices(hass, entry, coordinator.data)
    entry.runtime_data = KomariRuntimeData(
        client=client,
        coordinator=coordinator,
        panel_device_id=panel.id,
    )
    update_repairs(hass, entry)

    def _sync_devices() -> None:
        register_devices(hass, entry, coordinator.data)
        for node_uuid in coordinator.confirmed_removed_node_ids:
            remove_node_registry_entries(
                device_registry,
                entity_registry,
                entry.entry_id,
                node_uuid,
            )
        remove_disallowed_ping_entities(
            entity_registry,
            entry.entry_id,
            allowed_task_ids=options.ping_task_ids,
            allowed_stat_keys=options.ping_stat_keys,
            confirmed_removed_task_ids=(coordinator.confirmed_removed_ping_task_ids),
        )
        remove_disallowed_metric_entities(
            entity_registry,
            entry.entry_id,
            allowed_metric_keys=options.metric_keys,
            confirmed_removed_metric_keys=(coordinator.confirmed_removed_metric_keys),
        )
        update_repairs(hass, entry)

    entry.async_on_unload(coordinator.async_add_listener(_sync_devices))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
) -> bool:
    """Unload one Komari panel and its platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: KomariConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow manual removal only after a node leaves the latest raw inventory."""
    if (
        node_uuid := node_uuid_from_identifiers(
            config_entry.entry_id,
            device_entry.identifiers,
        )
    ) is None:
        return False
    return node_uuid not in config_entry.runtime_data.coordinator.last_observed_node_ids


async def async_remove_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
) -> None:
    """Clear Repair issues after a Komari config entry is deleted."""
    clear_repairs(hass, entry.entry_id)
