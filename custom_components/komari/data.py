"""Runtime data types for the Komari integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .api import KomariApiClient
    from .coordinator import KomariDataUpdateCoordinator


@dataclass(slots=True)
class KomariRuntimeData:
    """Objects owned by exactly one Komari config entry."""

    client: KomariApiClient
    coordinator: KomariDataUpdateCoordinator
    panel_device_id: str


type KomariConfigEntry = ConfigEntry[KomariRuntimeData]
