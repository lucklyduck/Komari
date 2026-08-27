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

from .api import select_ping_stats_method
from .const import (
    AUTHORITATIVE_MISSING_THRESHOLD,
    CAPABILITY_REFRESH_INTERVAL,
    DOMAIN,
    INVENTORY_REFRESH_INTERVAL,
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
    from collections.abc import Iterable, Mapping

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
        self._node_missing_counts: dict[str, int] = {}
        self._ping_task_missing_counts: dict[str, int] = {}
        self._tracked_node_ids: set[str] = set()
        self._tracked_ping_task_ids: set[str] = set(self.options.ping_task_ids)
        self._last_observed_node_ids: frozenset[str] = frozenset()
        self._confirmed_removed_node_ids: set[str] = set()
        self._confirmed_removed_ping_task_ids: set[str] = set()

    @property
    def branch_states(self) -> Mapping[str, KomariBranchState]:
        """Return an immutable view of the latest branch states."""
        return MappingProxyType(dict(self._branch_states))

    @property
    def ping_stats_available(self) -> bool:
        """Return whether Ping data remains inside its two-failure TTL."""
        state = self._branch_states[BRANCH_PING_STATS]
        return state.fetched_at is not None and state.consecutive_failures < 2

    @property
    def node_missing_counts(self) -> Mapping[str, int]:
        """Return current authoritative node-absence counters."""
        return MappingProxyType(dict(self._node_missing_counts))

    @property
    def ping_task_missing_counts(self) -> Mapping[str, int]:
        """Return current authoritative Ping-task absence counters."""
        return MappingProxyType(dict(self._ping_task_missing_counts))

    @property
    def last_observed_node_ids(self) -> frozenset[str]:
        """Return node IDs from the most recent successful raw inventory."""
        return self._last_observed_node_ids

    @property
    def confirmed_removed_node_ids(self) -> frozenset[str]:
        """Return nodes confirmed absent during this coordinator lifetime."""
        return frozenset(self._confirmed_removed_node_ids)

    @property
    def confirmed_removed_ping_task_ids(self) -> frozenset[str]:
        """Return Ping tasks confirmed absent during this coordinator lifetime."""
        return frozenset(self._confirmed_removed_ping_task_ids)

    def track_registry_node_ids(self, node_ids: Iterable[str]) -> None:
        """Include pre-existing registry nodes in future absence confirmation."""
        self._tracked_node_ids.update(node_ids)

    def track_registry_ping_task_ids(self, task_ids: Iterable[str]) -> None:
        """Include pre-existing registry tasks in future absence confirmation."""
        self._tracked_ping_task_ids.update(task_ids)

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
                observed_nodes = await self.client.async_get_nodes(capabilities)
            except (KomariAuthenticationError, KomariPermissionError) as err:
                self._record_failure(BRANCH_INVENTORY, err)
                self._raise_auth_failed(err)
            except KomariError as err:
                self._record_failure(BRANCH_INVENTORY, err)
            else:
                self._last_observed_node_ids = frozenset(observed_nodes)
                nodes, removed_node_ids = _retain_until_confirmed_missing(
                    nodes,
                    observed_nodes,
                    self._node_missing_counts,
                    self._tracked_node_ids,
                )
                self._confirmed_removed_node_ids.difference_update(observed_nodes)
                self._confirmed_removed_node_ids.update(removed_node_ids)
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
        self._last_observed_node_ids = frozenset(bootstrap.nodes)
        self._tracked_node_ids.update(bootstrap.nodes)

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
        if self.options.ping_task_ids:
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
            return prior, capabilities - {METHOD_PUBLIC_PING_TASKS}
        except KomariError as err:
            self._record_failure(BRANCH_PING_TASKS, err)
            return prior, capabilities
        retained_tasks, removed_task_ids = _retain_until_confirmed_missing(
            prior,
            tasks,
            self._ping_task_missing_counts,
            self._tracked_ping_task_ids,
        )
        self._confirmed_removed_ping_task_ids.difference_update(tasks)
        self._confirmed_removed_ping_task_ids.update(removed_task_ids)
        self._record_success(BRANCH_PING_TASKS)
        return retained_tasks, capabilities

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
        if not nodes:
            self._record_success(BRANCH_PING_STATS)
            return MappingProxyType({}), capabilities
        try:
            selected_method = select_ping_stats_method(
                capabilities,
                tuple(nodes),
                self.options.ping_task_ids,
            )
        except KomariUnsupportedError as err:
            self._record_failure(BRANCH_PING_STATS, err)
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
            return prior, capabilities - {selected_method}
        except KomariError as err:
            self._record_failure(BRANCH_PING_STATS, err)
            return prior, capabilities
        visible_nodes = frozenset(nodes)
        selected_tasks = frozenset(self.options.ping_task_ids)
        filtered_stats = MappingProxyType(
            {
                key: value
                for key, value in stats.items()
                if key.node_uuid in visible_nodes and key.task_id in selected_tasks
            }
        )
        self._record_success(BRANCH_PING_STATS)
        return filtered_stats, capabilities

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
        error_type = type(error).__name__
        if previous.consecutive_failures:
            _LOGGER.debug(
                "Komari %s refresh still failing (%s, %s)",
                branch,
                category,
                error_type,
            )
        elif branch == BRANCH_INVENTORY:
            _LOGGER.warning(
                "Komari inventory refresh failed (%s, %s); keeping prior inventory",
                category,
                error_type,
            )
        else:
            _LOGGER.warning(
                "Komari %s refresh failed (%s, %s)",
                branch,
                category,
                error_type,
            )
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


def _retain_until_confirmed_missing[T](
    prior: Mapping[str, T],
    observed: Mapping[str, T],
    missing_counts: dict[str, int],
    tracked_ids: set[str],
) -> tuple[Mapping[str, T], frozenset[str]]:
    """Retain absent authoritative items until three successful list misses."""
    retained = dict(observed)
    for identifier in observed:
        tracked_ids.add(identifier)
        missing_counts.pop(identifier, None)
    removed: set[str] = set()
    for identifier in tuple(tracked_ids):
        if identifier in observed:
            continue
        misses = missing_counts.get(identifier, 0) + 1
        if misses >= AUTHORITATIVE_MISSING_THRESHOLD:
            missing_counts.pop(identifier, None)
            tracked_ids.remove(identifier)
            removed.add(identifier)
            continue
        missing_counts[identifier] = misses
        if identifier in prior:
            retained[identifier] = prior[identifier]
    return MappingProxyType(retained), frozenset(removed)


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
