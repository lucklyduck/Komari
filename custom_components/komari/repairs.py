"""Automatically managed Repair issues for Komari."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    DYNAMIC_METRIC_METHODS,
    MAX_METRIC_ENTITIES,
    MAX_PING_ENTITIES,
)
from .coordinator import BRANCH_METRIC_DEFINITIONS, BRANCH_PING_TASKS
from .options import get_options

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import KomariConfigEntry

ISSUE_SELECTED_PING_TASK_MISSING = "selected_ping_task_missing"
ISSUE_PING_ENTITY_LIMIT_EXCEEDED = "ping_entity_limit_exceeded"
ISSUE_TLS_VERIFICATION_DISABLED = "tls_verification_disabled"
ISSUE_METRIC_CAPABILITY_UNAVAILABLE = "metric_capability_unavailable"
ISSUE_SELECTED_METRIC_MISSING = "selected_metric_missing"
ISSUE_METRIC_CONFIGURATION_INVALID = "metric_configuration_invalid"
ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED = "metric_entity_limit_exceeded"
ISSUE_TYPES = (
    ISSUE_SELECTED_PING_TASK_MISSING,
    ISSUE_PING_ENTITY_LIMIT_EXCEEDED,
    ISSUE_TLS_VERIFICATION_DISABLED,
    ISSUE_METRIC_CAPABILITY_UNAVAILABLE,
    ISSUE_SELECTED_METRIC_MISSING,
    ISSUE_METRIC_CONFIGURATION_INVALID,
    ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED,
)


def issue_id(entry_id: str, issue_type: str) -> str:
    """Return a stable issue ID scoped to one config entry."""
    return f"{issue_type}_{entry_id}"


def update_repairs(hass: HomeAssistant, entry: KomariConfigEntry) -> None:
    """Create or clear every condition-driven issue for one panel."""
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data
    options = get_options(entry)

    selected_tasks = frozenset(options.ping_task_ids)
    task_branch = coordinator.branch_states[BRANCH_PING_TASKS]
    if not selected_tasks:
        _delete_issue(hass, entry.entry_id, ISSUE_SELECTED_PING_TASK_MISSING)
    elif task_branch.fetched_at is not None and task_branch.error_category is None:
        missing_tasks = selected_tasks - snapshot.ping_tasks.keys()
        _sync_issue(
            hass,
            entry,
            ISSUE_SELECTED_PING_TASK_MISSING,
            active=bool(missing_tasks),
            placeholders={"missing_count": str(len(missing_tasks))},
        )

    url = urlsplit(entry.data[CONF_URL])
    _sync_issue(
        hass,
        entry,
        ISSUE_TLS_VERIFICATION_DISABLED,
        active=(url.scheme == "https" and entry.data.get(CONF_VERIFY_SSL) is False),
        placeholders={},
    )

    _update_metric_repairs(hass, entry)


def update_ping_entity_limit_repair(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    *,
    entity_count: int,
) -> None:
    """Report retained and observed Ping identities counted by the platform."""
    _sync_issue(
        hass,
        entry,
        ISSUE_PING_ENTITY_LIMIT_EXCEEDED,
        active=entity_count > MAX_PING_ENTITIES,
        placeholders={
            "entity_count": str(entity_count),
            "entity_limit": str(MAX_PING_ENTITIES),
        },
    )


def update_metric_entity_limit_repair(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    *,
    entity_count: int,
) -> None:
    """Synchronize the dynamic-Metric entity limit after platform discovery.

    The sensor platform owns the candidate calculation because it is the sole
    place that knows which series can become entities. Keeping this narrowly
    scoped helper here makes the Repair entry-scoped and lets it be cleared on
    a later coordinator update when the candidate set shrinks.
    """
    _sync_issue(
        hass,
        entry,
        ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED,
        active=entity_count > MAX_METRIC_ENTITIES,
        placeholders={
            "entity_count": str(entity_count),
            "entity_limit": str(MAX_METRIC_ENTITIES),
        },
    )


def _update_metric_repairs(hass: HomeAssistant, entry: KomariConfigEntry) -> None:
    """Synchronize safe metric configuration and capability warnings."""
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data
    options = get_options(entry)
    selected_metrics = frozenset(options.metric_keys)

    if not selected_metrics:
        for issue_type in (
            ISSUE_METRIC_CAPABILITY_UNAVAILABLE,
            ISSUE_SELECTED_METRIC_MISSING,
            ISSUE_METRIC_CONFIGURATION_INVALID,
            ISSUE_METRIC_ENTITY_LIMIT_EXCEEDED,
        ):
            _delete_issue(hass, entry.entry_id, issue_type)
        return

    capabilities_available = snapshot.capabilities >= DYNAMIC_METRIC_METHODS
    _sync_issue(
        hass,
        entry,
        ISSUE_METRIC_CAPABILITY_UNAVAILABLE,
        active=not capabilities_available,
        placeholders={},
    )

    definition_branch = coordinator.branch_states.get(BRANCH_METRIC_DEFINITIONS)
    removed_metrics = selected_metrics & coordinator.confirmed_removed_metric_keys
    _sync_issue(
        hass,
        entry,
        ISSUE_SELECTED_METRIC_MISSING,
        active=(
            definition_branch is not None
            and definition_branch.fetched_at is not None
            and definition_branch.error_category is None
            and bool(removed_metrics)
        ),
        placeholders={"missing_count": str(len(removed_metrics))},
    )

    invalid_count = len(selected_metrics & options.invalid_metric_filter_keys)
    _sync_issue(
        hass,
        entry,
        ISSUE_METRIC_CONFIGURATION_INVALID,
        active=options.metric_configuration_invalid,
        placeholders={"invalid_count": str(max(1, invalid_count))},
    )


def clear_repairs(hass: HomeAssistant, entry_id: str) -> None:
    """Delete all issues owned by one removed config entry."""
    for issue_type in ISSUE_TYPES:
        _delete_issue(hass, entry_id, issue_type)


def _sync_issue(
    hass: HomeAssistant,
    entry: KomariConfigEntry,
    issue_type: str,
    *,
    active: bool,
    placeholders: dict[str, str],
) -> None:
    """Create or delete one translated warning issue."""
    if not active:
        _delete_issue(hass, entry.entry_id, issue_type)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry.entry_id, issue_type),
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key=issue_type,
        translation_placeholders={"entry_title": entry.title, **placeholders},
    )


def _delete_issue(hass: HomeAssistant, entry_id: str, issue_type: str) -> None:
    """Delete one entry-scoped issue if it exists."""
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry_id, issue_type))
