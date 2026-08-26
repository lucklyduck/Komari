"""Run two sanitized local Komari RPC2 panels for manual HA acceptance."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOGGER = logging.getLogger(__name__)

SHARED_NODE_UUID = "00000000-0000-0000-0000-000000000001"
ADDED_NODE_UUID = "00000000-0000-0000-0000-000000000099"

RPC_METHODS = (
    "rpc.ping",
    "rpc.version",
    "rpc.methods",
    "public:getPublicSettings",
    "public:getVersion",
    "public:getNodesInformation",
    "common:getNodesLatestStatus",
)


def _node(node_uuid: str, name: str) -> dict[str, Any]:
    """Return sanitized node metadata."""
    return {
        "uuid": node_uuid,
        "name": name,
        "cpu_name": "Fake CPU",
        "gpu_name": "Fake GPU",
        "arch": "amd64",
        "os": "Fake Linux",
        "version": "1.0.0",
        "mem_total": 8_589_934_592,
        "swap_total": 2_147_483_648,
        "disk_total": 107_374_182_400,
    }


def _status(node_uuid: str, *, online: bool = True) -> dict[str, Any]:
    """Return sanitized latest-status data."""
    return {
        "client": node_uuid,
        "online": online,
        "time": "2026-08-26T00:00:00Z",
        "cpu": 12.5,
        "gpu": 0,
        "ram": 4_294_967_296,
        "ram_total": 8_589_934_592,
        "swap": 0,
        "swap_total": 2_147_483_648,
        "disk": 53_687_091_200,
        "disk_total": 107_374_182_400,
        "load": 0.25,
        "load5": 0.2,
        "load15": 0.1,
        "temp": 42.5,
        "net_in": 1024,
        "net_out": 2048,
        "net_total_up": 123_456_789,
        "net_total_down": 987_654_321,
        "process": 123,
        "connections": 10,
        "connections_udp": 2,
    }


@dataclass(slots=True)
class FakeKomariPanel:
    """Mutable state for one local fake Komari panel."""

    name: str
    node_list_as_mapping: bool
    failing: bool = False
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    statuses: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Create the shared-UUID node used for multi-panel isolation checks."""
        self.nodes[SHARED_NODE_UUID] = _node(
            SHARED_NODE_UUID, f"{self.name} shared node"
        )
        self.statuses[SHARED_NODE_UUID] = _status(SHARED_NODE_UUID)

    def rpc_result(self, method: str) -> object:
        """Return one supported RPC result or raise KeyError."""
        if method == "rpc.ping":
            return "pong"
        if method == "rpc.version":
            return "2.0"
        if method == "rpc.methods":
            return list(RPC_METHODS)
        if method == "public:getPublicSettings":
            return {"sitename": self.name, "private_site": False}
        if method == "public:getVersion":
            return {"version": "1.2.3", "hash": "sanitized-build"}
        if method == "public:getNodesInformation":
            if self.node_list_as_mapping:
                return self.nodes
            return list(self.nodes.values())
        if method == "common:getNodesLatestStatus":
            return self.statuses
        raise KeyError(method)

    def add_node(self) -> None:
        """Add one deterministic node for dynamic-discovery acceptance."""
        self.nodes[ADDED_NODE_UUID] = _node(ADDED_NODE_UUID, f"{self.name} added node")
        self.statuses[ADDED_NODE_UUID] = _status(ADDED_NODE_UUID)


def create_app(panel: FakeKomariPanel) -> web.Application:
    """Create one local-only fake panel application."""
    app = web.Application()

    async def rpc(request: web.Request) -> web.Response:
        """Serve one JSON-RPC2 request."""
        if panel.failing:
            raise web.HTTPServiceUnavailable(text="simulated panel outage")
        try:
            payload = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="invalid JSON") from None
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="request must be an object")
        request_id = payload.get("id")
        method = payload.get("method")
        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
            raise web.HTTPBadRequest(text="invalid JSON-RPC2 request")
        try:
            result = panel.rpc_result(method)
        except KeyError:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
        return web.json_response({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def fail(request: web.Request) -> web.Response:
        """Enable simulated RPC failure for this panel."""
        panel.failing = True
        return web.json_response({"failing": panel.failing})

    async def recover(request: web.Request) -> web.Response:
        """Recover this panel from simulated failure."""
        panel.failing = False
        return web.json_response({"failing": panel.failing})

    async def add_node(request: web.Request) -> web.Response:
        """Add one node for dynamic-discovery acceptance."""
        panel.add_node()
        return web.json_response({"node_count": len(panel.nodes)})

    app.router.add_post("/api/rpc2", rpc)
    app.router.add_post("/_control/fail", fail)
    app.router.add_post("/_control/recover", recover)
    app.router.add_post("/_control/add-node", add_node)
    return app


async def _serve(bind: str, ports: Sequence[int]) -> None:
    """Run both fake panels until interrupted."""
    panels = (
        FakeKomariPanel("Fake Komari A", node_list_as_mapping=True),
        FakeKomariPanel("Fake Komari B", node_list_as_mapping=False),
    )
    runners: list[web.AppRunner] = []
    for panel, port in zip(panels, ports, strict=True):
        runner = web.AppRunner(create_app(panel), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, bind, port).start()
        runners.append(runner)
        _LOGGER.info("%s: http://%s:%d", panel.name, bind, port)

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stopped.set)
    try:
        await stopped.wait()
    finally:
        await asyncio.gather(*(runner.cleanup() for runner in runners))


def main() -> None:
    """Parse CLI arguments and run both local fake panels."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port-a", type=int, default=18881)
    parser.add_argument("--port-b", type=int, default=18882)
    args = parser.parse_args()
    asyncio.run(_serve(args.bind, (args.port_a, args.port_b)))


if __name__ == "__main__":
    main()
