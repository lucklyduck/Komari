"""Pure identity-set calculations for per-entry Ping entity budgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from .models import KomariPingSeriesKey, KomariPingTask

type PingEntityKey = tuple[str, str, str]


def retained_ping_entity_keys(
    identities: Iterable[PingEntityKey],
    *,
    task_ids: Collection[str],
    stat_keys: Collection[str],
    removed_nodes: Collection[str] = (),
    removed_tasks: Collection[str] = (),
) -> frozenset[PingEntityKey]:
    """Keep exactly the selected identities not authoritatively removed.

    Absence from the latest stats, assignment list or visible inventory does
    not release an existing registration. Disabled entities also occupy slots.
    """
    return frozenset(
        (node_uuid, task_id, stat_key)
        for node_uuid, task_id, stat_key in identities
        if task_id in task_ids
        and stat_key in stat_keys
        and node_uuid not in removed_nodes
        and task_id not in removed_tasks
    )


def observed_ping_entity_keys(
    series: Iterable[KomariPingSeriesKey],
    *,
    node_ids: Collection[str],
    task_ids: Collection[str],
    stat_keys: Collection[str],
) -> frozenset[PingEntityKey]:
    """Expand observed visible series into selected fields, including nulls."""
    return frozenset(
        (key.node_uuid, key.task_id, stat_key)
        for key in series
        if key.node_uuid in node_ids and key.task_id in task_ids
        for stat_key in stat_keys
    )


def predicted_ping_entity_keys(
    tasks: Mapping[str, KomariPingTask],
    *,
    node_ids: Collection[str],
    task_ids: Collection[str],
    stat_keys: Collection[str],
    assignments_current: bool,
    missing_tasks: Collection[str] = (),
) -> frozenset[PingEntityKey]:
    """Forecast assignments, conservatively retaining unknown task coverage."""
    visible_nodes = frozenset(node_ids)
    identities: set[PingEntityKey] = set()
    for task_id in task_ids:
        task = tasks.get(task_id)
        clients = (
            task.clients & visible_nodes
            if task is not None
            and task.clients is not None
            and assignments_current
            and task_id not in missing_tasks
            else visible_nodes
        )
        identities.update(
            (node_uuid, task_id, stat_key)
            for node_uuid in clients
            for stat_key in stat_keys
        )
    return frozenset(identities)
