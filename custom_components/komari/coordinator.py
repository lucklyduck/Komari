"""Data coordinator for one Komari config entry."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING, Never, override

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CAPABILITY_REFRESH_INTERVAL,
    DOMAIN,
    INVENTORY_REFRESH_INTERVAL,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_PING_TASKS,
    PING_QUERY_HOURS,
    PING_QUERY_MAX_POINTS,
    PING_TASK_REFRESH_INTERVAL,
)
from .exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariError,
    KomariMethodNotFoundError,
    KomariPermissionError,
    KomariProtocolError,
    KomariRpcError,
    KomariUnsupportedError,
)
from .options import KomariOptions, get_options

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
        KomariPingSeriesKey,
        KomariPingStats,
        KomariPingTask,
    )

_LOGGER = logging.getLogger(__name__)

BRANCH_CORE = "core"
BRANCH_INVENTORY = "inventory"
BRANCH_PING_TASKS = "ping_tasks"
BRANCH_PING_STATS = "ping_stats"
BRANCH_CAPABILITIES = "capabilities"
BRANCHES = (
    BRANCH_CORE,
    BRANCH_INVENTORY,
    BRANCH_PING_TASKS,
    BRANCH_PING_STATS,
    BRANCH_CAPABILITIES,
)


def _empty_ping_tasks() -> Mapping[str, KomariPingTask]:
    """Return an immutable empty Ping-task mapping."""
    return MappingProxyType({})


def _empty_ping_stats() -> Mapping[KomariPingSeriesKey, KomariPingStats]:
    """Return an immutable empty Ping-statistics mapping."""
    return MappingProxyType({})


def _empty_branch_states() -> Mapping[str, KomariBranchState]:
    """Return an immutable empty branch-state mapping."""
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class KomariBranchState:
    """Sanitized refresh state for one independently failing branch."""

    fetched_at: datetime | None = None
    error_category: str | None = None
    consecutive_failures: int = 0


@dataclass(frozen=True, slots=True)
class KomariSnapshot:
    """One coherent per-panel view exposed to all entity platforms."""

    panel: KomariPanelInfo
    nodes: Mapping[str, KomariNode]
    statuses: Mapping[str, KomariNodeStatus]
    capabilities: frozenset[str]
    ping_tasks: Mapping[str, KomariPingTask] = field(default_factory=_empty_ping_tasks)
    ping_stats: Mapping[KomariPingSeriesKey, KomariPingStats] = field(
        default_factory=_empty_ping_stats
    )
    branch_states: Mapping[str, KomariBranchState] = field(
        default_factory=_empty_branch_states
    )

    @classmethod
    def from_bootstrap(
        cls,
        data: KomariBootstrapData,
        *,
        branch_states: Mapping[str, KomariBranchState] | None = None,
    ) -> KomariSnapshot:
        """Create the first coordinator snapshot."""
        return cls(
            panel=data.panel,
            nodes=data.nodes,
            statuses=data.statuses,
            capabilities=data.capabilities,
            branch_states=branch_states or _initial_branch_states(),
        )


def _initial_branch_states() -> Mapping[str, KomariBranchState]:
    """Return immutable empty branch state for standalone snapshot creation."""
    return MappingProxyType({branch: KomariBranchState() for branch in BRANCHES})


class KomariDataUpdateCoordinator(DataUpdateCoordinator[KomariSnapshot]):
    """Coordinate independently scheduled data branches for one panel."""

    config_entry: KomariConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: KomariConfigEntry,
        client: KomariApiClient,
    ) -> None:
        """Initialize a panel-scoped coordinator."""
        self.options: KomariOptions = get_options(config_entry)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=self.options.scan_interval),
        )
        self.client = client
        self._snapshot: KomariSnapshot | None = None
        self._last_inventory_attempt: float | None = None
        self._last_ping_tasks_attempt: float | None = None
        self._last_ping_stats_attempt: float | None = None
        self._last_capabilities_attempt: float | None = None
        self._branch_states: dict[str, KomariBranchState] = {
            branch: KomariBranchState() for branch in BRANCHES
        }

    @property
    def branch_states(self) -> Mapping[str, KomariBranchState]:
        """Return an immutable view of the latest branch states."""
        return MappingProxyType(dict(self._branch_states))

    @property
    def ping_stats_available(self) -> bool:
        """Return whether Ping data remains inside its two-failure TTL."""
        state = self._branch_states[BRANCH_PING_STATS]
        return state.fetched_at is not None and state.consecutive_failures < 2

    @override
    async def _async_update_data(self) -> KomariSnapshot:
        """Refresh due branches and publish one immutable snapshot."""
        if self._snapshot is None:
            return await self._async_bootstrap()

        statuses = await self._async_refresh_core()
        panel = self._snapshot.panel
        nodes = self._snapshot.nodes
        capabilities = self._snapshot.capabilities
        ping_tasks = self._snapshot.ping_tasks
        ping_stats = self._snapshot.ping_stats
        now = monotonic()

        if self._inventory_is_due(now):
            self._last_inventory_attempt = now
            try:
                nodes = await self.client.async_get_nodes(capabilities)
            except (KomariAuthenticationError, KomariPermissionError) as err:
                self._record_failure(BRANCH_INVENTORY, err)
                self._raise_auth_failed(err)
            except KomariError as err:
                self._record_failure(BRANCH_INVENTORY, err)
            else:
                self._record_success(BRANCH_INVENTORY)

        if self._ping_tasks_are_due(now):
            self._last_ping_tasks_attempt = now
            ping_tasks, capabilities = await self._async_refresh_ping_tasks(
                ping_tasks,
                capabilities,
            )

        if self.options.ping_task_ids and self._ping_stats_are_due(now):
            self._last_ping_stats_attempt = now
            ping_stats, capabilities = await self._async_refresh_ping_stats(
                ping_stats,
                capabilities,
                nodes,
            )

        if self._capabilities_are_due(now):
            self._last_capabilities_attempt = now
            try:
                panel, capabilities = await self.client.async_get_metadata()
            except (KomariAuthenticationError, KomariPermissionError) as err:
                self._record_failure(BRANCH_CAPABILITIES, err)
                self._raise_auth_failed(err)
            except KomariError as err:
                self._record_failure(BRANCH_CAPABILITIES, err)
            else:
                self._record_success(BRANCH_CAPABILITIES)

        return self._publish_snapshot(
            panel=panel,
            nodes=nodes,
            statuses=statuses,
            capabilities=capabilities,
            ping_tasks=ping_tasks,
            ping_stats=ping_stats,
        )

    async def _async_bootstrap(self) -> KomariSnapshot:
        """Load the initial core snapshot and optional Ping branches."""
        try:
            bootstrap = await self.client.async_get_bootstrap()
        except (KomariAuthenticationError, KomariPermissionError) as err:
            self._record_failure(BRANCH_CORE, err)
            self._raise_auth_failed(err)
        except KomariError as err:
            self._record_failure(BRANCH_CORE, err)
            self._raise_update_failed(err)

        now = monotonic()
        self._last_inventory_attempt = now
        self._last_capabilities_attempt = now
        self._record_success(BRANCH_CORE)
        self._record_success(BRANCH_INVENTORY)
        self._record_success(BRANCH_CAPABILITIES)

        snapshot = KomariSnapshot.from_bootstrap(
            bootstrap,
            branch_states=self.branch_states,
        )
        self._snapshot = snapshot
        ping_tasks = snapshot.ping_tasks
        ping_stats = snapshot.ping_stats
        capabilities = snapshot.capabilities

        if METHOD_PUBLIC_PING_TASKS in capabilities:
            self._last_ping_tasks_attempt = now
            ping_tasks, capabilities = await self._async_refresh_ping_tasks(
                ping_tasks,
                capabilities,
            )
        if self.options.ping_task_ids and METHOD_PING_METRIC_STATS in capabilities:
            self._last_ping_stats_attempt = now
            ping_stats, capabilities = await self._async_refresh_ping_stats(
                ping_stats,
                capabilities,
                snapshot.nodes,
            )

        return self._publish_snapshot(
            panel=snapshot.panel,
            nodes=snapshot.nodes,
            statuses=snapshot.statuses,
            capabilities=capabilities,
            ping_tasks=ping_tasks,
            ping_stats=ping_stats,
        )

    async def _async_refresh_core(self) -> Mapping[str, KomariNodeStatus]:
        """Refresh mandatory latest status and map core failures."""
        try:
            statuses = await self.client.async_get_latest_status()
        except (KomariAuthenticationError, KomariPermissionError) as err:
            self._record_failure(BRANCH_CORE, err)
            self._raise_auth_failed(err)
        except KomariError as err:
            self._record_failure(BRANCH_CORE, err)
            self._raise_update_failed(err)
        self._record_success(BRANCH_CORE)
        return statuses

    async def _async_refresh_ping_tasks(
        self,
        prior: Mapping[str, KomariPingTask],
        capabilities: frozenset[str],
    ) -> tuple[Mapping[str, KomariPingTask], frozenset[str]]:
        """Refresh optional Ping tasks without affecting core data."""
        if METHOD_PUBLIC_PING_TASKS not in capabilities:
            return prior, capabilities
        try:
            tasks = await self.client.async_get_ping_tasks(capabilities)
        except (KomariAuthenticationError, KomariPermissionError) as err:
            self._record_failure(BRANCH_PING_TASKS, err)
            self._raise_auth_failed(err)
        except KomariMethodNotFoundError as err:
            self._record_failure(BRANCH_PING_TASKS, err)
            return MappingProxyType({}), capabilities - {METHOD_PUBLIC_PING_TASKS}
        except KomariError as err:
            self._record_failure(BRANCH_PING_TASKS, err)
            return prior, capabilities
        self._record_success(BRANCH_PING_TASKS)
        return tasks, capabilities

    async def _async_refresh_ping_stats(
        self,
        prior: Mapping[KomariPingSeriesKey, KomariPingStats],
        capabilities: frozenset[str],
        nodes: Mapping[str, KomariNode],
    ) -> tuple[
        Mapping[KomariPingSeriesKey, KomariPingStats],
        frozenset[str],
    ]:
        """Refresh selected Ping statistics in one batch request."""
        if METHOD_PING_METRIC_STATS not in capabilities:
            return prior, capabilities
        try:
            stats = await self.client.async_get_ping_stats(
                capabilities,
                tuple(nodes),
                self.options.ping_task_ids,
                hours=PING_QUERY_HOURS,
                max_points=PING_QUERY_MAX_POINTS,
            )
        except (KomariAuthenticationError, KomariPermissionError) as err:
            self._record_failure(BRANCH_PING_STATS, err)
            self._raise_auth_failed(err)
        except KomariMethodNotFoundError as err:
            self._record_failure(BRANCH_PING_STATS, err)
            return prior, capabilities - {METHOD_PING_METRIC_STATS}
        except KomariError as err:
            self._record_failure(BRANCH_PING_STATS, err)
            return prior, capabilities
        self._record_success(BRANCH_PING_STATS)
        return stats, capabilities

    def _publish_snapshot(
        self,
        *,
        panel: KomariPanelInfo,
        nodes: Mapping[str, KomariNode],
        statuses: Mapping[str, KomariNodeStatus],
        capabilities: frozenset[str],
        ping_tasks: Mapping[str, KomariPingTask],
        ping_stats: Mapping[KomariPingSeriesKey, KomariPingStats],
    ) -> KomariSnapshot:
        """Publish one frozen branch-consistent view."""
        self._snapshot = KomariSnapshot(
            panel=panel,
            nodes=nodes,
            statuses=statuses,
            capabilities=capabilities,
            ping_tasks=ping_tasks,
            ping_stats=ping_stats,
            branch_states=self.branch_states,
        )
        return self._snapshot

    def _record_success(self, branch: str) -> None:
        """Record a sanitized branch success."""
        previous = self._branch_states[branch]
        if previous.consecutive_failures:
            _LOGGER.info("Komari %s refresh recovered", branch)
        self._branch_states[branch] = KomariBranchState(
            fetched_at=datetime.now(tz=UTC),
        )

    def _record_failure(self, branch: str, error: Exception) -> None:
        """Record only a safe error category and failure count."""
        previous = self._branch_states[branch]
        category = _error_category(error)
        if previous.consecutive_failures:
            _LOGGER.debug("Komari %s refresh still failing (%s)", branch, category)
        elif branch == BRANCH_INVENTORY:
            _LOGGER.warning(
                "Komari inventory refresh failed (%s); keeping prior inventory",
                category,
            )
        else:
            _LOGGER.warning("Komari %s refresh failed (%s)", branch, category)
        self._branch_states[branch] = KomariBranchState(
            fetched_at=previous.fetched_at,
            error_category=category,
            consecutive_failures=previous.consecutive_failures + 1,
        )

    @staticmethod
    def _raise_auth_failed(error: Exception) -> Never:
        """Raise Home Assistant's reauthentication signal."""
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
        ) from error

    @staticmethod
    def _raise_update_failed(error: Exception) -> Never:
        """Raise a sanitized mandatory-update failure."""
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="update_failed",
        ) from error

    def _inventory_is_due(self, now: float) -> bool:
        """Return whether the lower-frequency node inventory is due."""
        return _is_due(self._last_inventory_attempt, now, INVENTORY_REFRESH_INTERVAL)

    def _ping_tasks_are_due(self, now: float) -> bool:
        """Return whether public Ping task discovery is due."""
        return _is_due(
            self._last_ping_tasks_attempt,
            now,
            PING_TASK_REFRESH_INTERVAL,
        )

    def _ping_stats_are_due(self, now: float) -> bool:
        """Return whether selected Ping statistics are due."""
        return _is_due(
            self._last_ping_stats_attempt,
            now,
            timedelta(seconds=self.options.ping_interval),
        )

    def _capabilities_are_due(self, now: float) -> bool:
        """Return whether metadata and capability discovery are due."""
        return _is_due(
            self._last_capabilities_attempt,
            now,
            CAPABILITY_REFRESH_INTERVAL,
        )


def _is_due(last_attempt: float | None, now: float, interval: timedelta) -> bool:
    """Return whether a monotonic branch interval has elapsed."""
    return last_attempt is None or now - last_attempt >= interval.total_seconds()


def _error_category(error: Exception) -> str:
    """Map exceptions to non-sensitive diagnostics categories."""
    if isinstance(error, KomariAuthenticationError | KomariPermissionError):
        return "authentication"
    if isinstance(error, KomariConnectionError):
        return "connection"
    if isinstance(error, KomariProtocolError):
        return "protocol"
    if isinstance(error, KomariMethodNotFoundError):
        return "method_not_found"
    if isinstance(error, KomariUnsupportedError):
        return "unsupported"
    if isinstance(error, KomariRpcError):
        return "rpc"
    return "unknown"
