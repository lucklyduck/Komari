"""Komari integration for Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KomariApiClient
from .const import CONF_API_KEY, DEFAULT_VERIFY_SSL, DOMAIN, PLATFORMS
from .coordinator import KomariDataUpdateCoordinator
from .data import KomariConfigEntry, KomariRuntimeData
from .device import register_devices

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Komari integration."""
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

    panel = register_devices(hass, entry, coordinator.data)
    entry.runtime_data = KomariRuntimeData(
        client=client,
        coordinator=coordinator,
        panel_device_id=panel.id,
    )

    def _sync_devices() -> None:
        register_devices(hass, entry, coordinator.data)

    entry.async_on_unload(coordinator.async_add_listener(_sync_devices))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
) -> bool:
    """Unload one Komari panel and its platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
