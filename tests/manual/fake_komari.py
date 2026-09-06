"""Run three sanitized local Komari RPC2 panels for manual HA acceptance."""

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
ADDED_TASK_ID = "909"
FAKE_API_KEYS = ("fake-api-key-one", "fake-api-key-two")

RPC_METHODS = (
    "rpc.ping",
    "rpc.version",
    "rpc.methods",
    "public:getPublicSettings",
    "public:getVersion",
    "public:getNodesInformation",
    "common:getNodesLatestStatus",
    "public:getPublicPingTasks",
    "public:getPingMetricStats",
    "public:getPingRecords",
    "common:getRecords",
    "public:listMetricDefinitions",
    "public:queryMetrics",
)
PING_METHODS = frozenset(
    {
        "public:getPublicPingTasks",
        "public:getPingMetricStats",
        "public:getPingRecords",
        "common:getRecords",
    }
)
METRIC_METHODS = frozenset(
    {
        "public:listMetricDefinitions",
        "public:queryMetrics",
    }
)
METRIC_DEFINITIONS = (
    {
        "name": "custom.rate",
        "description": {"en": "Fixture rate", "zh-Hans": "固定测试速率"},
        "type": "gauge",
        "unit": "bytes/s",
        "retention_days": 7,
        "metadata": {"source": "fake"},
    },
    {
        "name": "custom.counter",
        "description": "Fixture counter",
        "type": "counter",
        "unit": "count",
        "retention_days": 30,
        "metadata": {},
    },
    {
        "name": "custom.empty",
        "description": "Fixture empty value",
        "type": "gauge",
        "retention_days": 7,
        "metadata": {},
    },
)
METRIC_HIGH_CARDINALITY_SERIES = 129


def _node(
    node_uuid: str,
    name: str,
    *,
    optional_hardware: bool = False,
    traffic_limit_type: str = "sum",
) -> dict[str, Any]:
    """Return sanitized node metadata."""
    node = {
        "uuid": node_uuid,
        "name": name,
        "cpu_name": "Fake CPU",
        "arch": "amd64",
        "os": "Fake Linux",
        "version": "1.0.0",
        "mem_total": 8_589_934_592,
        "swap_total": 2_147_483_648,
        "disk_total": 107_374_182_400,
        "expired_at": "2027-01-01T00:00:00Z",
        "traffic_limit": 1_000_000_000,
        "traffic_limit_type": traffic_limit_type,
        "billing_cycle": 30,
        "auto_renewal": False,
    }
    if optional_hardware:
        node["gpu_name"] = "Fake GPU"
    return node


def _status(
    node_uuid: str,
    *,
    online: bool = True,
    optional_hardware: bool = False,
) -> dict[str, Any]:
    """Return sanitized latest-status data."""
    status = {
        "client": node_uuid,
        "online": online,
        "time": "2026-08-26T00:00:00Z",
        "cpu": 12.5,
        "ram": 4_294_967_296,
        "ram_total": 8_589_934_592,
        "swap": 0,
        "swap_total": 2_147_483_648,
        "disk": 53_687_091_200,
        "disk_total": 107_374_182_400,
        "load": 0.25,
        "load5": 0.2,
        "load15": 0.1,
        "net_in": 1024,
        "net_out": 2048,
        "net_total_up": 123_456_789,
        "net_total_down": 987_654_321,
        "process": 123,
        "connections": 10,
        "connections_udp": 2,
    }
    if optional_hardware:
        status.update({"gpu": 25, "temp": 42.5})
    return status


@dataclass(slots=True)
class FakeKomariPanel:
    """Mutable state for one local fake Komari panel."""

    name: str
    node_list_as_mapping: bool
    private_site: bool = False
    task_kind: str = "http"
    task_count: int = 2
    optional_hardware: bool = False
    failing: bool = False
    ping_failing: bool = False
    metric_failing: bool = False
    metric_malformed: bool = False
    metric_high_cardinality: bool = False
    delay_seconds: float = 0
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    statuses: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    method_not_found: set[str] = field(default_factory=set)
    api_key_generation: int = 0

    def __post_init__(self) -> None:
        """Create the shared-UUID node used for multi-panel isolation checks."""
        limit_type = "sum" if self.node_list_as_mapping else "max"
        self.nodes[SHARED_NODE_UUID] = _node(
            SHARED_NODE_UUID,
            f"{self.name} shared node",
            optional_hardware=self.optional_hardware,
            traffic_limit_type=limit_type,
        )
        self.statuses[SHARED_NODE_UUID] = _status(
            SHARED_NODE_UUID,
            optional_hardware=self.optional_hardware,
        )
        for index in range(1, self.task_count + 1):
            task_id = str(index * 101)
            self.tasks[task_id] = self._task(
                task_id,
                f"{self.name} task {index}",
            )

    @property
    def api_key(self) -> str:
        """Return the current deterministic local-only API key."""
        return FAKE_API_KEYS[self.api_key_generation]

    def is_authorized(self, authorization: str | None) -> bool:
        """Validate a fake private-panel Authorization header."""
        return not self.private_site or authorization == f"Bearer {self.api_key}"

    def rpc_result(self, method: str, params: object | None = None) -> object:
        """Return one supported RPC result or raise KeyError."""
        if method == "rpc.ping":
            return "pong"
        if method == "rpc.version":
            return "2.0"
        if method == "rpc.methods":
            return list(RPC_METHODS)
        if method == "public:getPublicSettings":
            return {"sitename": self.name, "private_site": self.private_site}
        if method == "public:getVersion":
            return {"version": "1.2.3", "hash": "sanitized-build"}
        if method == "public:getNodesInformation":
            if self.node_list_as_mapping:
                return self.nodes
            return list(self.nodes.values())
        if method == "common:getNodesLatestStatus":
            return self.statuses
        if method == "public:getPublicPingTasks":
            return list(self.tasks.values())
        if method == "public:getPingMetricStats":
            return {"stats": self._ping_stats(params)}
        if method in {"public:getPingRecords", "common:getRecords"}:
            return {"records": self._ping_records()}
        if method == "public:listMetricDefinitions":
            return [dict(definition) for definition in METRIC_DEFINITIONS]
        if method == "public:queryMetrics":
            if self.metric_malformed:
                return {"series": "malformed"}
            return {"series": self._metric_query(params)}
        raise KeyError(method)

    def add_node(self) -> None:
        """Add one deterministic node for dynamic-discovery acceptance."""
        limit_type = "sum" if self.node_list_as_mapping else "max"
        self.nodes[ADDED_NODE_UUID] = _node(
            ADDED_NODE_UUID,
            f"{self.name} added node",
            traffic_limit_type=limit_type,
        )
        self.statuses[ADDED_NODE_UUID] = _status(ADDED_NODE_UUID)
        for task in self.tasks.values():
            task["clients"] = list(self.nodes)

    def delete_added_node(self) -> None:
        """Delete the deterministic dynamic node."""
        self.nodes.pop(ADDED_NODE_UUID, None)
        self.statuses.pop(ADDED_NODE_UUID, None)
        for task in self.tasks.values():
            task["clients"] = list(self.nodes)

    def rename_shared_node(self) -> None:
        """Rename the shared node without changing its UUID."""
        self.nodes[SHARED_NODE_UUID]["name"] = f"{self.name} renamed node"

    def add_task(self) -> None:
        """Add one deterministic Ping task."""
        self.tasks[ADDED_TASK_ID] = self._task(
            ADDED_TASK_ID,
            f"{self.name} added task",
        )

    def remove_added_task(self) -> None:
        """Remove the deterministic Ping task."""
        self.tasks.pop(ADDED_TASK_ID, None)

    def rename_first_task(self) -> None:
        """Rename a task without changing its stable ID."""
        first_task = next(iter(self.tasks.values()))
        first_task["name"] = f"{self.name} renamed task"

    def rotate_key(self) -> None:
        """Rotate between two deterministic fake API keys."""
        self.api_key_generation = 1 - self.api_key_generation

    def _task(self, task_id: str, name: str) -> dict[str, Any]:
        """Return one sanitized task payload."""
        return {
            "id": task_id,
            "name": name,
            "type": self.task_kind,
            "interval": 60,
            "default_on": True,
            "clients": list(self.nodes),
        }

    def _ping_stats(self, params: object | None) -> list[dict[str, Any]]:
        """Return deterministic statistics, respecting batched filters."""
        entity_ids = set(self.nodes)
        task_ids = set(self.tasks)
        if isinstance(params, dict):
            if isinstance(params.get("entity_ids"), list):
                entity_ids &= {str(item) for item in params["entity_ids"]}
            if isinstance(params.get("task_ids"), list):
                task_ids &= {str(item) for item in params["task_ids"]}
        stats: list[dict[str, Any]] = []
        for node_index, node_uuid in enumerate(sorted(entity_ids), start=1):
            for task_index, task_id in enumerate(sorted(task_ids), start=1):
                latency = float(10 + node_index + task_index)
                item: dict[str, Any] = {
                    "entity_id": node_uuid,
                    "task_id": task_id,
                    "latest": latency,
                    "loss": 0,
                    "max": latency + 2,
                    "avg": latency + 1,
                    "p50": latency + 1,
                    "p99": latency + 2,
                    "stddev": 0.5,
                    "total": 10,
                    "valid": 10,
                }
                if not (node_index == 1 and task_index == 1):
                    item["min"] = latency
                stats.append(item)
        return stats

    def _ping_records(self) -> list[dict[str, Any]]:
        """Return one batch records fallback for every visible series."""
        return [
            {
                "client": node_uuid,
                "task_id": task_id,
                "time": "2026-08-26T00:00:00Z",
                "value": 12,
            }
            for node_uuid in self.nodes
            for task_id in self.tasks
        ]

    def _metric_query(self, params: object | None) -> list[dict[str, Any]]:
        """Return bounded deterministic Metric series for the requested filters."""
        metric_keys = {str(definition["name"]) for definition in METRIC_DEFINITIONS}
        entity_ids = set(self.nodes)
        requested_tags: dict[str, str] = {}
        if isinstance(params, dict):
            if isinstance(params.get("metric_keys"), list):
                metric_keys &= {
                    metric_key
                    for item in params["metric_keys"]
                    if isinstance(item, str)
                    for metric_key in (item,)
                }
            if isinstance(params.get("entity_ids"), list):
                entity_ids &= {
                    entity_id
                    for item in params["entity_ids"]
                    if isinstance(item, str)
                    for entity_id in (item,)
                }
            if isinstance(params.get("tags"), dict):
                requested_tags = {
                    key: value
                    for key, value in params["tags"].items()
                    if isinstance(key, str) and isinstance(value, str)
                }

        series: list[dict[str, Any]] = []
        for entity_id in sorted(entity_ids):
            for metric_key in sorted(metric_keys):
                if self.metric_high_cardinality and metric_key == "custom.rate":
                    series.extend(
                        self._high_cardinality_metric_series(entity_id, requested_tags)
                    )
                    continue
                candidate = self._metric_series(metric_key, entity_id)
                if requested_tags.items() <= candidate["tags"].items():
                    series.append(candidate)
        return series

    @staticmethod
    def _metric_series(metric_key: str, entity_id: str) -> dict[str, Any]:
        """Build one sanitized Metric series with a single current point."""
        values: dict[str, float | int | None] = {
            "custom.rate": 12.5,
            "custom.counter": 42,
            "custom.empty": None,
        }
        tags = (
            {"region": "fixture", "scope": "node"}
            if metric_key == "custom.rate"
            else {}
        )
        result: dict[str, Any] = {
            "metric_key": metric_key,
            "entity_id": entity_id,
            "tags": tags,
            "points": [{"time": "2026-08-26T00:00:00Z", "value": values[metric_key]}],
        }
        if metric_key == "custom.rate":
            result["labels"] = {"display_name": "ignored by identity"}
        return result

    @staticmethod
    def _high_cardinality_metric_series(
        entity_id: str,
        requested_tags: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return 129 distinct tag identities for local entity-limit acceptance."""
        return [
            {
                "metric_key": "custom.rate",
                "entity_id": entity_id,
                "tags": {"scope": "node", "series": str(index)},
                "points": [{"time": "2026-08-26T00:00:00Z", "value": index}],
            }
            for index in range(METRIC_HIGH_CARDINALITY_SERIES)
            if requested_tags.items() <= {"scope": "node", "series": str(index)}.items()
        ]


def create_app(panel: FakeKomariPanel) -> web.Application:
    """Create one local-only fake panel application."""
    app = web.Application()

    async def rpc(request: web.Request) -> web.Response:
        """Serve one JSON-RPC2 request."""
        if not panel.is_authorized(request.headers.get("Authorization")):
            raise web.HTTPUnauthorized(text="invalid fake credentials")
        if panel.failing:
            raise web.HTTPServiceUnavailable(text="simulated panel outage")
        if panel.delay_seconds:
            await asyncio.sleep(panel.delay_seconds)
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
        if method in panel.method_not_found:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
        if panel.ping_failing and method in PING_METHODS:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "Simulated Ping failure"},
                }
            )
        if panel.metric_failing and method in METRIC_METHODS:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "Simulated Metric failure"},
                }
            )
        try:
            result = panel.rpc_result(method, payload.get("params"))
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

    async def delete_node(request: web.Request) -> web.Response:
        """Delete the deterministic dynamic node."""
        panel.delete_added_node()
        return web.json_response({"node_count": len(panel.nodes)})

    async def rename_node(request: web.Request) -> web.Response:
        """Rename the shared node while preserving identity."""
        panel.rename_shared_node()
        return web.json_response({"renamed": True})

    async def add_task(request: web.Request) -> web.Response:
        """Add the deterministic Ping task."""
        panel.add_task()
        return web.json_response({"task_count": len(panel.tasks)})

    async def remove_task(request: web.Request) -> web.Response:
        """Remove the deterministic Ping task."""
        panel.remove_added_task()
        return web.json_response({"task_count": len(panel.tasks)})

    async def rename_task(request: web.Request) -> web.Response:
        """Rename the first Ping task while preserving identity."""
        panel.rename_first_task()
        return web.json_response({"renamed": True})

    async def fail_ping(request: web.Request) -> web.Response:
        """Fail only Ping RPC methods."""
        panel.ping_failing = True
        return web.json_response({"ping_failing": True})

    async def recover_ping(request: web.Request) -> web.Response:
        """Recover only Ping RPC methods."""
        panel.ping_failing = False
        return web.json_response({"ping_failing": False})

    async def fail_metrics(request: web.Request) -> web.Response:
        """Fail only the optional Metric RPC methods."""
        panel.metric_failing = True
        return web.json_response({"metric_failing": True})

    async def recover_metrics(request: web.Request) -> web.Response:
        """Recover only the optional Metric RPC methods."""
        panel.metric_failing = False
        return web.json_response({"metric_failing": False})

    async def method_not_found(request: web.Request) -> web.Response:
        """Make the preferred Ping stats method return Method Not Found."""
        panel.method_not_found.add("public:getPingMetricStats")
        return web.json_response({"method_not_found": True})

    async def restore_method(request: web.Request) -> web.Response:
        """Restore the preferred Ping stats method."""
        panel.method_not_found.discard("public:getPingMetricStats")
        return web.json_response({"method_not_found": False})

    async def metric_method_not_found(request: web.Request) -> web.Response:
        """Make the dynamic Metric query RPC report Method Not Found."""
        panel.method_not_found.add("public:queryMetrics")
        return web.json_response({"metric_method_not_found": True})

    async def restore_metric_method(request: web.Request) -> web.Response:
        """Restore the dynamic Metric query RPC."""
        panel.method_not_found.discard("public:queryMetrics")
        return web.json_response({"metric_method_not_found": False})

    async def malformed_metrics(request: web.Request) -> web.Response:
        """Return a deliberately malformed Metric query envelope."""
        panel.metric_malformed = True
        return web.json_response({"metric_malformed": True})

    async def restore_metrics(request: web.Request) -> web.Response:
        """Restore valid Metric query envelopes."""
        panel.metric_malformed = False
        return web.json_response({"metric_malformed": False})

    async def high_cardinality_metrics(request: web.Request) -> web.Response:
        """Return more dynamic series than Home Assistant may register."""
        panel.metric_high_cardinality = True
        return web.json_response({"metric_high_cardinality": True})

    async def restore_metric_cardinality(request: web.Request) -> web.Response:
        """Return the normal bounded Metric series set."""
        panel.metric_high_cardinality = False
        return web.json_response({"metric_high_cardinality": False})

    async def add_delay(request: web.Request) -> web.Response:
        """Add a short deterministic response delay."""
        panel.delay_seconds = 0.05
        return web.json_response({"delayed": True})

    async def clear_delay(request: web.Request) -> web.Response:
        """Clear the deterministic response delay."""
        panel.delay_seconds = 0
        return web.json_response({"delayed": False})

    async def rotate_key(request: web.Request) -> web.Response:
        """Rotate the private fake panel key without returning it."""
        panel.rotate_key()
        return web.json_response({"key_generation": panel.api_key_generation})

    app.router.add_post("/api/rpc2", rpc)
    app.router.add_post("/_control/fail", fail)
    app.router.add_post("/_control/recover", recover)
    app.router.add_post("/_control/add-node", add_node)
    app.router.add_post("/_control/delete-node", delete_node)
    app.router.add_post("/_control/rename-node", rename_node)
    app.router.add_post("/_control/add-task", add_task)
    app.router.add_post("/_control/remove-task", remove_task)
    app.router.add_post("/_control/rename-task", rename_task)
    app.router.add_post("/_control/fail-ping", fail_ping)
    app.router.add_post("/_control/recover-ping", recover_ping)
    app.router.add_post("/_control/fail-metrics", fail_metrics)
    app.router.add_post("/_control/recover-metrics", recover_metrics)
    app.router.add_post("/_control/method-not-found", method_not_found)
    app.router.add_post("/_control/restore-method", restore_method)
    app.router.add_post("/_control/metric-method-not-found", metric_method_not_found)
    app.router.add_post("/_control/restore-metric-method", restore_metric_method)
    app.router.add_post("/_control/malformed-metrics", malformed_metrics)
    app.router.add_post("/_control/restore-metrics", restore_metrics)
    app.router.add_post("/_control/high-cardinality-metrics", high_cardinality_metrics)
    app.router.add_post(
        "/_control/restore-metric-cardinality", restore_metric_cardinality
    )
    app.router.add_post("/_control/delay", add_delay)
    app.router.add_post("/_control/clear-delay", clear_delay)
    app.router.add_post("/_control/rotate-key", rotate_key)
    return app


async def _serve(bind: str, ports: Sequence[int]) -> None:
    """Run all fake panels until interrupted."""
    panels = (
        FakeKomariPanel(
            "Fake Komari A",
            node_list_as_mapping=True,
            task_kind="http",
            task_count=2,
            optional_hardware=True,
        ),
        FakeKomariPanel(
            "Fake Komari B",
            node_list_as_mapping=False,
            task_kind="tcp",
            task_count=3,
        ),
        FakeKomariPanel(
            "Fake Komari C",
            node_list_as_mapping=True,
            private_site=True,
            task_kind="tcp",
            task_count=1,
        ),
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
    """Parse CLI arguments and run all local fake panels."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port-a", type=int, default=18881)
    parser.add_argument("--port-b", type=int, default=18882)
    parser.add_argument("--port-c", type=int, default=18883)
    args = parser.parse_args()
    asyncio.run(_serve(args.bind, (args.port_a, args.port_b, args.port_c)))


if __name__ == "__main__":
    main()
