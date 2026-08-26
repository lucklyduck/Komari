"""Data coordinator for one Komari config entry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, override

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, INVENTORY_REFRESH_INTERVAL
from .exceptions import (
    KomariAuthenticationError,
    KomariError,
    KomariPermissionError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from .api import KomariApiClient
    from .data import KomariConfigEntry
    from .models import (
        KomariBootstrapData,
        KomariNode,
        KomariNodeStatus,
        KomariPanelInfo,
    )

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KomariSnapshot:
    """One coherent per-panel view exposed to all entity platforms."""

    panel: KomariPanelInfo
    nodes: Mapping[str, KomariNode]
    statuses: Mapping[str, KomariNodeStatus]
    capabilities: frozenset[str]

    @classmethod
    def from_bootstrap(cls, data: KomariBootstrapData) -> KomariSnapshot:
        """Create the first coordinator snapshot."""
        return cls(
            panel=data.panel,
            nodes=data.nodes,
            statuses=data.statuses,
            capabilities=data.capabilities,
        )


class KomariDataUpdateCoordinator(DataUpdateCoordinator[KomariSnapshot]):
    """Coordinate status and lower-frequency inventory requests for one panel."""

    config_entry: KomariConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: KomariConfigEntry,
        client: KomariApiClient,
    ) -> None:
        """Initialize a panel-scoped coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._snapshot: KomariSnapshot | None = None
        self._last_inventory_attempt: float | None = None

    @override
    async def _async_update_data(self) -> KomariSnapshot:
        """Fetch status every cycle and inventory every ten minutes."""
        if self._snapshot is None:
            try:
                bootstrap = await self.client.async_get_bootstrap()
            except (KomariAuthenticationError, KomariPermissionError) as err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                ) from err
            except KomariError as err:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="update_failed",
                ) from err

            self._last_inventory_attempt = monotonic()
            self._snapshot = KomariSnapshot.from_bootstrap(bootstrap)
            return self._snapshot

        try:
            statuses = await self.client.async_get_latest_status()
        except (KomariAuthenticationError, KomariPermissionError) as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except KomariError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err

        nodes = self._snapshot.nodes
        now = monotonic()
        if self._inventory_is_due(now):
            self._last_inventory_attempt = now
            try:
                nodes = await self.client.async_get_nodes(self._snapshot.capabilities)
            except (KomariAuthenticationError, KomariPermissionError) as err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="auth_failed",
                ) from err
            except KomariError:
                _LOGGER.warning(
                    "Unable to refresh Komari node inventory; keeping prior inventory"
                )

        self._snapshot = KomariSnapshot(
            panel=self._snapshot.panel,
            nodes=nodes,
            statuses=statuses,
            capabilities=self._snapshot.capabilities,
        )
        return self._snapshot

    def _inventory_is_due(self, now: float) -> bool:
        """Return whether the lower-frequency node inventory is due."""
        if self._last_inventory_attempt is None:
            return True
        return (
            now - self._last_inventory_attempt
            >= INVENTORY_REFRESH_INTERVAL.total_seconds()
        )
