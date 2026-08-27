"""Run sanitized live RPC2 smoke checks without persisting panel data."""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import suppress

from aiohttp import ClientSession
from custom_components.komari.api import KomariApiClient
from custom_components.komari.const import (
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_PING_TASKS,
    PING_STATS_METHODS,
)
from custom_components.komari.exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariMethodNotFoundError,
    KomariRpcError,
    KomariUnsupportedError,
)

from .fake_komari import FAKE_API_KEYS

_LOGGER = logging.getLogger(__name__)


async def _smoke_public(url: str) -> None:
    """Run one anonymous read-only bootstrap without logging panel data."""
    async with ClientSession() as session:
        client = KomariApiClient(session, url)
        bootstrap = await client.async_get_bootstrap()
        assert client.authenticated is False
        if (
            METHOD_PUBLIC_PING_TASKS in bootstrap.capabilities
            and bootstrap.capabilities & PING_STATS_METHODS
        ):
            tasks = await client.async_get_ping_tasks(bootstrap.capabilities)
            selected_tasks = tuple(tasks)[:8]
            if selected_tasks:
                with suppress(KomariUnsupportedError):
                    await client.async_get_ping_stats(
                        bootstrap.capabilities,
                        tuple(bootstrap.nodes),
                        selected_tasks,
                        hours=1,
                        max_points=120,
                    )
    _LOGGER.info(
        "Anonymous RPC2 bootstrap passed; panel identity, nodes, and metrics discarded"
    )


async def _control(session: ClientSession, panel: str, action: str) -> None:
    """Call one local-only deterministic control endpoint."""
    async with session.post(f"{panel}/_control/{action}") as response:
        response.raise_for_status()


async def _smoke_fake(panel_a: str, panel_b: str, panel_c: str) -> None:
    """Validate three-panel isolation, optional branches, and lifecycle controls."""
    async with ClientSession() as session:
        client_a = KomariApiClient(session, panel_a)
        client_b = KomariApiClient(session, panel_b)
        anonymous_c = KomariApiClient(session, panel_c)
        try:
            await anonymous_c.async_get_bootstrap()
        except KomariAuthenticationError:
            pass
        else:
            raise AssertionError("Private Fake C accepted an anonymous bootstrap")
        client_c = KomariApiClient(session, panel_c, FAKE_API_KEYS[0])
        bootstrap_a, bootstrap_b = await asyncio.gather(
            client_a.async_get_bootstrap(),
            client_b.async_get_bootstrap(),
        )
        bootstrap_c = await client_c.async_get_bootstrap()
        assert set(bootstrap_a.nodes) & set(bootstrap_b.nodes) & set(bootstrap_c.nodes)

        tasks_a, tasks_b = await asyncio.gather(
            client_a.async_get_ping_tasks(bootstrap_a.capabilities),
            client_b.async_get_ping_tasks(bootstrap_b.capabilities),
        )
        await asyncio.gather(
            client_a.async_get_ping_stats(
                bootstrap_a.capabilities,
                tuple(bootstrap_a.nodes),
                tuple(tasks_a),
                hours=1,
                max_points=120,
            ),
            client_b.async_get_ping_stats(
                bootstrap_b.capabilities,
                tuple(bootstrap_b.nodes),
                tuple(tasks_b),
                hours=1,
                max_points=120,
            ),
        )

        await _control(session, panel_b, "fail")
        await client_a.async_get_latest_status()
        try:
            await client_b.async_get_latest_status()
        except KomariConnectionError:
            pass
        else:
            raise AssertionError("Panel B did not enter the simulated outage")
        await _control(session, panel_b, "recover")
        await client_b.async_get_latest_status()

        await _control(session, panel_a, "add-node")
        updated_nodes = await client_a.async_get_nodes(bootstrap_a.capabilities)
        assert len(updated_nodes) > len(bootstrap_a.nodes)
        await _control(session, panel_a, "rename-node")
        renamed_nodes = await client_a.async_get_nodes(bootstrap_a.capabilities)
        assert set(renamed_nodes) == set(updated_nodes)
        await _control(session, panel_a, "delete-node")
        deleted_nodes = await client_a.async_get_nodes(bootstrap_a.capabilities)
        assert set(deleted_nodes) == set(bootstrap_a.nodes)

        await _control(session, panel_a, "add-task")
        added_tasks = await client_a.async_get_ping_tasks(bootstrap_a.capabilities)
        assert len(added_tasks) > len(tasks_a)
        await _control(session, panel_a, "rename-task")
        renamed_tasks = await client_a.async_get_ping_tasks(bootstrap_a.capabilities)
        assert set(renamed_tasks) == set(added_tasks)
        await _control(session, panel_a, "remove-task")
        restored_tasks = await client_a.async_get_ping_tasks(bootstrap_a.capabilities)
        assert set(restored_tasks) == set(tasks_a)

        await _control(session, panel_b, "fail-ping")
        await client_b.async_get_latest_status()
        try:
            await client_b.async_get_ping_tasks(bootstrap_b.capabilities)
        except KomariRpcError:
            pass
        else:
            raise AssertionError("Ping branch failure did not stay isolated")
        await _control(session, panel_b, "recover-ping")
        await client_b.async_get_ping_tasks(bootstrap_b.capabilities)

        await _control(session, panel_b, "method-not-found")
        try:
            await client_b.async_get_ping_stats(
                bootstrap_b.capabilities,
                tuple(bootstrap_b.nodes),
                tuple(tasks_b),
                hours=1,
                max_points=120,
            )
        except KomariMethodNotFoundError:
            pass
        else:
            raise AssertionError(
                "Preferred Ping method did not report Method Not Found"
            )
        await client_b.async_get_ping_stats(
            bootstrap_b.capabilities - {METHOD_PING_METRIC_STATS},
            tuple(bootstrap_b.nodes),
            tuple(tasks_b),
            hours=1,
            max_points=120,
        )
        await _control(session, panel_b, "restore-method")

        await _control(session, panel_a, "delay")
        await client_a.async_get_latest_status()
        await _control(session, panel_a, "clear-delay")

        await _control(session, panel_c, "rotate-key")
        try:
            await client_c.async_get_latest_status()
        except KomariAuthenticationError:
            pass
        else:
            raise AssertionError("Rotated fake key remained valid")
        replacement_c = KomariApiClient(session, panel_c, FAKE_API_KEYS[1])
        await replacement_c.async_get_latest_status()

    _LOGGER.info(
        "Three-panel RPC2 smoke passed: shared identities, lifecycle controls, "
        "Ping isolation/fallback, delay, and key rotation"
    )


def main() -> None:
    """Parse arguments and run one selected smoke mode."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--public")
    mode.add_argument("--panel-a")
    parser.add_argument("--panel-b")
    parser.add_argument("--panel-c")
    args = parser.parse_args()

    if args.public:
        asyncio.run(_smoke_public(args.public))
        return
    if not args.panel_b or not args.panel_c:
        parser.error("--panel-b and --panel-c are required with --panel-a")
    asyncio.run(_smoke_fake(args.panel_a, args.panel_b, args.panel_c))


if __name__ == "__main__":
    main()
