"""Run sanitized live RPC2 smoke checks without persisting panel data."""

from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import ClientSession
from custom_components.komari.api import KomariApiClient
from custom_components.komari.exceptions import KomariConnectionError

_LOGGER = logging.getLogger(__name__)


async def _bootstrap(url: str, session: ClientSession) -> KomariApiClient:
    """Validate one anonymous panel and discard its returned snapshot."""
    client = KomariApiClient(session, url)
    await client.async_get_bootstrap()
    return client


async def _smoke_public(url: str) -> None:
    """Run one anonymous read-only bootstrap without logging panel data."""
    async with ClientSession() as session:
        client = await _bootstrap(url, session)
        assert client.authenticated is False
    _LOGGER.info(
        "Anonymous RPC2 bootstrap passed; panel identity, nodes, and metrics discarded"
    )


async def _smoke_fake(panel_a: str, panel_b: str) -> None:
    """Validate dual-panel isolation, local outage, recovery, and discovery."""
    async with ClientSession() as session:
        client_a = KomariApiClient(session, panel_a)
        client_b = KomariApiClient(session, panel_b)
        bootstrap_a, bootstrap_b = await asyncio.gather(
            client_a.async_get_bootstrap(),
            client_b.async_get_bootstrap(),
        )
        assert set(bootstrap_a.nodes) & set(bootstrap_b.nodes)

        async with session.post(f"{panel_b}/_control/fail") as response:
            response.raise_for_status()
        await client_a.async_get_latest_status()
        try:
            await client_b.async_get_latest_status()
        except KomariConnectionError:
            pass
        else:
            raise AssertionError("Panel B did not enter the simulated outage")
        async with session.post(f"{panel_b}/_control/recover") as response:
            response.raise_for_status()
        await client_b.async_get_latest_status()

        async with session.post(f"{panel_a}/_control/add-node") as response:
            response.raise_for_status()
        updated_nodes = await client_a.async_get_nodes(bootstrap_a.capabilities)
        assert len(updated_nodes) > len(bootstrap_a.nodes)

    _LOGGER.info(
        "Dual-panel RPC2 smoke passed: shared UUID isolation, outage, "
        "recovery, discovery"
    )


def main() -> None:
    """Parse arguments and run one selected smoke mode."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--public")
    mode.add_argument("--panel-a")
    parser.add_argument("--panel-b")
    args = parser.parse_args()

    if args.public:
        asyncio.run(_smoke_public(args.public))
        return
    if not args.panel_b:
        parser.error("--panel-b is required with --panel-a")
    asyncio.run(_smoke_fake(args.panel_a, args.panel_b))


if __name__ == "__main__":
    main()
