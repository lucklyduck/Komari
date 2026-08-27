"""Tests for the Komari RPC2 client and response models."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

import pytest
from aiohttp import ClientConnectionError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aiohttp import ClientSession

from custom_components.komari.api import KomariApiClient
from custom_components.komari.const import (
    METHOD_COMMON_NODES,
    METHOD_COMMON_PUBLIC_INFO,
    METHOD_COMMON_VERSION,
    METHOD_LATEST_STATUS,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_TASKS,
    METHOD_PUBLIC_SETTINGS,
    METHOD_PUBLIC_VERSION,
)
from custom_components.komari.exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariMethodNotFoundError,
    KomariPermissionError,
    KomariProtocolError,
    KomariRpcError,
    KomariUnsupportedError,
)
from custom_components.komari.models import (
    TrafficLimitType,
    parse_datetime,
    parse_nodes,
    parse_ping_metric_stats,
    parse_ping_tasks,
    parse_statuses,
)

FIXTURES = Path(__file__).parent / "fixtures"
NODE_UUID = "00000000-0000-0000-0000-000000000001"


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> Self:
        """Enter the response context."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit the response context."""

    async def json(self, *, content_type: None = None) -> object:
        """Return the configured JSON payload."""
        del content_type
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Queue-backed session for API unit tests."""

    def __init__(
        self,
        responses: list[FakeResponse | Exception],
        *,
        rpc_results: Mapping[str, object] | None = None,
    ) -> None:
        self.responses = deque(responses)
        self.rpc_results = rpc_results
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a request and return the next response."""
        self.requests.append({"url": url, **kwargs})
        if self.rpc_results is not None:
            request = kwargs["json"]
            return rpc_result(
                request["id"],
                self.rpc_results[request["method"]],
            )
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def rpc_result(request_id: int, result: object) -> FakeResponse:
    """Build one successful JSON-RPC response."""
    return FakeResponse(
        200,
        {"jsonrpc": "2.0", "id": request_id, "result": result},
    )


def load_fixture(name: str) -> object:
    """Load one sanitized Komari fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(session: FakeSession, api_key: str | None = None) -> KomariApiClient:
    """Create a client around a test session."""
    return KomariApiClient(
        cast("ClientSession", session),
        "https://panel.example",
        api_key,
    )


def test_client_properties_normalize_trailing_slash() -> None:
    """Client properties expose normalized, non-secret connection metadata."""
    client = KomariApiClient(
        cast("ClientSession", FakeSession([])),
        "https://panel.example/",
        " secret-key ",
    )
    assert client.base_url == "https://panel.example"
    assert client.endpoint == "https://panel.example/api/rpc2"
    assert client.authenticated is True


@pytest.mark.asyncio
async def test_rpc_call_uses_bearer_auth_without_leaking_key() -> None:
    """The API key is sent only in the Authorization header."""
    session = FakeSession([rpc_result(1, "pong")])
    client = make_client(session, "secret-key")

    await client.async_ping()

    request = session.requests[0]
    assert request["url"] == "https://panel.example/api/rpc2"
    assert request["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in request["url"]
    assert request["json"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "rpc.ping",
    }
    assert request["allow_redirects"] is False


@pytest.mark.asyncio
async def test_anonymous_call_omits_authorization() -> None:
    """Anonymous RPC calls do not send an Authorization header."""
    session = FakeSession([rpc_result(1, "pong")])
    await make_client(session).async_ping()
    assert "Authorization" not in session.requests[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_http_authentication_error(status: int) -> None:
    """Authentication HTTP responses map to a safe auth exception."""
    client = make_client(FakeSession([FakeResponse(status, {})]), "secret-key")
    with pytest.raises(KomariAuthenticationError, match="credentials") as error:
        await client.async_ping()
    assert "secret-key" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 500])
async def test_http_connection_errors(status: int) -> None:
    """Redirects and server failures never expose response bodies."""
    client = make_client(FakeSession([FakeResponse(status, {"secret": "body"})]))
    with pytest.raises(KomariConnectionError) as error:
        await client.async_ping()
    assert "body" not in str(error.value)


@pytest.mark.asyncio
async def test_client_transport_error() -> None:
    """aiohttp failures map to a connection error."""
    client = make_client(FakeSession([ClientConnectionError("private host")]))
    with pytest.raises(KomariConnectionError, match="Unable to contact") as error:
        await client.async_ping()
    assert "private host" not in str(error.value)


@pytest.mark.asyncio
async def test_client_timeout() -> None:
    """Timeouts map to a connection error."""
    client = make_client(FakeSession([TimeoutError()]))
    with pytest.raises(KomariConnectionError, match="Timed out"):
        await client.async_ping()


@pytest.mark.asyncio
async def test_invalid_json_and_response_id() -> None:
    """Invalid JSON and mismatched RPC ids are rejected."""
    invalid_json = make_client(FakeSession([FakeResponse(200, ValueError("raw"))]))
    with pytest.raises(KomariProtocolError, match="invalid JSON"):
        await invalid_json.async_ping()

    wrong_id = make_client(
        FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 99, "result": "pong"})])
    )
    with pytest.raises(KomariProtocolError, match="id"):
        await wrong_id.async_ping()


@pytest.mark.asyncio
async def test_rpc_error_mapping() -> None:
    """Method and permission errors retain only safe RPC metadata."""
    missing = make_client(
        FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32601, "message": "Method not found"},
                    },
                )
            ]
        )
    )
    with pytest.raises(KomariMethodNotFoundError):
        await missing.async_ping()

    denied = make_client(
        FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32000, "message": "Permission denied"},
                    },
                )
            ]
        )
    )
    with pytest.raises(KomariPermissionError):
        await denied.async_ping()

    generic = make_client(
        FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32001, "message": "Temporary failure"},
                    },
                )
            ]
        )
    )
    with pytest.raises(KomariRpcError, match="RPC request failed") as error:
        await generic.async_ping()
    assert "Temporary failure" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"jsonrpc": "1.0", "id": 1, "result": "pong"},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "result": "pong", "error": {}},
        {"jsonrpc": "2.0", "id": 1, "error": "invalid"},
    ],
)
async def test_invalid_rpc_envelopes(payload: object) -> None:
    """Malformed JSON-RPC envelopes are rejected."""
    with pytest.raises(KomariProtocolError):
        await make_client(FakeSession([FakeResponse(200, payload)])).async_ping()


@pytest.mark.asyncio
async def test_invalid_ping_version_and_methods_results() -> None:
    """Built-in RPC method result types are validated."""
    with pytest.raises(KomariProtocolError, match="pong"):
        await make_client(FakeSession([rpc_result(1, "not-pong")])).async_ping()

    version_client = make_client(FakeSession([rpc_result(1, 2)]))
    with pytest.raises(KomariProtocolError, match="version"):
        await version_client.async_get_rpc_version()

    methods_client = make_client(FakeSession([rpc_result(1, ["rpc.ping", 7])]))
    with pytest.raises(KomariProtocolError, match="methods"):
        await methods_client.async_get_methods()


@pytest.mark.asyncio
async def test_bootstrap_selects_public_methods() -> None:
    """Anonymous bootstrap uses public metadata and node methods."""
    methods = [
        METHOD_PUBLIC_SETTINGS,
        METHOD_PUBLIC_VERSION,
        METHOD_PUBLIC_NODES,
        METHOD_LATEST_STATUS,
    ]
    session = FakeSession(
        [],
        rpc_results={
            "rpc.ping": "pong",
            "rpc.version": "2.0",
            "rpc.methods": methods,
            METHOD_PUBLIC_SETTINGS: {"sitename": "Example", "private_site": False},
            METHOD_PUBLIC_VERSION: {"version": "1.4.3", "hash": "abcdef"},
            METHOD_PUBLIC_NODES: load_fixture("nodes_array.json"),
            METHOD_LATEST_STATUS: load_fixture("latest_status.json"),
        },
    )

    bootstrap = await make_client(session).async_get_bootstrap()

    assert bootstrap.panel.name == "Example"
    assert bootstrap.panel.version == "1.4.3"
    assert bootstrap.panel.rpc_version == "2.0"
    assert bootstrap.nodes[NODE_UUID].name == "Example node"
    assert bootstrap.statuses[NODE_UUID].online is True
    requested_methods = {request["json"]["method"] for request in session.requests}
    assert METHOD_PUBLIC_NODES in requested_methods


@pytest.mark.asyncio
async def test_authenticated_bootstrap_uses_common_fallbacks() -> None:
    """Authenticated bootstrap prefers complete nodes and valid fallback metadata."""
    methods = [
        METHOD_COMMON_PUBLIC_INFO,
        METHOD_COMMON_VERSION,
        METHOD_COMMON_NODES,
        METHOD_LATEST_STATUS,
    ]
    session = FakeSession(
        [],
        rpc_results={
            "rpc.ping": "pong",
            "rpc.version": None,
            "rpc.methods": methods,
            METHOD_COMMON_PUBLIC_INFO: {"sitename": "Private", "private_site": True},
            METHOD_COMMON_VERSION: {"version": "1.4.3", "hash": "abcdef"},
            METHOD_COMMON_NODES: load_fixture("nodes_map.json"),
            METHOD_LATEST_STATUS: load_fixture("latest_status.json"),
        },
    )

    bootstrap = await make_client(session, "key").async_get_bootstrap()

    assert bootstrap.panel.private is True
    assert bootstrap.panel.rpc_version is None
    requested_methods = {request["json"]["method"] for request in session.requests}
    assert METHOD_COMMON_NODES in requested_methods


@pytest.mark.asyncio
async def test_node_list_capability_is_required() -> None:
    """A panel without any documented node-list method is unsupported."""
    capabilities = frozenset({METHOD_LATEST_STATUS})
    with pytest.raises(KomariUnsupportedError, match="node-list"):
        await make_client(FakeSession([])).async_get_nodes(capabilities)


@pytest.mark.asyncio
async def test_bootstrap_rejects_missing_required_method() -> None:
    """A panel without latest status support is not accepted."""
    session = FakeSession(
        [
            rpc_result(1, "pong"),
            rpc_result(2, "2.0"),
            rpc_result(3, [METHOD_PUBLIC_SETTINGS, METHOD_PUBLIC_VERSION]),
        ]
    )
    with pytest.raises(KomariUnsupportedError, match=METHOD_LATEST_STATUS):
        await make_client(session).async_get_bootstrap()


def test_node_mapping_and_array_normalize_identically() -> None:
    """Current mapping and older array shapes produce the same model."""
    mapped = parse_nodes(load_fixture("nodes_map.json"))
    array = parse_nodes(load_fixture("nodes_array.json"))
    assert mapped == array
    node = mapped[NODE_UUID]
    assert node.memory_total == 8 * 1024**3
    assert node.traffic_limit_type is TrafficLimitType.SUM
    assert node.expired_at == datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_status_values_and_zero_are_preserved() -> None:
    """Valid zero values remain available in the normalized model."""
    status = parse_statuses(load_fixture("latest_status.json"))[NODE_UUID]
    assert status.gpu_usage == 0
    assert status.swap_used == 0
    assert status.memory_used == 4 * 1024**3
    assert status.network_in_rate == 1024
    assert status.observed_at == datetime(2026, 8, 26, tzinfo=UTC)


@pytest.mark.parametrize(
    "gpu_name",
    [None, "", "none", "null", "N/A", "n-a", "unknown", "-"],
)
def test_gpu_absence_sentinels_are_normalized(gpu_name: object) -> None:
    """Common Komari no-GPU names do not become capabilities."""
    node = parse_nodes([{"uuid": NODE_UUID, "gpu_name": gpu_name}])[NODE_UUID]
    assert node.gpu_name is None


def test_meaningful_gpu_name_is_preserved() -> None:
    """Real GPU model text remains available for capability discovery."""
    node = parse_nodes([{"uuid": NODE_UUID, "gpu_name": " Example GPU "}])[NODE_UUID]
    assert node.gpu_name == "Example GPU"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-26T00:00:00Z", datetime(2026, 8, 26, tzinfo=UTC)),
        (1_777_593_600, datetime(2026, 5, 1, tzinfo=UTC)),
        (1_777_593_600_000, datetime(2026, 5, 1, tzinfo=UTC)),
        ("2026-08-26T00:00:00", None),
        ("not-a-date", None),
        (None, None),
    ],
)
def test_datetime_parsing(value: object, expected: datetime | None) -> None:
    """Only explicit aware or supported epoch timestamps are accepted."""
    assert parse_datetime(value) == expected


@pytest.mark.parametrize(
    "payload",
    [None, "nodes", ["not-an-object"], {"": {"name": "missing uuid"}}],
)
def test_invalid_node_shapes(payload: object) -> None:
    """Malformed node payloads fail explicitly."""
    with pytest.raises(KomariProtocolError):
        parse_nodes(payload)


def test_invalid_status_shape_and_values() -> None:
    """Malformed status identity/state fails and invalid metrics become None."""
    with pytest.raises(KomariProtocolError):
        parse_statuses({NODE_UUID: {"client": NODE_UUID}})

    status_payload: dict[str, object] = {
        NODE_UUID: {
            "client": NODE_UUID,
            "online": True,
            "net_in": -1,
            "cpu": float("nan"),
        }
    }
    status = parse_statuses(status_payload)[NODE_UUID]
    assert status.network_in_rate is None
    assert status.cpu_usage is None


def test_ping_task_and_stats_fixtures_are_normalized() -> None:
    """Current task IDs and per-field missing stats normalize deterministically."""
    tasks = parse_ping_tasks(load_fixture("ping_tasks.json"))
    assert tuple(tasks) == ("101", "tcp-202")
    assert tasks["101"].kind == "http"
    assert tasks["101"].clients == {
        "fixture-node-sum",
        "hidden-fixture-node",
    }

    stats = parse_ping_metric_stats(load_fixture("ping_stats.json"))
    first = next(
        value
        for key, value in stats.items()
        if key.node_uuid == "fixture-node-sum" and key.task_id == "101"
    )
    second = next(
        value
        for key, value in stats.items()
        if key.node_uuid == "fixture-node-max" and key.task_id == "tcp-202"
    )
    assert first.latest == 25
    assert first.minimum == 10
    assert first.p99 == 88
    assert second.minimum is None
    assert second.total == 120


@pytest.mark.parametrize(
    "payload",
    [None, {}, ["task"], [{"name": "missing id"}], [{"id": True}]],
)
def test_invalid_ping_task_shapes_fail_explicitly(payload: object) -> None:
    """Malformed Ping task structures never create unstable identities."""
    with pytest.raises(KomariProtocolError):
        parse_ping_tasks(payload)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"stats": ["bad"]},
        {"stats": [{"task_id": 1}]},
        {"stats": [{"entity_id": "node", "task_id": True}]},
    ],
)
def test_invalid_ping_stats_shapes_fail_explicitly(payload: object) -> None:
    """Malformed Ping series data is isolated as a protocol failure."""
    with pytest.raises(KomariProtocolError):
        parse_ping_metric_stats(payload)


async def test_client_fetches_ping_tasks_and_one_batch_of_stats() -> None:
    """The typed client uses exactly one request for all node/task combinations."""
    task_payload = load_fixture("ping_tasks.json")
    stats_payload = load_fixture("ping_stats.json")
    session = FakeSession(
        [],
        rpc_results={
            METHOD_PUBLIC_PING_TASKS: task_payload,
            METHOD_PING_METRIC_STATS: stats_payload,
        },
    )
    client = make_client(session)
    capabilities = frozenset({METHOD_PUBLIC_PING_TASKS, METHOD_PING_METRIC_STATS})

    tasks = await client.async_get_ping_tasks(capabilities)
    stats = await client.async_get_ping_stats(
        capabilities,
        ("fixture-node-sum", "fixture-node-max"),
        ("101", "tcp-202"),
        hours=1,
        max_points=120,
    )

    assert len(tasks) == 2
    assert len(stats) == 2
    assert len(session.requests) == 2
    assert session.requests[1]["json"]["params"] == {
        "entity_ids": ["fixture-node-sum", "fixture-node-max"],
        "task_ids": ["101", "tcp-202"],
        "hours": 1,
        "max_points": 120,
    }


async def test_ping_methods_require_advertised_capabilities() -> None:
    """The client never guesses optional Ping method availability."""
    client = make_client(FakeSession([]))
    with pytest.raises(KomariUnsupportedError):
        await client.async_get_ping_tasks(frozenset())
    with pytest.raises(KomariUnsupportedError):
        await client.async_get_ping_stats(
            frozenset(),
            (),
            (),
            hours=1,
            max_points=120,
        )


async def test_metadata_refresh_reprobes_methods_and_panel_versions() -> None:
    """Six-hour metadata refresh is capability-driven and internally coherent."""
    capabilities = [
        METHOD_LATEST_STATUS,
        METHOD_PUBLIC_NODES,
        METHOD_PUBLIC_SETTINGS,
        METHOD_PUBLIC_VERSION,
    ]
    session = FakeSession(
        [],
        rpc_results={
            "rpc.version": "2.0",
            "rpc.methods": capabilities,
            METHOD_PUBLIC_SETTINGS: {"sitename": "Fixture panel"},
            METHOD_PUBLIC_VERSION: {"version": "2.0.0", "hash": "fixture"},
        },
    )

    panel, discovered = await make_client(session).async_get_metadata()

    assert panel.name == "Fixture panel"
    assert panel.version == "2.0.0"
    assert discovered == frozenset(capabilities)
