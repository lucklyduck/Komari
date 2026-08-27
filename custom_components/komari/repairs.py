"""Automatically managed Repair issues for Komari."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, MAX_PING_ENTITIES
from .coordinator import BRANCH_PING_TASKS
from .options import get_options

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import KomariConfigEntry

ISSUE_SELECTED_PING_TASK_MISSING = "selected_ping_task_missing"
ISSUE_PING_ENTITY_LIMIT_EXCEEDED = "ping_entity_limit_exceeded"
ISSUE_TLS_VERIFICATION_DISABLED = "tls_verification_disabled"
ISSUE_TYPES = (
    ISSUE_SELECTED_PING_TASK_MISSING,
    ISSUE_PING_ENTITY_LIMIT_EXCEEDED,
    ISSUE_TLS_VERIFICATION_DISABLED,
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

    potential_ping_entities = sum(
        key.node_uuid in snapshot.nodes
        and key.task_id in selected_tasks
        and key.task_id in snapshot.ping_tasks
        for key in snapshot.ping_stats
    ) * len(options.ping_stat_keys)
    _sync_issue(
        hass,
        entry,
        ISSUE_PING_ENTITY_LIMIT_EXCEEDED,
        active=potential_ping_entities > MAX_PING_ENTITIES,
        placeholders={
            "entity_count": str(potential_ping_entities),
            "entity_limit": str(MAX_PING_ENTITIES),
        },
    )

    url = urlsplit(entry.data[CONF_URL])
    _sync_issue(
        hass,
        entry,
        ISSUE_TLS_VERIFICATION_DISABLED,
        active=(url.scheme == "https" and entry.data.get(CONF_VERIFY_SSL) is False),
        placeholders={},
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
